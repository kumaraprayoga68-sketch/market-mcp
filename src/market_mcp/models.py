"""Shared data shapes.

One candle format for every provider, so indicators, the backtester and the
tools never need to know whether the bars came from Yahoo or Binance.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, TypedDict


class Candle(TypedDict):
    ts: int  # epoch milliseconds, bar open time
    open: float
    high: float
    low: float
    close: float
    volume: float


def series(candles: Iterable[Candle], field: str) -> list[float]:
    """Pull one OHLCV column out as a plain list."""
    return [float(c[field]) for c in candles]  # type: ignore[literal-required]


def ohlcv(candles: list[Candle]) -> tuple[list[float], list[float], list[float], list[float]]:
    """Convenience unpack: (highs, lows, closes, volumes)."""
    return (
        series(candles, "high"),
        series(candles, "low"),
        series(candles, "close"),
        series(candles, "volume"),
    )


def iso(ts_ms: int | float | None) -> str | None:
    """Epoch milliseconds -> ISO-8601 UTC string, for human/model-readable output."""
    if ts_ms is None:
        return None
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()


def round_sig(value: float | None, digits: int = 6) -> float | None:
    """Trim float noise before it goes over the wire."""
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def clean(obj: Any) -> Any:
    """Recursively round floats so tool payloads stay compact and readable."""
    if isinstance(obj, float):
        return round_sig(obj)
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    return obj


# Bars per year, used to annualise Sharpe/CAGR. Crypto trades every day of the
# year; equities only on ~252 sessions.
BARS_PER_YEAR: dict[str, dict[str, float]] = {
    "crypto": {
        "1m": 525600, "5m": 105120, "15m": 35040, "30m": 17520,
        "1h": 8760, "4h": 2190, "1d": 365, "1w": 52,
    },
    "equity": {
        "1m": 98280, "5m": 19656, "15m": 6552, "30m": 3276,
        "1h": 1638, "1d": 252, "1wk": 52, "1mo": 12,
    },
}


def bars_per_year(asset_class: str, interval: str) -> float:
    table = BARS_PER_YEAR.get(asset_class, BARS_PER_YEAR["equity"])
    return table.get(interval, 252.0)
