"""Technical summary and composite rating.

Turns raw candles into the indicator readings plus a single rating. The rating
is a plain vote count across independent signals, deliberately not a black box:
every contribution is returned alongside it so the reasoning can be inspected
and disagreed with.

ADX only scales the result — it never votes. Trend strength says how much to
trust a directional signal, not which direction it points.
"""

from __future__ import annotations

from typing import Any

from . import indicators as ta
from .models import Candle, iso, ohlcv, series

# Bounded oscillators: they pin at their extremes and stay there while a trend
# runs, so their "reversal" reading is unreliable in exactly those conditions.
OSCILLATOR_VOTES = frozenset({"rsi", "stochastic", "bollinger"})
# Votes that describe direction rather than stretch.
TREND_VOTES = frozenset({"ema_stack", "long_trend", "supertrend"})

RATING_LABELS = [
    (-1.0, "strong_sell"),
    (-0.5, "sell"),
    (-0.15, "weak_sell"),
    (0.15, "neutral"),
    (0.5, "weak_buy"),
    (1.0, "buy"),
]


def summarize(candles: list[Candle], *, symbol: str = "", interval: str = "") -> dict[str, Any]:
    """Full indicator snapshot for the most recent bar."""
    if len(candles) < 30:
        raise ValueError(f"need at least 30 candles for analysis, got {len(candles)}")

    highs, lows, closes, volumes = ohlcv(candles)
    opens = series(candles, "open")
    last = closes[-1]

    rsi_v = ta.rsi(closes, 14)
    macd_line, macd_sig, macd_hist = ta.macd(closes)
    bb_up, bb_mid, bb_low = ta.bollinger(closes, 20, 2.0)
    pct_b = ta.percent_b(closes, 20, 2.0)
    ema20, ema50, ema200 = ta.ema(closes, 20), ta.ema(closes, 50), ta.ema(closes, 200)
    atr_v = ta.atr(highs, lows, closes, 14)
    adx_v, plus_di, minus_di = ta.adx(highs, lows, closes, 14)
    st_line, st_dir = ta.supertrend(highs, lows, closes, 10, 3.0)
    stoch_k, stoch_d = ta.stochastic(highs, lows, closes, 14, 3)

    vol_avg20 = ta.sma(volumes, 20)
    vol_ratio = None
    if vol_avg20[-1]:
        vol_ratio = volumes[-1] / vol_avg20[-1]

    indicators = {
        "price": last,
        "open": opens[-1],
        "high": highs[-1],
        "low": lows[-1],
        "volume": volumes[-1],
        "volume_vs_20bar_avg": _r(vol_ratio, 2),
        "rsi_14": _r(rsi_v[-1]),
        "macd": _r(macd_line[-1]),
        "macd_signal": _r(macd_sig[-1]),
        "macd_histogram": _r(macd_hist[-1]),
        "bb_upper": _r(bb_up[-1]),
        "bb_middle": _r(bb_mid[-1]),
        "bb_lower": _r(bb_low[-1]),
        "bb_percent_b": _r(pct_b[-1], 3),
        "ema_20": _r(ema20[-1]),
        "ema_50": _r(ema50[-1]),
        "ema_200": _r(ema200[-1]),
        "atr_14": _r(atr_v[-1]),
        "atr_pct_of_price": _r(atr_v[-1] / last * 100, 2) if atr_v[-1] and last else None,
        "adx_14": _r(adx_v[-1], 1),
        "plus_di": _r(plus_di[-1], 1),
        "minus_di": _r(minus_di[-1], 1),
        "supertrend": _r(st_line[-1]),
        "supertrend_direction": "up" if st_dir[-1] == 1 else ("down" if st_dir[-1] == -1 else None),
        "stochastic_k": _r(stoch_k[-1], 1),
        "stochastic_d": _r(stoch_d[-1], 1),
    }

    votes = _votes(
        last=last,
        rsi=rsi_v[-1],
        macd_hist=macd_hist[-1],
        pct_b=pct_b[-1],
        ema20=ema20[-1],
        ema50=ema50[-1],
        ema200=ema200[-1],
        st_dir=st_dir[-1],
        stoch_k=stoch_k[-1],
    )

    trend_strength = _trend_strength(adx_v[-1])
    _suppress_countertrend_oscillators(votes, trend_strength)

    raw = sum(v["score"] for v in votes)
    normalized = raw / len(votes) if votes else 0.0

    # A weak-trend reading is a reason to discount a directional call, not to
    # flip it, so it damps the magnitude only.
    damped = normalized * (0.6 if trend_strength == "weak" else 1.0)

    return {
        "symbol": symbol,
        "interval": interval,
        "as_of": iso(candles[-1]["ts"]),
        "bars_analyzed": len(candles),
        "indicators": indicators,
        "rating": {
            "score": round(damped, 3),
            "label": _label(damped),
            "trend_strength": trend_strength,
            "bullish_votes": sum(1 for v in votes if v["score"] > 0),
            "bearish_votes": sum(1 for v in votes if v["score"] < 0),
            "neutral_votes": sum(1 for v in votes if v["score"] == 0),
            "votes": votes,
        },
        "levels": {
            "resistance_20bar": max(highs[-20:]),
            "support_20bar": min(lows[-20:]),
            "distance_to_resistance_pct": _r((max(highs[-20:]) / last - 1) * 100, 2),
            "distance_to_support_pct": _r((1 - min(lows[-20:]) / last) * 100, 2),
        },
    }


def _votes(
    *,
    last: float,
    rsi: float | None,
    macd_hist: float | None,
    pct_b: float | None,
    ema20: float | None,
    ema50: float | None,
    ema200: float | None,
    st_dir: int | None,
    stoch_k: float | None,
) -> list[dict[str, Any]]:
    votes: list[dict[str, Any]] = []

    def add(name: str, score: int, reason: str) -> None:
        votes.append({"indicator": name, "score": score, "reason": reason})

    if rsi is not None:
        if rsi < 30:
            add("rsi", 1, f"RSI {rsi:.1f} is oversold")
        elif rsi > 70:
            add("rsi", -1, f"RSI {rsi:.1f} is overbought")
        else:
            add("rsi", 0, f"RSI {rsi:.1f} is mid-range")

    if macd_hist is not None:
        if macd_hist > 0:
            add("macd", 1, "MACD is above its signal line")
        elif macd_hist < 0:
            add("macd", -1, "MACD is below its signal line")
        else:
            add("macd", 0, "MACD sits on its signal line")

    if pct_b is not None:
        if pct_b < 0.1:
            add("bollinger", 1, "price is at or below the lower band")
        elif pct_b > 0.9:
            add("bollinger", -1, "price is at or above the upper band")
        else:
            add("bollinger", 0, "price is inside the bands")

    if ema20 is not None and ema50 is not None:
        if last > ema20 > ema50:
            add("ema_stack", 1, "price above EMA20 above EMA50")
        elif last < ema20 < ema50:
            add("ema_stack", -1, "price below EMA20 below EMA50")
        else:
            add("ema_stack", 0, "EMAs are not stacked in either direction")

    if ema200 is not None:
        if last > ema200:
            add("long_trend", 1, "price is above the 200-period EMA")
        else:
            add("long_trend", -1, "price is below the 200-period EMA")

    if st_dir is not None:
        add("supertrend", 1 if st_dir == 1 else -1,
            f"Supertrend is pointing {'up' if st_dir == 1 else 'down'}")

    if stoch_k is not None:
        if stoch_k < 20:
            add("stochastic", 1, f"stochastic %K {stoch_k:.0f} is oversold")
        elif stoch_k > 80:
            add("stochastic", -1, f"stochastic %K {stoch_k:.0f} is overbought")
        else:
            add("stochastic", 0, f"stochastic %K {stoch_k:.0f} is mid-range")

    return votes


def _suppress_countertrend_oscillators(votes: list[dict[str, Any]], trend_strength: str) -> None:
    """Neutralise oscillator votes that fight a strong trend, in place.

    RSI pins near 100 for the whole of a real rally. Counting that as bearish
    lets the oscillators cancel the trend indicators and rate an unmistakable
    uptrend "neutral" — the single most common way a vote-counting rating goes
    wrong. In a strong trend an extreme reading means persistence, so it is
    dropped rather than allowed to vote against the direction.
    """
    if trend_strength != "strong":
        return

    consensus = sum(v["score"] for v in votes if v["indicator"] in TREND_VOTES)
    if consensus == 0:
        return
    direction = 1 if consensus > 0 else -1

    for v in votes:
        if v["indicator"] in OSCILLATOR_VOTES and v["score"] == -direction:
            v["score"] = 0
            v["reason"] += (
                " — not counted: in a strong trend an extreme reading signals "
                "persistence, not reversal"
            )


def _trend_strength(adx: float | None) -> str:
    if adx is None:
        return "unknown"
    if adx < 20:
        return "weak"
    if adx < 40:
        return "moderate"
    return "strong"


def _label(score: float) -> str:
    for threshold, label in RATING_LABELS:
        if score < threshold:
            return label
    return "strong_buy"


def _r(v: float | None, digits: int = 6) -> float | None:
    return None if v is None else round(float(v), digits)
