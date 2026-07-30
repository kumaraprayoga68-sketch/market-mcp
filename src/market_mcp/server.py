"""market-mcp server entry point."""

from __future__ import annotations

import argparse
import logging

from mcp.server import MCPServer

from . import __version__
from .tools import register_all

INSTRUCTIONS = """\
Market data, technical analysis, prediction-market odds and strategy backtesting.

Coverage:
  - crypto: Binance spot pairs (market="crypto")
  - stock:  global equities, ETFs, indices and FX via Yahoo (market="stock")
  - idx:    Indonesia Stock Exchange, .JK tickers (market="idx")
  - prediction markets: Polymarket

How to use these tools well:
  - If the user names a company rather than a ticker, call search_symbol first.
  - technical_analysis returns the individual indicator votes behind its rating.
    Explain the reasoning; do not just report the label.
  - Backtests already account for costs and contain no look-ahead, but a single
    backtest is weak evidence. Confirm anything promising with
    walk_forward_backtest and always compare against buy-and-hold.
  - Polymarket prices are probabilities: 0.63 means the market says 63%.

Every tool returns {"ok", "data", "error"}. On failure, `error.retryable` says
whether trying again could help — do not retry a "bad_input" or "not_found".

This server provides information, not financial advice. State that clearly when
the user asks what to buy or sell.\
"""


def _quiet_http_logs() -> None:
    """Stop httpx from logging a line per request.

    On the stdio transport the JSON-RPC stream shares the process with the log
    handlers, so a chatty library is not just noise — a screener firing 60
    requests would bury the client in log records.
    """
    for name in ("httpx", "httpcore", "httpx2", "httpcore2"):
        logging.getLogger(name).setLevel(logging.WARNING)


def build_server() -> MCPServer:
    _quiet_http_logs()
    server = MCPServer(
        name="market-mcp",
        version=__version__,
        instructions=INSTRUCTIONS,
    )
    register_all(server)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(prog="market-mcp", description="market-mcp server")
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse", "streamable-http"],
        help="stdio for desktop clients; http transports for remote hosting",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    server = build_server()
    if args.transport == "stdio":
        server.run("stdio")
    else:
        server.run(args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
