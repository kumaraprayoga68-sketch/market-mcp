"""Signal generators.

Each strategy maps candles to a position series aligned 1:1 with the input:
+1 long, 0 flat, -1 short. Bars inside an indicator's warm-up window are always
0 — a strategy is never allowed to act on an undefined indicator.

Positions are stateful (hold until an exit condition fires) rather than
re-evaluated from scratch each bar, which is what makes entry/exit rules
asymmetric the way real rules are.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .. import indicators as ta
from ..models import Candle, series

SignalFn = Callable[..., list[int]]


@dataclass(frozen=True)
class Strategy:
    name: str
    description: str
    fn: SignalFn
    defaults: dict[str, Any]
    grid: dict[str, list[Any]]  # searched by walk-forward optimisation
    supports_short: bool = False


# --------------------------------------------------------------------------
# mean reversion
# --------------------------------------------------------------------------


def rsi_reversion(
    candles: list[Candle], period: int = 14, oversold: float = 30, exit_level: float = 55
) -> list[int]:
    """Long when RSI drops below `oversold`, flat once it recovers past `exit_level`."""
    r = ta.rsi(series(candles, "close"), period)
    out = [0] * len(candles)
    pos = 0
    for i in range(len(candles)):
        if r[i] is None:
            out[i] = 0
            continue
        if pos == 0 and r[i] < oversold:
            pos = 1
        elif pos == 1 and r[i] > exit_level:
            pos = 0
        out[i] = pos
    return out


def bb_reversion(
    candles: list[Candle], period: int = 20, mult: float = 2.0
) -> list[int]:
    """Long on a close below the lower Bollinger band, flat back at the mean."""
    closes = series(candles, "close")
    _upper, mid, lower = ta.bollinger(closes, period, mult)
    out = [0] * len(candles)
    pos = 0
    for i in range(len(candles)):
        if lower[i] is None or mid[i] is None:
            out[i] = 0
            continue
        if pos == 0 and closes[i] < lower[i]:
            pos = 1
        elif pos == 1 and closes[i] > mid[i]:
            pos = 0
        out[i] = pos
    return out


def rsi_pullback(
    candles: list[Candle],
    trend_period: int = 200,
    rsi_period: int = 14,
    entry: float = 40,
    exit_level: float = 65,
) -> list[int]:
    """Buy dips, but only while price is above its long trend EMA.

    The trend filter is the whole point: naked RSI dip-buying keeps averaging
    into downtrends, which is where mean reversion does its worst damage.
    """
    closes = series(candles, "close")
    trend = ta.ema(closes, trend_period)
    r = ta.rsi(closes, rsi_period)
    out = [0] * len(candles)
    pos = 0
    for i in range(len(candles)):
        if trend[i] is None or r[i] is None:
            out[i] = 0
            continue
        if pos == 0 and closes[i] > trend[i] and r[i] < entry:
            pos = 1
        elif pos == 1 and (r[i] > exit_level or closes[i] < trend[i]):
            pos = 0
        out[i] = pos
    return out


# --------------------------------------------------------------------------
# trend following
# --------------------------------------------------------------------------


def ema_cross(
    candles: list[Candle], fast: int = 20, slow: int = 50, allow_short: bool = False
) -> list[int]:
    """Classic fast/slow EMA crossover."""
    closes = series(candles, "close")
    f = ta.ema(closes, fast)
    s = ta.ema(closes, slow)
    return _from_pair(f, s, allow_short)


def macd_cross(
    candles: list[Candle],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    allow_short: bool = False,
) -> list[int]:
    """Long while the MACD line sits above its signal line."""
    line, sig, _hist = ta.macd(series(candles, "close"), fast, slow, signal)
    return _from_pair(line, sig, allow_short)


def supertrend_follow(
    candles: list[Candle], period: int = 10, mult: float = 3.0, allow_short: bool = False
) -> list[int]:
    """Follow the Supertrend direction flag."""
    highs, lows, closes = (
        series(candles, "high"),
        series(candles, "low"),
        series(candles, "close"),
    )
    _line, direction = ta.supertrend(highs, lows, closes, period, mult)
    out = [0] * len(candles)
    for i, d in enumerate(direction):
        if d is None:
            out[i] = 0
        elif d == 1:
            out[i] = 1
        else:
            out[i] = -1 if allow_short else 0
    return out


def donchian_breakout(
    candles: list[Candle], entry_period: int = 20, exit_period: int = 10, allow_short: bool = False
) -> list[int]:
    """Turtle-style channel breakout: enter on new highs, leave on the exit channel.

    Bands are read from the *previous* bar so that today's own high cannot be
    part of the level today's close is required to break.
    """
    highs, lows, closes = (
        series(candles, "high"),
        series(candles, "low"),
        series(candles, "close"),
    )
    up_entry = ta.rolling_max(highs, entry_period)
    dn_entry = ta.rolling_min(lows, entry_period)
    up_exit = ta.rolling_max(highs, exit_period)
    dn_exit = ta.rolling_min(lows, exit_period)

    out = [0] * len(candles)
    pos = 0
    for i in range(1, len(candles)):
        if up_entry[i - 1] is None or dn_entry[i - 1] is None or up_exit[i - 1] is None or dn_exit[i - 1] is None:
            out[i] = 0
            continue
        if pos <= 0 and closes[i] > up_entry[i - 1]:
            pos = 1
        elif pos >= 0 and closes[i] < dn_entry[i - 1]:
            pos = -1 if allow_short else 0
        elif pos == 1 and closes[i] < dn_exit[i - 1]:
            pos = 0
        elif pos == -1 and closes[i] > up_exit[i - 1]:
            pos = 0
        out[i] = pos
    return out


def keltner_breakout(
    candles: list[Candle], period: int = 20, mult: float = 2.0
) -> list[int]:
    """Long on a close above the upper Keltner band, out at the centre line."""
    highs, lows, closes = (
        series(candles, "high"),
        series(candles, "low"),
        series(candles, "close"),
    )
    upper, mid, _lower = ta.keltner(highs, lows, closes, period, mult)
    out = [0] * len(candles)
    pos = 0
    for i in range(len(candles)):
        if upper[i] is None or mid[i] is None:
            out[i] = 0
            continue
        if pos == 0 and closes[i] > upper[i]:
            pos = 1
        elif pos == 1 and closes[i] < mid[i]:
            pos = 0
        out[i] = pos
    return out


def triple_ema(
    candles: list[Candle], fast: int = 8, mid: int = 21, slow: int = 55
) -> list[int]:
    """Long only while all three EMAs are stacked bullishly."""
    closes = series(candles, "close")
    f, m, s = ta.ema(closes, fast), ta.ema(closes, mid), ta.ema(closes, slow)
    out = [0] * len(candles)
    for i in range(len(candles)):
        if f[i] is None or m[i] is None or s[i] is None:
            out[i] = 0
        else:
            out[i] = 1 if f[i] > m[i] > s[i] else 0
    return out


def _from_pair(
    fast: list[float | None], slow: list[float | None], allow_short: bool
) -> list[int]:
    """Position from the sign of (fast - slow), respecting warm-up."""
    out = [0] * len(fast)
    for i in range(len(fast)):
        if fast[i] is None or slow[i] is None:
            out[i] = 0
        elif fast[i] > slow[i]:
            out[i] = 1
        else:
            out[i] = -1 if allow_short else 0
    return out


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------

REGISTRY: dict[str, Strategy] = {
    s.name: s
    for s in [
        Strategy(
            name="rsi_reversion",
            description="Buy oversold RSI, exit when it recovers. Mean reversion; struggles in strong downtrends.",
            fn=rsi_reversion,
            defaults={"period": 14, "oversold": 30, "exit_level": 55},
            grid={"period": [7, 14, 21], "oversold": [25, 30, 35], "exit_level": [50, 55, 65]},
        ),
        Strategy(
            name="bb_reversion",
            description="Buy closes below the lower Bollinger band, exit at the middle band.",
            fn=bb_reversion,
            defaults={"period": 20, "mult": 2.0},
            grid={"period": [10, 20, 30], "mult": [1.5, 2.0, 2.5]},
        ),
        Strategy(
            name="rsi_pullback",
            description="Buy RSI dips only while price holds above a long trend EMA.",
            fn=rsi_pullback,
            defaults={"trend_period": 200, "rsi_period": 14, "entry": 40, "exit_level": 65},
            grid={"trend_period": [100, 200], "entry": [35, 40, 45], "exit_level": [60, 65, 70]},
        ),
        Strategy(
            name="ema_cross",
            description="Fast/slow EMA crossover. Trend following; whipsaws in ranges.",
            fn=ema_cross,
            defaults={"fast": 20, "slow": 50, "allow_short": False},
            grid={"fast": [9, 20, 30], "slow": [50, 100, 200]},
            supports_short=True,
        ),
        Strategy(
            name="macd_cross",
            description="Long while MACD is above its signal line.",
            fn=macd_cross,
            defaults={"fast": 12, "slow": 26, "signal": 9, "allow_short": False},
            grid={"fast": [8, 12], "slow": [21, 26, 34], "signal": [7, 9, 12]},
            supports_short=True,
        ),
        Strategy(
            name="supertrend",
            description="Follow the Supertrend (ATR band) direction flag.",
            fn=supertrend_follow,
            defaults={"period": 10, "mult": 3.0, "allow_short": False},
            grid={"period": [7, 10, 14], "mult": [2.0, 3.0, 4.0]},
            supports_short=True,
        ),
        Strategy(
            name="donchian_breakout",
            description="Turtle channel breakout with a shorter exit channel.",
            fn=donchian_breakout,
            defaults={"entry_period": 20, "exit_period": 10, "allow_short": False},
            grid={"entry_period": [20, 40, 55], "exit_period": [10, 20]},
            supports_short=True,
        ),
        Strategy(
            name="keltner_breakout",
            description="Long on closes above the upper Keltner band, exit at the EMA centre.",
            fn=keltner_breakout,
            defaults={"period": 20, "mult": 2.0},
            grid={"period": [10, 20, 30], "mult": [1.5, 2.0, 2.5]},
        ),
        Strategy(
            name="triple_ema",
            description="Long only while fast > mid > slow EMAs are stacked.",
            fn=triple_ema,
            defaults={"fast": 8, "mid": 21, "slow": 55},
            grid={"fast": [5, 8, 13], "mid": [21, 34], "slow": [55, 89]},
        ),
    ]
}


def get(name: str) -> Strategy:
    key = name.strip().lower()
    if key not in REGISTRY:
        raise ValueError(
            f"unknown strategy '{name}'; available: {sorted(REGISTRY)}"
        )
    return REGISTRY[key]


def resolve_params(strategy: Strategy, overrides: dict[str, Any] | None) -> dict[str, Any]:
    """Merge user overrides onto defaults, rejecting unknown keys early."""
    params = dict(strategy.defaults)
    for k, v in (overrides or {}).items():
        if k not in params:
            raise ValueError(
                f"'{k}' is not a parameter of {strategy.name}; valid: {sorted(params)}"
            )
        params[k] = v
    return params
