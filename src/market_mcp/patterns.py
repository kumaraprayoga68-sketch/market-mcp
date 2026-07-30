"""Candlestick pattern detection.

Patterns are scored relative to each candle's own range rather than against
absolute thresholds, so the same rules work on a 90,000-dollar BTC bar and a
1,200-rupiah IDX bar.

A pattern is context, not a signal: `bullish`/`bearish` here describes the
shape only. Whether it means anything depends on where in the trend it appears,
which is why every detection carries the trend state it fired in.
"""

from __future__ import annotations

from typing import Any

from .indicators import ema
from .models import Candle, iso, series


def _body(c: Candle) -> float:
    return abs(c["close"] - c["open"])


def _range(c: Candle) -> float:
    return max(c["high"] - c["low"], 1e-12)


def _upper_wick(c: Candle) -> float:
    return c["high"] - max(c["open"], c["close"])


def _lower_wick(c: Candle) -> float:
    return min(c["open"], c["close"]) - c["low"]


def _bullish(c: Candle) -> bool:
    return c["close"] > c["open"]


def detect(candles: list[Candle], lookback: int = 5) -> list[dict[str, Any]]:
    """Patterns found in the last `lookback` bars, newest first."""
    if len(candles) < 5:
        return []

    closes = series(candles, "close")
    trend_ema = ema(closes, min(20, len(closes) - 1))

    found: list[dict[str, Any]] = []
    start = max(2, len(candles) - lookback)

    for i in range(start, len(candles)):
        c, p = candles[i], candles[i - 1]
        rng, body = _range(c), _body(c)
        body_pct = body / rng

        trend = "unknown"
        if trend_ema[i] is not None:
            trend = "up" if closes[i] > trend_ema[i] else "down"

        hits: list[tuple[str, str, str]] = []  # (name, bias, note)

        # --- single-candle shapes ---
        if body_pct < 0.1:
            hits.append(("doji", "neutral", "open and close nearly equal — indecision"))
        elif body_pct > 0.9:
            hits.append(
                ("marubozu", "bullish" if _bullish(c) else "bearish",
                 "full-body candle with almost no wick — one side in control")
            )

        if body_pct < 0.4 and _lower_wick(c) > 2 * body and _upper_wick(c) < body:
            hits.append(
                ("hammer" if trend == "down" else "hanging_man",
                 "bullish" if trend == "down" else "bearish",
                 "long lower wick — sellers pushed down and were rejected")
            )
        if body_pct < 0.4 and _upper_wick(c) > 2 * body and _lower_wick(c) < body:
            hits.append(
                ("inverted_hammer" if trend == "down" else "shooting_star",
                 "bullish" if trend == "down" else "bearish",
                 "long upper wick — buyers pushed up and were rejected")
            )

        # --- two-candle shapes ---
        if _bullish(c) and not _bullish(p) and c["close"] >= p["open"] and c["open"] <= p["close"]:
            hits.append(("bullish_engulfing", "bullish", "this body swallows the prior down candle"))
        if not _bullish(c) and _bullish(p) and c["close"] <= p["open"] and c["open"] >= p["close"]:
            hits.append(("bearish_engulfing", "bearish", "this body swallows the prior up candle"))

        if _body(p) > 0 and body < _body(p) * 0.6:
            inside = max(c["open"], c["close"]) < max(p["open"], p["close"]) and min(
                c["open"], c["close"]
            ) > min(p["open"], p["close"])
            if inside:
                hits.append(
                    ("bullish_harami" if not _bullish(p) else "bearish_harami",
                     "bullish" if not _bullish(p) else "bearish",
                     "small body contained by the previous candle — momentum stalling")
                )

        # --- three-candle shapes ---
        if i >= 2:
            a = candles[i - 2]
            small_middle = _body(p) < _body(a) * 0.5 and _body(p) < body * 0.5
            if small_middle and not _bullish(a) and _bullish(c) and c["close"] > (a["open"] + a["close"]) / 2:
                hits.append(("morning_star", "bullish", "down bar, pause, strong up bar — reversal off a low"))
            if small_middle and _bullish(a) and not _bullish(c) and c["close"] < (a["open"] + a["close"]) / 2:
                hits.append(("evening_star", "bearish", "up bar, pause, strong down bar — reversal off a high"))

            if all(_bullish(x) for x in (a, p, c)) and c["close"] > p["close"] > a["close"]:
                hits.append(("three_white_soldiers", "bullish", "three rising bodies in a row"))
            if all(not _bullish(x) for x in (a, p, c)) and c["close"] < p["close"] < a["close"]:
                hits.append(("three_black_crows", "bearish", "three falling bodies in a row"))

        for name, bias, note in hits:
            found.append(
                {
                    "pattern": name,
                    "bias": bias,
                    "note": note,
                    "time": iso(c["ts"]),
                    "bars_ago": len(candles) - 1 - i,
                    "prevailing_trend": trend,
                    # A reversal shape only reverses something if the trend
                    # actually ran the other way first.
                    "confirms_reversal": (bias == "bullish" and trend == "down")
                    or (bias == "bearish" and trend == "up"),
                }
            )

    found.sort(key=lambda p: p["bars_ago"])
    return found
