"""Technical indicators in pure Python.

No pandas/numpy on purpose: the whole server stays installable on any CPython
3.10+ with nothing but httpx, and the data volumes here (a few thousand bars)
are far too small for vectorisation to matter.

Every function returns a list the same length as its input, with `None` for
bars inside the warm-up window. Callers can therefore zip results straight back
onto candles without worrying about offsets.
"""

from __future__ import annotations

import math

Series = list[float]
OptSeries = list[float | None]


# --------------------------------------------------------------------------
# moving averages
# --------------------------------------------------------------------------


def sma(values: Series, period: int) -> OptSeries:
    """Simple moving average."""
    if period < 1:
        raise ValueError("period must be >= 1")
    out: OptSeries = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def ema(values: Series, period: int) -> OptSeries:
    """Exponential moving average, seeded with the first full SMA."""
    if period < 1:
        raise ValueError("period must be >= 1")
    out: OptSeries = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def wilder_smooth(values: Series, period: int) -> OptSeries:
    """Wilder's smoothing (the RMA used by RSI/ATR/ADX)."""
    out: OptSeries = [None] * len(values)
    if len(values) < period:
        return out
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


def stdev(values: Series, period: int) -> OptSeries:
    """Rolling population standard deviation (matches Bollinger convention)."""
    out: OptSeries = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        mean = sum(window) / period
        var = sum((v - mean) ** 2 for v in window) / period
        out[i] = math.sqrt(var)
    return out


def rolling_max(values: Series, period: int) -> OptSeries:
    return [
        max(values[i - period + 1 : i + 1]) if i >= period - 1 else None
        for i in range(len(values))
    ]


def rolling_min(values: Series, period: int) -> OptSeries:
    return [
        min(values[i - period + 1 : i + 1]) if i >= period - 1 else None
        for i in range(len(values))
    ]


# --------------------------------------------------------------------------
# oscillators
# --------------------------------------------------------------------------


def rsi(values: Series, period: int = 14) -> OptSeries:
    """Wilder's Relative Strength Index."""
    out: OptSeries = [None] * len(values)
    if len(values) <= period:
        return out

    gains = [0.0] * len(values)
    losses = [0.0] * len(values)
    for i in range(1, len(values)):
        delta = values[i] - values[i - 1]
        gains[i] = max(delta, 0.0)
        losses[i] = max(-delta, 0.0)

    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    out[period] = _rsi_from(avg_gain, avg_loss)

    for i in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i] = _rsi_from(avg_gain, avg_loss)
    return out


def _rsi_from(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(
    values: Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[OptSeries, OptSeries, OptSeries]:
    """Returns (macd line, signal line, histogram)."""
    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    line: OptSeries = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(fast_ema, slow_ema)
    ]

    # The signal EMA runs on the defined part of the MACD line only, then gets
    # mapped back onto the original index.
    start = next((i for i, v in enumerate(line) if v is not None), None)
    sig: OptSeries = [None] * len(values)
    hist: OptSeries = [None] * len(values)
    if start is None:
        return line, sig, hist

    dense = [v for v in line[start:] if v is not None]
    dense_sig = ema(dense, signal)
    for offset, v in enumerate(dense_sig):
        sig[start + offset] = v

    for i in range(len(values)):
        if line[i] is not None and sig[i] is not None:
            hist[i] = line[i] - sig[i]
    return line, sig, hist


def stochastic(
    highs: Series, lows: Series, closes: Series, period: int = 14, smooth: int = 3
) -> tuple[OptSeries, OptSeries]:
    """Stochastic oscillator, returns (%K, %D)."""
    hi = rolling_max(highs, period)
    lo = rolling_min(lows, period)
    k: OptSeries = [None] * len(closes)
    for i in range(len(closes)):
        if hi[i] is None or lo[i] is None:
            continue
        span = hi[i] - lo[i]
        k[i] = 50.0 if span == 0 else (closes[i] - lo[i]) / span * 100.0

    start = next((i for i, v in enumerate(k) if v is not None), None)
    d: OptSeries = [None] * len(closes)
    if start is None:
        return k, d
    dense_d = sma([v for v in k[start:] if v is not None], smooth)
    for offset, v in enumerate(dense_d):
        d[start + offset] = v
    return k, d


# --------------------------------------------------------------------------
# volatility / bands
# --------------------------------------------------------------------------


def bollinger(
    values: Series, period: int = 20, mult: float = 2.0
) -> tuple[OptSeries, OptSeries, OptSeries]:
    """Returns (upper, middle, lower)."""
    mid = sma(values, period)
    sd = stdev(values, period)
    upper: OptSeries = [None] * len(values)
    lower: OptSeries = [None] * len(values)
    for i in range(len(values)):
        if mid[i] is not None and sd[i] is not None:
            upper[i] = mid[i] + mult * sd[i]
            lower[i] = mid[i] - mult * sd[i]
    return upper, mid, lower


def percent_b(values: Series, period: int = 20, mult: float = 2.0) -> OptSeries:
    """Position within the Bollinger channel: 0 = lower band, 1 = upper band."""
    upper, _mid, lower = bollinger(values, period, mult)
    out: OptSeries = [None] * len(values)
    for i in range(len(values)):
        if upper[i] is None or lower[i] is None:
            continue
        span = upper[i] - lower[i]
        out[i] = 0.5 if span == 0 else (values[i] - lower[i]) / span
    return out


def true_range(highs: Series, lows: Series, closes: Series) -> Series:
    tr = [highs[0] - lows[0]] if highs else []
    for i in range(1, len(highs)):
        tr.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )
    return tr


def atr(highs: Series, lows: Series, closes: Series, period: int = 14) -> OptSeries:
    return wilder_smooth(true_range(highs, lows, closes), period)


def keltner(
    highs: Series, lows: Series, closes: Series, period: int = 20, mult: float = 2.0
) -> tuple[OptSeries, OptSeries, OptSeries]:
    """Keltner channel: EMA centre with ATR-scaled bands."""
    mid = ema(closes, period)
    rng = atr(highs, lows, closes, period)
    upper: OptSeries = [None] * len(closes)
    lower: OptSeries = [None] * len(closes)
    for i in range(len(closes)):
        if mid[i] is not None and rng[i] is not None:
            upper[i] = mid[i] + mult * rng[i]
            lower[i] = mid[i] - mult * rng[i]
    return upper, mid, lower


def donchian(
    highs: Series, lows: Series, period: int = 20
) -> tuple[OptSeries, OptSeries]:
    """Returns (upper, lower) of the Donchian channel."""
    return rolling_max(highs, period), rolling_min(lows, period)


def supertrend(
    highs: Series,
    lows: Series,
    closes: Series,
    period: int = 10,
    mult: float = 3.0,
) -> tuple[OptSeries, list[int | None]]:
    """Supertrend line and direction (+1 uptrend, -1 downtrend)."""
    rng = atr(highs, lows, closes, period)
    line: OptSeries = [None] * len(closes)
    direction: list[int | None] = [None] * len(closes)

    prev_upper: float | None = None
    prev_lower: float | None = None
    prev_dir: int | None = None

    for i in range(len(closes)):
        if rng[i] is None:
            continue
        hl2 = (highs[i] + lows[i]) / 2
        basic_upper = hl2 + mult * rng[i]
        basic_lower = hl2 - mult * rng[i]

        # Bands only tighten while the trend holds; they reset on a close through.
        if prev_upper is None or basic_upper < prev_upper or closes[i - 1] > prev_upper:
            upper = basic_upper
        else:
            upper = prev_upper
        if prev_lower is None or basic_lower > prev_lower or closes[i - 1] < prev_lower:
            lower = basic_lower
        else:
            lower = prev_lower

        if prev_dir is None:
            d = 1 if closes[i] >= upper else -1
        elif prev_dir == 1:
            d = -1 if closes[i] < lower else 1
        else:
            d = 1 if closes[i] > upper else -1

        line[i] = lower if d == 1 else upper
        direction[i] = d
        prev_upper, prev_lower, prev_dir = upper, lower, d

    return line, direction


def adx(
    highs: Series, lows: Series, closes: Series, period: int = 14
) -> tuple[OptSeries, OptSeries, OptSeries]:
    """Average Directional Index. Returns (adx, +DI, -DI)."""
    n = len(closes)
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0

    tr_s = wilder_smooth(true_range(highs, lows, closes), period)
    plus_s = wilder_smooth(plus_dm, period)
    minus_s = wilder_smooth(minus_dm, period)

    plus_di: OptSeries = [None] * n
    minus_di: OptSeries = [None] * n
    dx: list[float] = []
    dx_index: list[int] = []
    for i in range(n):
        if tr_s[i] in (None, 0) or plus_s[i] is None or minus_s[i] is None:
            continue
        plus_di[i] = 100.0 * plus_s[i] / tr_s[i]
        minus_di[i] = 100.0 * minus_s[i] / tr_s[i]
        denom = plus_di[i] + minus_di[i]
        if denom > 0:
            dx.append(100.0 * abs(plus_di[i] - minus_di[i]) / denom)
            dx_index.append(i)

    out: OptSeries = [None] * n
    for offset, v in enumerate(wilder_smooth(dx, period)):
        if v is not None:
            out[dx_index[offset]] = v
    return out, plus_di, minus_di


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def last_defined(series: OptSeries) -> float | None:
    """Most recent non-None value, or None if the series never warmed up."""
    for v in reversed(series):
        if v is not None:
            return v
    return None


def crossed_above(fast: OptSeries, slow: OptSeries, i: int) -> bool:
    """True when `fast` crosses up through `slow` on bar i."""
    if i < 1:
        return False
    a, b = fast[i - 1], slow[i - 1]
    c, d = fast[i], slow[i]
    if None in (a, b, c, d):
        return False
    return a <= b and c > d


def crossed_below(fast: OptSeries, slow: OptSeries, i: int) -> bool:
    """True when `fast` crosses down through `slow` on bar i."""
    if i < 1:
        return False
    a, b = fast[i - 1], slow[i - 1]
    c, d = fast[i], slow[i]
    if None in (a, b, c, d):
        return False
    return a >= b and c < d
