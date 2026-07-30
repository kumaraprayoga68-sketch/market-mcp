"""One candle loader across every venue.

Tools ask for `(symbol, market, interval, bars)` and get bars back. The
per-provider quirks — Binance's `limit` versus Yahoo's coarse `range` buckets,
and Yahoo's tighter history caps on intraday data — are resolved here so no
tool has to care.
"""

from __future__ import annotations

from typing import Any

from .core.errors import ToolError
from .models import Candle, bars_per_year
from .providers import binance, idx, yahoo

MARKETS = ("crypto", "stock", "idx")

# Canonical interval -> provider interval.
BINANCE_INTERVAL = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h",
    "2h": "2h", "4h": "4h", "6h": "6h", "12h": "12h", "1d": "1d",
    "3d": "3d", "1w": "1w",
}
YAHOO_INTERVAL = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "60m",
    "1d": "1d", "1w": "1wk", "1mo": "1mo",
}

# Smallest Yahoo `range` that yields at least N bars, per interval. Yahoo caps
# intraday history hard (7 days of 1m, 60 days of sub-hourly), so the tables for
# those intervals stop early rather than requesting a range that 422s.
YAHOO_RANGES: dict[str, list[tuple[str, int]]] = {
    "1m": [("1d", 390), ("5d", 1950)],
    "5m": [("1d", 78), ("5d", 390), ("1mo", 1600)],
    "15m": [("5d", 130), ("1mo", 520)],
    "30m": [("5d", 65), ("1mo", 260)],
    "60m": [("1mo", 150), ("3mo", 450), ("6mo", 900), ("1y", 1750), ("2y", 3400)],
    "1d": [("1mo", 21), ("3mo", 63), ("6mo", 126), ("1y", 252), ("2y", 504),
           ("5y", 1260), ("10y", 2520), ("max", 10**9)],
    "1wk": [("1y", 52), ("2y", 104), ("5y", 260), ("10y", 520), ("max", 10**9)],
    "1mo": [("5y", 60), ("10y", 120), ("max", 10**9)],
}


def normalize_market(market: str) -> str:
    m = (market or "").strip().lower()
    aliases = {
        "crypto": "crypto", "binance": "crypto", "coin": "crypto",
        "stock": "stock", "stocks": "stock", "equity": "stock", "us": "stock",
        "idx": "idx", "indonesia": "idx", "id": "idx", "jk": "idx",
    }
    if m not in aliases:
        raise ValueError(f"market must be one of {list(MARKETS)}, got '{market}'")
    return aliases[m]


def _yahoo_range(interval: str, bars: int) -> str:
    table = YAHOO_RANGES.get(interval)
    if table is None:
        raise ValueError(f"interval '{interval}' is not available for equities")
    for range_, capacity in table:
        if capacity >= bars:
            return range_
    return table[-1][0]


async def load_candles(
    symbol: str, market: str, interval: str = "1d", bars: int = 500
) -> tuple[list[Candle], dict[str, Any]]:
    """Fetch up to `bars` most recent candles. Returns (candles, meta)."""
    market = normalize_market(market)
    bars = max(30, min(int(bars), 1500))
    interval = (interval or "1d").strip().lower()

    if market == "crypto":
        if interval not in BINANCE_INTERVAL:
            raise ValueError(
                f"interval '{interval}' is not available for crypto; "
                f"use one of {sorted(BINANCE_INTERVAL)}"
            )
        resolved = binance.normalize_symbol(symbol)
        candles = await binance.candles(resolved, BINANCE_INTERVAL[interval], min(bars, 1000))
        asset_class = "crypto"
    else:
        if interval not in YAHOO_INTERVAL:
            raise ValueError(
                f"interval '{interval}' is not available for equities; "
                f"use one of {sorted(YAHOO_INTERVAL)}"
            )
        y_interval = YAHOO_INTERVAL[interval]
        resolved = idx.normalize_ticker(symbol) if market == "idx" else symbol.strip().upper()
        candles = await yahoo.candles(resolved, y_interval, _yahoo_range(y_interval, bars))
        asset_class = "equity"

    if len(candles) > bars:
        candles = candles[-bars:]
    if len(candles) < 30:
        raise ToolError(
            "not_found",
            f"only {len(candles)} candles available for '{resolved}' at {interval}; "
            "try a longer interval",
        )

    return candles, {
        "symbol": resolved,
        "market": market,
        "interval": interval,
        "asset_class": asset_class,
        "bars": len(candles),
        "bars_per_year": bars_per_year(asset_class, interval),
    }


async def load_quote(symbol: str, market: str) -> dict[str, Any]:
    """Latest price for any venue."""
    market = normalize_market(market)
    if market == "crypto":
        return await binance.price(symbol)
    if market == "idx":
        return await idx.quote(symbol)
    return await yahoo.quote(symbol)
