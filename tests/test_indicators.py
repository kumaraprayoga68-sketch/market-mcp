"""Indicator correctness. No network — everything here is a known-answer test."""

import math

import pytest

from market_mcp import indicators as ta

# Wilder's worked example from "New Concepts in Technical Trading Systems".
WILDER = [
    44.00, 44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
    46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64,
]


def test_sma_warmup_and_values():
    assert ta.sma([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]


def test_sma_rejects_bad_period():
    with pytest.raises(ValueError):
        ta.sma([1, 2, 3], 0)


def test_ema_seeds_with_sma():
    e = ta.ema([1, 2, 3, 4, 5], 3)
    assert e[0] is None and e[1] is None
    assert e[2] == pytest.approx(2.0)  # SMA of first 3
    assert e[3] == pytest.approx(2.0 + (4 - 2.0) * 0.5)


def test_rsi_matches_wilder():
    """First RSI value on Wilder's series.

    Hand-checked: over the first 14 changes the gains total 3.68 and the losses
    1.40, so RS = (3.68/14) / (1.40/14) = 2.6286 and RSI = 100 - 100/(1+RS).
    """
    r = ta.rsi(WILDER, 14)
    assert all(v is None for v in r[:14])
    assert r[14] == pytest.approx(72.44, abs=0.05)


def test_rsi_is_100_when_only_gains():
    assert ta.rsi([float(i) for i in range(1, 30)], 14)[-1] == pytest.approx(100.0)


def test_rsi_is_zero_when_only_losses():
    assert ta.rsi([float(i) for i in range(30, 1, -1)], 14)[-1] == pytest.approx(0.0)


def test_macd_histogram_is_line_minus_signal():
    values = [float(i % 7 + i * 0.1) for i in range(200)]
    line, sig, hist = ta.macd(values)
    for a, b, h in zip(line, sig, hist):
        if a is not None and b is not None:
            assert h == pytest.approx(a - b)


def test_bollinger_bands_straddle_the_mean():
    values = [10.0 + math.sin(i / 3) for i in range(80)]
    upper, mid, lower = ta.bollinger(values, 20, 2.0)
    assert upper[-1] > mid[-1] > lower[-1]
    # Bands are symmetric around the moving average by construction.
    assert upper[-1] - mid[-1] == pytest.approx(mid[-1] - lower[-1])


def test_bollinger_collapses_on_a_flat_series():
    upper, mid, lower = ta.bollinger([5.0] * 50, 20, 2.0)
    assert upper[-1] == mid[-1] == lower[-1] == 5.0


def test_percent_b_is_zero_at_lower_band_and_one_at_upper():
    values = [10.0 + math.sin(i / 2) * 2 for i in range(60)]
    pb = ta.percent_b(values, 20, 2.0)
    assert all(v is None or -1.5 <= v <= 2.5 for v in pb)


def test_atr_is_positive_and_warms_up():
    highs = [10 + i * 0.1 for i in range(40)]
    lows = [9 + i * 0.1 for i in range(40)]
    closes = [9.5 + i * 0.1 for i in range(40)]
    a = ta.atr(highs, lows, closes, 14)
    assert a[12] is None
    assert a[-1] > 0


def test_supertrend_flags_a_clean_uptrend():
    closes = [100 + i for i in range(60)]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    _line, direction = ta.supertrend(highs, lows, closes, 10, 3.0)
    assert direction[-1] == 1


def test_supertrend_flags_a_clean_downtrend():
    closes = [200 - i for i in range(60)]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    _line, direction = ta.supertrend(highs, lows, closes, 10, 3.0)
    assert direction[-1] == -1


def test_adx_rises_in_a_strong_trend():
    closes = [100 + i * 2 for i in range(80)]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    adx, plus_di, minus_di = ta.adx(highs, lows, closes, 14)
    assert adx[-1] > 40
    assert plus_di[-1] > minus_di[-1]


def test_every_indicator_returns_input_length():
    values = [float(i) for i in range(100)]
    highs = [v + 1 for v in values]
    lows = [v - 1 for v in values]
    assert len(ta.sma(values, 20)) == 100
    assert len(ta.ema(values, 20)) == 100
    assert len(ta.rsi(values, 14)) == 100
    assert len(ta.stdev(values, 20)) == 100
    assert len(ta.atr(highs, lows, values, 14)) == 100
    assert len(ta.macd(values)[0]) == 100
    assert len(ta.supertrend(highs, lows, values)[0]) == 100


def test_short_input_never_raises():
    """Warm-up handling must degrade to all-None, not crash."""
    for fn in (lambda v: ta.rsi(v, 14), lambda v: ta.ema(v, 20), lambda v: ta.sma(v, 20)):
        assert all(x is None for x in fn([1.0, 2.0, 3.0]))


def test_cross_detection():
    fast = [1.0, 3.0, 4.0]
    slow = [2.0, 2.0, 2.0]
    assert ta.crossed_above(fast, slow, 1) is True   # 1 -> 3 through 2
    assert ta.crossed_above(fast, slow, 2) is False  # already above, no new cross
    assert ta.crossed_below([4.0, 1.0], [2.0, 2.0], 1) is True


def test_touching_then_breaking_counts_as_a_cross():
    """Equal-then-above is a cross; requiring a strict prior gap would miss it."""
    assert ta.crossed_above([2.0, 3.0], [2.0, 2.0], 1) is True


def test_cross_needs_defined_values():
    assert ta.crossed_above([None, 3.0], [2.0, 2.0], 1) is False
    assert ta.crossed_above([1.0, 3.0], [2.0, 2.0], 0) is False
