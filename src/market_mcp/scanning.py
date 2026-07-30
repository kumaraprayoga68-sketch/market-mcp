"""Signal matching and universe scanning.

Extracted from the MCP tool layer so the scheduled job and the tool share one
implementation. If a signal's definition changes, both move together — a
dashboard that disagreed with the assistant about what "oversold" means would
be worse than having no dashboard.
"""

from __future__ import annotations

import asyncio
from typing import Any

from . import analysis
from .indicators import bollinger
from .market_data import load_candles
from .models import Candle

SIGNALS: dict[str, str] = {
    "oversold": "RSI below 30",
    "overbought": "RSI above 70",
    "bullish": "composite rating is buy or strong_buy",
    "bearish": "composite rating is sell or strong_sell",
    "uptrend": "price above the 200-EMA with Supertrend pointing up",
    "downtrend": "price below the 200-EMA with Supertrend pointing down",
    "volume_spike": "volume at least 2x its 20-bar average",
    "squeeze": "Bollinger width in the tightest quarter of its recent range",
}


def matches(summary: dict[str, Any], candles: list[Candle], signal: str) -> bool:
    """Does this symbol satisfy `signal`?"""
    ind = summary["indicators"]
    rsi = ind.get("rsi_14")
    label = summary["rating"]["label"]

    if signal == "oversold":
        return rsi is not None and rsi < 30
    if signal == "overbought":
        return rsi is not None and rsi > 70
    if signal == "bullish":
        return label in ("buy", "strong_buy")
    if signal == "bearish":
        return label in ("sell", "strong_sell")
    if signal == "uptrend":
        return (
            ind.get("ema_200") is not None
            and ind["price"] > ind["ema_200"]
            and ind.get("supertrend_direction") == "up"
        )
    if signal == "downtrend":
        return (
            ind.get("ema_200") is not None
            and ind["price"] < ind["ema_200"]
            and ind.get("supertrend_direction") == "down"
        )
    if signal == "volume_spike":
        return (ind.get("volume_vs_20bar_avg") or 0) >= 2.0
    if signal == "squeeze":
        return in_squeeze(ind, candles)
    raise ValueError(f"signal must be one of {sorted(SIGNALS)}")


def in_squeeze(indicators: dict[str, Any], candles: list[Candle]) -> bool:
    """Bollinger width in the tightest quarter of the last 100 bars.

    Absolute width is meaningless across instruments, so it is normalised by
    price and compared against the symbol's own recent history.
    """
    upper, lower, price = (
        indicators.get("bb_upper"),
        indicators.get("bb_lower"),
        indicators.get("price"),
    )
    if not upper or not lower or not price:
        return False
    width_now = (upper - lower) / price

    closes = [c["close"] for c in candles]
    up, _mid, low = bollinger(closes, 20, 2.0)
    widths = [
        (u - l) / c
        for u, l, c in zip(up[-100:], low[-100:], closes[-100:])
        if u is not None and l is not None and c
    ]
    if len(widths) < 20:
        return False
    return width_now <= sorted(widths)[len(widths) // 4]


def row(summary: dict[str, Any]) -> dict[str, Any]:
    """The per-symbol payload shared by the tool and the dashboard."""
    ind = summary["indicators"]
    rating = summary["rating"]
    return {
        "symbol": summary["symbol"],
        "price": ind["price"],
        "rsi_14": ind.get("rsi_14"),
        "rating": rating["label"],
        "rating_score": rating["score"],
        "trend_strength": rating["trend_strength"],
        "volume_vs_20bar_avg": ind.get("volume_vs_20bar_avg"),
        "adx_14": ind.get("adx_14"),
    }


async def evaluate_symbols(
    symbols: list[str],
    market: str,
    interval: str = "1d",
    *,
    bars: int = 260,
    concurrency: int = 6,
) -> tuple[dict[str, tuple[dict[str, Any], list[Candle]]], list[dict[str, str]]]:
    """Download and analyse every symbol once.

    Returns ({symbol: (summary, candles)}, skipped). Analysing once and testing
    many signals against the result is what makes a multi-signal scan cost the
    same as a single-signal one.
    """
    sem = asyncio.Semaphore(concurrency)
    analysed: dict[str, tuple[dict[str, Any], list[Candle]]] = {}
    skipped: list[dict[str, str]] = []

    async def one(sym: str) -> None:
        async with sem:
            try:
                candles, meta = await load_candles(sym, market, interval, bars)
                summary = analysis.summarize(candles, symbol=meta["symbol"], interval=interval)
            except Exception as e:  # noqa: BLE001 - a dead ticker must not stop the scan
                skipped.append({"symbol": sym, "reason": str(e)[:120]})
                return
        analysed[sym] = (summary, candles)

    await asyncio.gather(*(one(s) for s in symbols))
    return analysed, skipped


async def scan(
    symbols: list[str],
    market: str,
    signal: str,
    interval: str = "1d",
    *,
    concurrency: int = 6,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Scan `symbols` for one signal. Returns (matching rows, skipped)."""
    if signal not in SIGNALS:
        raise ValueError(f"signal must be one of {sorted(SIGNALS)}")

    analysed, skipped = await evaluate_symbols(symbols, market, interval, concurrency=concurrency)
    rows = [
        row(summary)
        for summary, candles in analysed.values()
        if matches(summary, candles, signal)
    ]
    rows.sort(key=lambda r: abs(r["rating_score"]), reverse=True)
    return rows, skipped
