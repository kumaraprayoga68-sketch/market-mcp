"""Data providers. Each module exposes plain async functions returning our
shared candle/quote shapes; nothing here knows about MCP."""

from . import binance, idx, polymarket, yahoo

__all__ = ["binance", "idx", "polymarket", "yahoo"]
