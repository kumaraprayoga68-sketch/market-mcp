"""Yahoo Finance provider — quotes, OHLCV and symbol search.

Uses the public `chart` endpoint for both prices and candles. The older
`v7/finance/quote` route now demands a crumb/cookie handshake, while `chart`
returns everything we need (price, previous close, 52-week range, currency,
exchange) in its `meta` block with no auth at all.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any

from ..core.cache import ttl_cache
from ..core.errors import ToolError
from ..core.http import fetch_json
from ..models import Candle, iso

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"

VALID_INTERVALS = {
    "1m", "2m", "5m", "15m", "30m", "60m", "1h", "1d", "5d", "1wk", "1mo", "3mo",
}
VALID_RANGES = {
    "1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max",
}

# Index/FX/crypto tickers used by the market snapshot tool.
SNAPSHOT_SYMBOLS: dict[str, list[tuple[str, str]]] = {
    "us_equity": [("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq"), ("^DJI", "Dow Jones")],
    "volatility": [("^VIX", "VIX")],
    "asia": [("^JKSE", "IDX Composite"), ("^N225", "Nikkei 225"), ("^HSI", "Hang Seng")],
    "crypto": [("BTC-USD", "Bitcoin"), ("ETH-USD", "Ethereum")],
    "fx_commodity": [("USDIDR=X", "USD/IDR"), ("GC=F", "Gold"), ("CL=F", "Crude Oil")],
}


@ttl_cache(seconds=20)
async def _chart(symbol: str, interval: str, range_: str) -> dict[str, Any]:
    payload = await fetch_json(
        CHART_URL.format(symbol=symbol),
        params={"interval": interval, "range": range_, "includePrePost": "false"},
        source="Yahoo Finance",
    )
    chart = (payload or {}).get("chart") or {}
    if chart.get("error"):
        desc = chart["error"].get("description") or chart["error"].get("code")
        raise ToolError("not_found", f"Yahoo Finance: {desc} (symbol '{symbol}')")
    results = chart.get("result") or []
    if not results:
        raise ToolError("not_found", f"Yahoo Finance has no data for '{symbol}'")
    return results[0]


def _validate(interval: str, range_: str) -> None:
    if interval not in VALID_INTERVALS:
        raise ValueError(
            f"interval '{interval}' is not supported; use one of {sorted(VALID_INTERVALS)}"
        )
    if range_ not in VALID_RANGES:
        raise ValueError(
            f"range '{range_}' is not supported; use one of {sorted(VALID_RANGES)}"
        )


async def quote(symbol: str) -> dict[str, Any]:
    """Latest price plus the context needed to judge it."""
    # A 5d window, not a longer one: `meta.chartPreviousClose` is the close
    # *before the requested range*, so asking for 1mo would report the monthly
    # change as if it were today's move.
    result = await _chart(symbol.strip().upper(), "1d", "5d")
    meta = result.get("meta") or {}

    price = meta.get("regularMarketPrice")
    prev = _previous_close(result, price) or meta.get("chartPreviousClose")
    change = change_pct = None
    if price is not None and prev:
        change = price - prev
        change_pct = change / prev * 100

    hi52 = meta.get("fiftyTwoWeekHigh")
    lo52 = meta.get("fiftyTwoWeekLow")
    pos52 = None
    if price is not None and hi52 and lo52 and hi52 > lo52:
        pos52 = (price - lo52) / (hi52 - lo52) * 100

    return {
        "symbol": meta.get("symbol", symbol.upper()),
        "name": meta.get("longName") or meta.get("shortName"),
        "price": price,
        "previous_close": prev,
        "change": change,
        "change_pct": change_pct,
        "currency": meta.get("currency"),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "market_state": meta.get("marketState"),
        "day_high": meta.get("regularMarketDayHigh"),
        "day_low": meta.get("regularMarketDayLow"),
        "volume": meta.get("regularMarketVolume"),
        "fifty_two_week_high": hi52,
        "fifty_two_week_low": lo52,
        "position_in_52w_range_pct": pos52,
        "quote_time": iso((meta.get("regularMarketTime") or 0) * 1000) if meta.get("regularMarketTime") else None,
    }


def _previous_close(result: dict[str, Any], price: float | None) -> float | None:
    """Prior session's close, taken from the daily closes in the chart payload.

    The final bar is the current (possibly still-open) session, so the previous
    close is the bar before it — unless the feed has not printed today yet, in
    which case the final bar already *is* the previous close.
    """
    closes = [
        c
        for c in (((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
        if c is not None
    ]
    if not closes:
        return None
    if price is None:
        return float(closes[-2]) if len(closes) >= 2 else None

    # Yahoo stores closes at float32 precision but rounds `regularMarketPrice`,
    # so an exact comparison would call an identical value "different". Only a
    # relative gap means a new session that the candle array has not printed yet.
    if math.isclose(closes[-1], price, rel_tol=1e-5):
        return float(closes[-2]) if len(closes) >= 2 else None
    return float(closes[-1])


async def candles(symbol: str, interval: str = "1d", range_: str = "1y") -> list[Candle]:
    """OHLCV bars, oldest first. Bars with gaps (holidays, halts) are dropped."""
    _validate(interval, range_)
    result = await _chart(symbol.strip().upper(), interval, range_)

    timestamps = result.get("timestamp") or []
    quotes = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quotes.get("open") or []
    highs = quotes.get("high") or []
    lows = quotes.get("low") or []
    closes = quotes.get("close") or []
    volumes = quotes.get("volume") or []

    out: list[Candle] = []
    for i, ts in enumerate(timestamps):
        try:
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        except IndexError:
            break
        if None in (o, h, l, c):
            continue
        v = volumes[i] if i < len(volumes) and volumes[i] is not None else 0
        out.append(
            {"ts": int(ts) * 1000, "open": float(o), "high": float(h),
             "low": float(l), "close": float(c), "volume": float(v)}
        )
    if not out:
        raise ToolError("not_found", f"Yahoo Finance returned no candles for '{symbol}'")
    return out


async def quotes_bulk(symbols: list[str], concurrency: int = 8) -> list[dict[str, Any]]:
    """Fetch many quotes concurrently, skipping symbols that fail.

    Screeners routinely ask for a few hundred tickers and a handful will always
    be delisted or suspended; one bad symbol must not sink the whole scan.
    """
    sem = asyncio.Semaphore(concurrency)

    async def one(sym: str) -> dict[str, Any] | None:
        async with sem:
            try:
                return await quote(sym)
            except Exception:  # noqa: BLE001 - partial results beat no results
                return None

    results = await asyncio.gather(*(one(s) for s in symbols))
    return [r for r in results if r is not None and r.get("price") is not None]


async def search(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Resolve a company or ticker name to Yahoo symbols."""
    payload = await fetch_json(
        SEARCH_URL,
        params={"q": query, "quotesCount": limit, "newsCount": 0},
        source="Yahoo Finance search",
    )
    out = []
    for q in (payload or {}).get("quotes", [])[:limit]:
        out.append(
            {
                "symbol": q.get("symbol"),
                "name": q.get("longname") or q.get("shortname"),
                "type": q.get("quoteType"),
                "exchange": q.get("exchDisp") or q.get("exchange"),
            }
        )
    return out
