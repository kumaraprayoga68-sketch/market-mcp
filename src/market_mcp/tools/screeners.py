"""Screening tools: scan many symbols and return the ones that qualify."""

from __future__ import annotations

import asyncio
from typing import Any

from .. import analysis
from ..core.errors import envelope
from ..market_data import load_candles
from ..models import clean
from ..providers import binance, idx

SIGNALS = {
    "oversold": "RSI below 30",
    "overbought": "RSI above 70",
    "bullish": "composite rating is buy or strong_buy",
    "bearish": "composite rating is sell or strong_sell",
    "uptrend": "price above the 200-EMA with Supertrend pointing up",
    "downtrend": "price below the 200-EMA with Supertrend pointing down",
    "volume_spike": "volume at least 2x its 20-bar average",
    "squeeze": "Bollinger width in the tightest quarter of its recent range",
}

# Technical scans need one full candle request per symbol, so the cap keeps a
# scan inside a reasonable response time rather than timing out the client.
MAX_TECHNICAL_SCAN = 60


def register(server: Any) -> None:
    @server.tool()
    @envelope
    async def crypto_screener(
        quote_asset: str = "USDT",
        min_volume_usd: float = 10_000_000,
        min_change_pct: float | None = None,
        max_change_pct: float | None = None,
        sort_by: str = "volume",
        limit: int = 25,
    ) -> dict[str, Any]:
        """Screen Binance spot pairs on 24h price and volume.

        Args:
            quote_asset: Quote leg to screen against (USDT, USDC, BTC...).
            min_volume_usd: Minimum 24h turnover. Raising this is the main lever
                for filtering out pairs too illiquid to trade.
            min_change_pct: Keep only pairs up at least this much over 24h.
            max_change_pct: Keep only pairs below this 24h change.
            sort_by: "volume", "change" or "change_asc".
            limit: Rows to return.
        """
        rows = await binance.universe(quote_asset, min_volume_usd)

        if min_change_pct is not None:
            rows = [r for r in rows if (r["change_pct"] or 0) >= min_change_pct]
        if max_change_pct is not None:
            rows = [r for r in rows if (r["change_pct"] or 0) <= max_change_pct]

        if sort_by == "change":
            rows.sort(key=lambda r: r["change_pct"] or -1e9, reverse=True)
        elif sort_by == "change_asc":
            rows.sort(key=lambda r: r["change_pct"] or 1e9)
        elif sort_by == "volume":
            rows.sort(key=lambda r: r["volume_quote_24h"] or 0, reverse=True)
        else:
            raise ValueError("sort_by must be 'volume', 'change' or 'change_asc'")

        limit = max(1, min(int(limit), 100))
        return clean(
            {
                "quote_asset": quote_asset.upper(),
                "matched": len(rows),
                "returned": min(limit, len(rows)),
                "filters": {
                    "min_volume_usd": min_volume_usd,
                    "min_change_pct": min_change_pct,
                    "max_change_pct": max_change_pct,
                },
                "results": rows[:limit],
            }
        )

    @server.tool()
    @envelope
    async def crypto_top_movers(
        quote_asset: str = "USDT",
        direction: str = "gainers",
        min_volume_usd: float = 10_000_000,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Get the biggest 24h movers on Binance spot.

        Args:
            quote_asset: Quote leg (USDT, USDC...).
            direction: "gainers", "losers" or "both".
            min_volume_usd: Liquidity floor — without one the list fills with
                thin pairs whose percentage moves are noise.
            limit: Rows per direction.
        """
        if direction not in ("gainers", "losers", "both"):
            raise ValueError("direction must be 'gainers', 'losers' or 'both'")

        rows = await binance.universe(quote_asset, min_volume_usd)
        rows = [r for r in rows if r["change_pct"] is not None]
        rows.sort(key=lambda r: r["change_pct"], reverse=True)
        limit = max(1, min(int(limit), 50))

        out: dict[str, Any] = {"quote_asset": quote_asset.upper(), "universe_size": len(rows)}
        if direction in ("gainers", "both"):
            out["gainers"] = rows[:limit]
        if direction in ("losers", "both"):
            out["losers"] = list(reversed(rows[-limit:]))
        return clean(out)

    @server.tool()
    @envelope
    async def stock_screener(
        universe: str = "idx",
        min_change_pct: float | None = None,
        max_change_pct: float | None = None,
        min_price: float = 0,
        max_symbols: int = 100,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Screen a stock universe on today's move.

        Args:
            universe: "idx" (~277 Indonesian tickers) or "us" (~117 US tickers).
            min_change_pct: Keep only names up at least this much today.
            max_change_pct: Keep only names below this change today.
            min_price: Minimum share price, in the listing currency (IDR for IDX).
            max_symbols: How many tickers to quote. The universe files are
                ordered by liquidity, so the first N are the tradeable ones.
            limit: Rows to return.
        """
        rows = await idx.scan(
            universe, max_symbols=max(10, min(int(max_symbols), 300)), min_price=min_price
        )
        if min_change_pct is not None:
            rows = [r for r in rows if (r.get("change_pct") or 0) >= min_change_pct]
        if max_change_pct is not None:
            rows = [r for r in rows if (r.get("change_pct") or 0) <= max_change_pct]

        limit = max(1, min(int(limit), 100))
        trimmed = [
            {
                "symbol": r["symbol"],
                "name": r.get("name"),
                "price": r.get("price"),
                "change_pct": r.get("change_pct"),
                "volume": r.get("volume"),
                "currency": r.get("currency"),
            }
            for r in rows[:limit]
        ]
        return clean(
            {
                "universe": universe,
                "scanned": max_symbols,
                "matched": len(rows),
                "results": trimmed,
            }
        )

    @server.tool()
    @envelope
    async def technical_scan(
        signal: str,
        market: str = "crypto",
        universe: str = "idx",
        interval: str = "1d",
        max_symbols: int = 30,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Scan a universe for a technical setup, not just a price move.

        Args:
            signal: One of oversold, overbought, bullish, bearish, uptrend,
                downtrend, volume_spike, squeeze.
            market: "crypto" scans the most liquid Binance pairs; "stock"/"idx"
                scan the bundled ticker universes.
            universe: For stock/idx markets: "idx" or "us".
            interval: Candle size for the indicators.
            max_symbols: Symbols to examine (capped at 60 — each one costs a
                full candle download).
            limit: Rows to return.

        Slower than the price screeners because it downloads candles per symbol;
        prefer a tighter `max_symbols` when you only need ideas, not coverage.
        """
        if signal not in SIGNALS:
            raise ValueError(f"signal must be one of {sorted(SIGNALS)}")

        max_symbols = max(5, min(int(max_symbols), MAX_TECHNICAL_SCAN))
        if market == "crypto":
            pairs = await binance.universe("USDT", 10_000_000)
            symbols = [p["symbol"] for p in pairs[:max_symbols]]
        else:
            symbols = list(idx.load_universe(universe))[:max_symbols]

        sem = asyncio.Semaphore(6)
        skipped: list[dict[str, str]] = []

        async def evaluate(sym: str) -> dict[str, Any] | None:
            async with sem:
                try:
                    candles, meta = await load_candles(sym, market, interval, 260)
                    s = analysis.summarize(candles, symbol=meta["symbol"], interval=interval)
                except Exception as e:  # noqa: BLE001 - a dead ticker must not stop the scan
                    skipped.append({"symbol": sym, "reason": str(e)[:120]})
                    return None
            return _match(s, signal, candles)

        rows = [r for r in await asyncio.gather(*(evaluate(s) for s in symbols)) if r]
        rows.sort(key=lambda r: abs(r["rating_score"]), reverse=True)

        evaluated = len(symbols) - len(skipped)
        out = {
            "signal": signal,
            "criterion": SIGNALS[signal],
            "market": market,
            "interval": interval,
            "requested": len(symbols),
            # Reported separately so an empty result is never confused with a
            # scan that silently failed on most of its symbols.
            "evaluated": evaluated,
            "skipped": len(skipped),
            "matched": len(rows),
            "results": rows[: max(1, min(int(limit), 50))],
        }
        if skipped:
            out["skipped_symbols"] = skipped[:10]
        if not rows:
            out["note"] = (
                f"No symbol met the '{signal}' criterion ({SIGNALS[signal]}) out of "
                f"{evaluated} evaluated. This is a real result, not an error — "
                "widen max_symbols or try a different signal."
            )
        return clean(out)

    @server.tool()
    @envelope
    async def list_universes() -> dict[str, Any]:
        """List the bundled stock ticker universes and their sizes."""
        return {
            "universes": [
                {"name": name, "symbols": len(idx.load_universe(name))}
                for name in ("idx", "us")
            ],
            "note": "IDX tickers carry a .JK suffix; pass them bare (BBCA) or suffixed.",
        }


def _match(summary: dict[str, Any], signal: str, candles: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return a result row if `summary` satisfies `signal`, else None."""
    ind = summary["indicators"]
    rating = summary["rating"]
    rsi = ind.get("rsi_14")
    label = rating["label"]

    hit = False
    if signal == "oversold":
        hit = rsi is not None and rsi < 30
    elif signal == "overbought":
        hit = rsi is not None and rsi > 70
    elif signal == "bullish":
        hit = label in ("buy", "strong_buy")
    elif signal == "bearish":
        hit = label in ("sell", "strong_sell")
    elif signal == "uptrend":
        hit = (
            ind.get("ema_200") is not None
            and ind["price"] > ind["ema_200"]
            and ind.get("supertrend_direction") == "up"
        )
    elif signal == "downtrend":
        hit = (
            ind.get("ema_200") is not None
            and ind["price"] < ind["ema_200"]
            and ind.get("supertrend_direction") == "down"
        )
    elif signal == "volume_spike":
        hit = (ind.get("volume_vs_20bar_avg") or 0) >= 2.0
    elif signal == "squeeze":
        hit = _in_squeeze(ind, candles)

    if not hit:
        return None
    return {
        "symbol": summary["symbol"],
        "price": ind["price"],
        "rsi_14": rsi,
        "rating": label,
        "rating_score": rating["score"],
        "trend_strength": rating["trend_strength"],
        "volume_vs_20bar_avg": ind.get("volume_vs_20bar_avg"),
        "adx_14": ind.get("adx_14"),
    }


def _in_squeeze(ind: dict[str, Any], candles: list[dict[str, Any]]) -> bool:
    """Bollinger width in the tightest quarter of the last 100 bars.

    Absolute width is meaningless across instruments, so it is normalised by
    price and compared against the symbol's own recent history.
    """
    upper, lower, price = ind.get("bb_upper"), ind.get("bb_lower"), ind.get("price")
    if not upper or not lower or not price:
        return False
    width_now = (upper - lower) / price

    from ..indicators import bollinger  # local import keeps the module graph flat

    closes = [c["close"] for c in candles]
    up, _mid, low = bollinger(closes, 20, 2.0)
    widths = [
        (u - l) / c
        for u, l, c in zip(up[-100:], low[-100:], closes[-100:])
        if u is not None and l is not None and c
    ]
    if len(widths) < 20:
        return False
    threshold = sorted(widths)[len(widths) // 4]
    return width_now <= threshold
