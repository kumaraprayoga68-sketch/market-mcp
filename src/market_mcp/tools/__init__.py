"""MCP tool registration. Each module exposes `register(server)`."""

from typing import Any

from . import analysis, backtesting, prediction, quotes, screeners

MODULES = (quotes, analysis, screeners, prediction, backtesting)


def register_all(server: Any) -> None:
    for module in MODULES:
        module.register(server)


__all__ = ["MODULES", "register_all"]
