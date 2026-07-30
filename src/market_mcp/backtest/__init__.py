"""Backtesting: signal generation, simulation, and walk-forward validation."""

from .engine import BacktestResult, Trade, run_backtest
from .strategies import REGISTRY, Strategy, get, resolve_params
from .walkforward import walk_forward

__all__ = [
    "REGISTRY",
    "BacktestResult",
    "Strategy",
    "Trade",
    "get",
    "resolve_params",
    "run_backtest",
    "walk_forward",
]
