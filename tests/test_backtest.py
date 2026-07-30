"""Backtest engine invariants.

The look-ahead test is the important one: if the engine ever applies a signal
to the bar that produced it, every metric this project reports becomes
meaningless. It is asserted here rather than assumed.
"""

import math
import random

import pytest

from market_mcp.backtest import REGISTRY, get, resolve_params, run_backtest, walk_forward


def make_candles(prices):
    return [
        {
            "ts": (1_700_000_000 + i * 86_400) * 1000,
            "open": p,
            "high": p * 1.01,
            "low": p * 0.99,
            "close": p,
            "volume": 1000.0,
        }
        for i, p in enumerate(prices)
    ]


def bt(candles, signals, **kw):
    kw.setdefault("symbol", "TEST")
    kw.setdefault("strategy", "test")
    kw.setdefault("params", {})
    kw.setdefault("interval", "1d")
    kw.setdefault("bars_per_year", 365)
    kw.setdefault("fee_bps", 0)
    kw.setdefault("slippage_bps", 0)
    return run_backtest(candles, signals, **kw)


@pytest.fixture
def trending():
    return make_candles([100 * (1.01**i) * (1 + 0.02 * math.sin(i / 3)) for i in range(300)])


@pytest.fixture
def noisy():
    rng = random.Random(7)
    prices = [100.0]
    for _ in range(499):
        prices.append(max(1.0, prices[-1] * (1 + rng.gauss(0, 0.02))))
    return make_candles(prices)


def test_always_long_equals_buy_and_hold(trending):
    res = bt(trending, [1] * len(trending))
    assert res.metrics["total_return_pct"] == pytest.approx(
        res.metrics["buy_and_hold_return_pct"], abs=0.01
    )


def test_always_flat_returns_nothing(trending):
    res = bt(trending, [0] * len(trending))
    assert res.metrics["total_return_pct"] == pytest.approx(0.0)
    assert res.metrics["trades"] == 0
    assert res.metrics["exposure_pct"] == 0.0


def test_no_lookahead(noisy):
    """A rule reading the next bar must be far more profitable than the same
    rule lagged by one bar. If the engine leaked the future, both would win."""
    closes = [c["close"] for c in noisy]
    oracle = [1 if i + 1 < len(closes) and closes[i + 1] > closes[i] else 0 for i in range(len(closes))]
    lagged = [0] + oracle[:-1]

    r_oracle = bt(noisy, oracle).metrics["total_return_pct"]
    r_lagged = bt(noisy, lagged).metrics["total_return_pct"]

    assert r_oracle > 1000
    assert r_lagged < r_oracle / 10


def test_costs_are_charged_on_every_position_change():
    flat = make_candles([100.0] * 200)
    flip = [i % 2 for i in range(200)]

    free = bt(flat, flip).metrics["total_return_pct"]
    paid = bt(flat, flip, fee_bps=10, slippage_bps=5).metrics["total_return_pct"]

    assert free == pytest.approx(0.0, abs=1e-9)
    assert paid < -10


def test_flipping_long_to_short_pays_double():
    flat = make_candles([100.0] * 100)
    one_way = [0] * 50 + [1] * 50           # flat -> long: turnover 1
    both_ways = [-1] * 50 + [1] * 50        # short -> long: turnover 2

    a = bt(flat, one_way, fee_bps=100, slippage_bps=0).metrics["total_return_pct"]
    b = bt(flat, both_ways, fee_bps=100, slippage_bps=0).metrics["total_return_pct"]
    assert b < a


def test_drawdown_is_measured_from_the_peak():
    prices = [100.0] * 10 + [200.0] * 10 + [100.0] * 10
    res = bt(make_candles(prices), [1] * 30)
    assert res.metrics["max_drawdown_pct"] == pytest.approx(50.0, abs=0.1)


def test_metrics_survive_a_zero_volatility_series():
    res = bt(make_candles([100.0] * 60), [1] * 60)
    assert res.metrics["sharpe"] is None  # undefined, not a crash
    assert res.metrics["max_drawdown_pct"] == 0.0


def test_rejects_mismatched_signal_length(trending):
    with pytest.raises(ValueError, match="signals length"):
        bt(trending, [1] * 5)


def test_rejects_too_few_candles():
    with pytest.raises(ValueError, match="at least 3"):
        bt(make_candles([1.0, 2.0]), [0, 0])


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_strategy_produces_valid_signals(name, noisy):
    strat = REGISTRY[name]
    signals = strat.fn(noisy, **strat.defaults)
    assert len(signals) == len(noisy)
    assert set(signals) <= {-1, 0, 1}
    # A long-only strategy must never emit a short.
    if not strat.supports_short:
        assert min(signals) >= 0


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_strategy_backtests(name, noisy):
    strat = REGISTRY[name]
    res = bt(noisy, strat.fn(noisy, **strat.defaults), strategy=name, params=strat.defaults)
    assert res.metrics["final_equity"] > 0
    assert res.bars == len(noisy)


def test_unknown_strategy_names_are_rejected():
    with pytest.raises(ValueError, match="unknown strategy"):
        get("nope")


def test_unknown_parameters_are_rejected():
    with pytest.raises(ValueError, match="is not a parameter"):
        resolve_params(get("rsi_reversion"), {"bogus": 1})


def test_parameter_overrides_merge_onto_defaults():
    params = resolve_params(get("rsi_reversion"), {"period": 21})
    assert params["period"] == 21
    assert params["oversold"] == 30  # untouched default


def test_walk_forward_reports_out_of_sample_folds(noisy):
    report = walk_forward(
        noisy, get("ema_cross"), symbol="TEST", interval="1d",
        bars_per_year=365, folds=3, fee_bps=0, slippage_bps=0,
    )
    assert report["folds_completed"] >= 2
    assert report["verdict"]["label"] in (
        "robust", "acceptable", "fragile", "likely_overfitted", "overfitted", "inconclusive",
    )
    for fold in report["folds"]:
        # Each fold must test on bars after the ones it trained on.
        assert fold["test_bars"] > 0
        assert fold["train_bars"] >= fold["test_bars"]


def test_walk_forward_refuses_impossible_fold_counts(noisy):
    with pytest.raises(ValueError, match="candles"):
        walk_forward(
            noisy[:100], get("ema_cross"), symbol="TEST", interval="1d",
            bars_per_year=365, folds=10,
        )


def test_trade_log_pairs_entries_with_exits(trending):
    # Alternate 20 bars flat / 20 bars long, padded to exactly the candle count.
    cycle = [0] * 20 + [1] * 20
    signals = [cycle[i % len(cycle)] for i in range(len(trending))]
    res = bt(trending, signals)
    assert res.metrics["trades"] == len(res.trades)
    for t in res.trades:
        assert t.entry_time is not None and t.exit_time is not None
        assert t.bars_held > 0
        assert t.side in ("long", "short")
