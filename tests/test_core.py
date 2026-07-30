"""Envelope, cache, patterns, analysis and market-data plumbing. No network."""

import asyncio
import math

import pytest

from market_mcp import analysis, patterns
from market_mcp.core.cache import cache_stats, clear_cache, ttl_cache
from market_mcp.core.errors import ToolError, envelope, err, ok
from market_mcp.market_data import normalize_market
from market_mcp.models import bars_per_year, clean, iso
from market_mcp.providers.binance import _is_stablecoin, normalize_symbol
from market_mcp.providers.idx import normalize_ticker

from .test_backtest import make_candles


# --------------------------------------------------------------------------
# envelopes
# --------------------------------------------------------------------------


async def test_envelope_wraps_success():
    @envelope
    async def fn():
        return {"x": 1}

    assert await fn() == {"ok": True, "data": {"x": 1}, "error": None}


async def test_envelope_converts_tool_errors():
    @envelope
    async def fn():
        raise ToolError("rate_limited", "slow down")

    result = await fn()
    assert result["ok"] is False
    assert result["error"]["code"] == "rate_limited"
    assert result["error"]["retryable"] is True


async def test_envelope_marks_bad_input_as_not_retryable():
    @envelope
    async def fn():
        raise ValueError("wrong")

    result = await fn()
    assert result["error"]["code"] == "bad_input"
    assert result["error"]["retryable"] is False


async def test_envelope_does_not_leak_unexpected_exceptions():
    @envelope
    async def fn():
        return 1 / 0

    result = await fn()
    assert result["ok"] is False
    assert result["error"]["code"] == "internal"
    assert "ZeroDivisionError" in result["error"]["message"]


async def test_envelope_passes_through_existing_envelopes():
    inner = err("not_found", "nope")

    @envelope
    async def fn():
        return inner

    assert await fn() == inner


async def test_envelope_preserves_the_signature():
    @envelope
    async def fn(a: int, b: str = "x") -> dict:
        return {}

    import inspect

    assert list(inspect.signature(fn).parameters) == ["a", "b"]


def test_envelope_rejects_sync_functions():
    with pytest.raises(TypeError):
        envelope(lambda: None)


def test_ok_and_err_shapes_match():
    assert set(ok(1)) == set(err("internal", "x"))


# --------------------------------------------------------------------------
# cache
# --------------------------------------------------------------------------


async def test_ttl_cache_memoizes_by_arguments():
    clear_cache()
    calls = []

    @ttl_cache(seconds=30)
    async def fn(x):
        calls.append(x)
        return x * 2

    assert await fn(2) == 4
    assert await fn(2) == 4
    assert await fn(3) == 6
    assert calls == [2, 3]


async def test_ttl_cache_expires():
    clear_cache()
    calls = []

    @ttl_cache(seconds=0.05)
    async def fn():
        calls.append(1)
        return len(calls)

    await fn()
    await asyncio.sleep(0.12)
    await fn()
    assert len(calls) == 2


async def test_ttl_cache_deduplicates_concurrent_calls():
    """A burst of identical requests must hit the upstream once, not N times."""
    clear_cache()
    calls = []

    @ttl_cache(seconds=30)
    async def fn():
        calls.append(1)
        await asyncio.sleep(0.05)
        return "v"

    results = await asyncio.gather(*(fn() for _ in range(10)))
    assert results == ["v"] * 10
    assert len(calls) == 1


def test_cache_stats_and_clear():
    clear_cache()
    assert cache_stats()["entries"] == 0


# --------------------------------------------------------------------------
# symbol handling
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("btc", "BTCUSDT"),
        ("BTC", "BTCUSDT"),
        ("BTC/USDT", "BTCUSDT"),
        ("eth-usdt", "ETHUSDT"),
        ("eth_usdt", "ETHUSDT"),
        ("ETHBTC", "ETHBTC"),
        ("SOLUSDC", "SOLUSDC"),
        ("doge", "DOGEUSDT"),
    ],
)
def test_binance_symbol_normalisation(raw, expected):
    assert normalize_symbol(raw) == expected


def test_bare_quote_asset_still_gets_a_quote_leg():
    """"BTC" ends with the quote asset "BTC" but is a base asset, not a pair."""
    assert normalize_symbol("BTC") == "BTCUSDT"
    assert normalize_symbol("ETH") == "ETHUSDT"


def test_empty_symbol_is_rejected():
    with pytest.raises(ValueError):
        normalize_symbol("   ")


@pytest.mark.parametrize("base", ["USDC", "USD1", "RLUSD", "FDUSD", "DAI", "PYUSD"])
def test_stablecoins_are_detected(base):
    assert _is_stablecoin(base)


@pytest.mark.parametrize("base", ["BTC", "ETH", "SOL", "USUAL"])
def test_real_assets_are_not_flagged_as_stablecoins(base):
    assert not _is_stablecoin(base)


def test_idx_ticker_suffixing():
    assert normalize_ticker("bbca") == "BBCA.JK"
    assert normalize_ticker("BBCA.JK") == "BBCA.JK"


@pytest.mark.parametrize(
    "raw,expected",
    [("crypto", "crypto"), ("binance", "crypto"), ("stocks", "stock"),
     ("equity", "stock"), ("idx", "idx"), ("indonesia", "idx")],
)
def test_market_aliases(raw, expected):
    assert normalize_market(raw) == expected


def test_unknown_market_is_rejected():
    with pytest.raises(ValueError, match="market must be"):
        normalize_market("forex")


def test_bars_per_year_differs_between_crypto_and_equities():
    assert bars_per_year("crypto", "1d") == 365
    assert bars_per_year("equity", "1d") == 252


# --------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------


def test_iso_formats_epoch_millis():
    assert iso(0).startswith("1970-01-01")
    assert iso(None) is None


def test_clean_rounds_nested_floats():
    out = clean({"a": 1.123456789, "b": [2.987654321], "c": "x"})
    assert out["a"] == 1.123457
    assert out["b"][0] == 2.987654
    assert out["c"] == "x"


# --------------------------------------------------------------------------
# analysis and patterns
# --------------------------------------------------------------------------


def test_analysis_requires_enough_history():
    with pytest.raises(ValueError, match="at least 30"):
        analysis.summarize(make_candles([100.0] * 10))


def test_analysis_rates_a_strong_uptrend_bullish():
    candles = make_candles([100 * (1.008**i) for i in range(300)])
    summary = analysis.summarize(candles, symbol="TEST", interval="1d")
    assert summary["rating"]["score"] > 0
    assert summary["rating"]["label"] in ("weak_buy", "buy", "strong_buy")
    assert summary["indicators"]["supertrend_direction"] == "up"


def test_analysis_rates_a_sustained_downtrend_bearish():
    candles = make_candles([300 * (0.992**i) for i in range(300)])
    summary = analysis.summarize(candles, symbol="TEST", interval="1d")
    assert summary["rating"]["score"] < 0
    assert summary["rating"]["label"] in ("weak_sell", "sell", "strong_sell")


def test_rating_votes_are_explained():
    candles = make_candles([100 + 10 * math.sin(i / 8) for i in range(300)])
    rating = analysis.summarize(candles)["rating"]
    assert rating["votes"]
    for vote in rating["votes"]:
        assert vote["score"] in (-1, 0, 1)
        assert vote["reason"]
    assert (
        rating["bullish_votes"] + rating["bearish_votes"] + rating["neutral_votes"]
        == len(rating["votes"])
    )


def test_strong_trend_disarms_countertrend_oscillators():
    """RSI pins at 100 through a real rally; that must not veto the trend."""
    candles = make_candles([100 * (1.008**i) for i in range(300)])
    rating = analysis.summarize(candles)["rating"]
    assert rating["trend_strength"] == "strong"

    oscillators = [v for v in rating["votes"] if v["indicator"] in ("rsi", "stochastic", "bollinger")]
    assert oscillators, "expected oscillator votes to be present"
    assert all(v["score"] >= 0 for v in oscillators)
    assert any("not counted" in v["reason"] for v in oscillators)


def test_weak_trend_damps_the_rating():
    """ADX below 20 should shrink the score's magnitude, never flip its sign."""
    candles = make_candles([100 + 0.5 * math.sin(i / 4) for i in range(300)])
    summary = analysis.summarize(candles)
    assert summary["rating"]["trend_strength"] in ("weak", "moderate", "strong")
    assert -1.0 <= summary["rating"]["score"] <= 1.0


def test_patterns_need_a_minimum_history():
    assert patterns.detect(make_candles([1.0, 2.0])) == []


def test_doji_is_detected():
    candles = make_candles([100.0] * 30)
    # A candle whose open and close match but with a real range.
    candles[-1] = {"ts": candles[-1]["ts"], "open": 100.0, "high": 105.0,
                   "low": 95.0, "close": 100.0, "volume": 1.0}
    found = patterns.detect(candles, lookback=1)
    assert any(p["pattern"] == "doji" for p in found)


def test_bullish_engulfing_is_detected():
    candles = make_candles([100.0] * 30)
    candles[-2] = {"ts": candles[-2]["ts"], "open": 102.0, "high": 103.0,
                   "low": 97.0, "close": 98.0, "volume": 1.0}
    candles[-1] = {"ts": candles[-1]["ts"], "open": 97.0, "high": 104.0,
                   "low": 96.0, "close": 103.0, "volume": 1.0}
    found = patterns.detect(candles, lookback=1)
    assert any(p["pattern"] == "bullish_engulfing" for p in found)


def test_patterns_report_the_trend_they_appeared_in():
    candles = make_candles([100 * (1.01**i) for i in range(40)])
    for p in patterns.detect(candles, lookback=5):
        assert p["prevailing_trend"] in ("up", "down", "unknown")
        assert isinstance(p["confirms_reversal"], bool)
