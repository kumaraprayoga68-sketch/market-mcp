"""Price and reference-data tools."""

from __future__ import annotations

import asyncio
from typing import Any

from ..core.errors import envelope
from ..market_data import load_quote
from ..models import clean
from ..providers import yahoo


def register(server: Any) -> None:
    @server.tool()
    @envelope
    async def get_price(symbol: str, market: str = "stock") -> dict[str, Any]:
        """Get the current price and daily change for one instrument.

        Args:
            symbol: Ticker. Crypto accepts BTC, BTCUSDT or BTC/USDT; equities use
                the Yahoo symbol (AAPL, MSFT); IDX accepts BBCA or BBCA.JK.
            market: One of "crypto", "stock" (global equities/ETFs/indices/FX)
                or "idx" (Indonesia Stock Exchange).

        Returns price, previous close, change %, day range and — for equities —
        the 52-week range and where price sits inside it.
        """
        return clean(await load_quote(symbol, market))

    @server.tool()
    @envelope
    async def get_prices(symbols: list[str], market: str = "stock") -> dict[str, Any]:
        """Get current prices for several instruments at once.

        Args:
            symbols: Up to 50 tickers.
            market: "crypto", "stock" or "idx" — applies to every symbol.

        Symbols that cannot be resolved are reported in `failed` rather than
        failing the whole call.
        """
        if not symbols:
            raise ValueError("symbols must not be empty")
        wanted = symbols[:50]

        results = await asyncio.gather(
            *(load_quote(s, market) for s in wanted), return_exceptions=True
        )
        quotes, failed = [], []
        for sym, r in zip(wanted, results):
            if isinstance(r, BaseException):
                failed.append({"symbol": sym, "reason": str(r)})
            else:
                quotes.append(r)

        quotes.sort(key=lambda q: q.get("change_pct") if q.get("change_pct") is not None else -1e9, reverse=True)
        return clean({"count": len(quotes), "quotes": quotes, "failed": failed})

    @server.tool()
    @envelope
    async def search_symbol(query: str, limit: int = 10) -> dict[str, Any]:
        """Find the ticker for a company or asset by name.

        Args:
            query: Company or asset name, e.g. "bank central asia" or "nvidia".
            limit: Maximum matches to return.

        Use this first whenever the user names a company rather than a ticker.
        """
        matches = await yahoo.search(query, max(1, min(int(limit), 25)))
        return {"query": query, "count": len(matches), "matches": matches}

    @server.tool()
    @envelope
    async def market_snapshot() -> dict[str, Any]:
        """Get a cross-market overview: US and Asian indices, VIX, crypto, FX and commodities.

        Includes the IDX Composite (^JKSE) and USD/IDR. Use this to answer
        "how are markets doing" without picking symbols by hand.
        """
        pairs = [(sym, label, group) for group, items in yahoo.SNAPSHOT_SYMBOLS.items()
                 for sym, label in items]
        quotes = await yahoo.quotes_bulk([p[0] for p in pairs], concurrency=8)
        by_symbol = {q["symbol"]: q for q in quotes}

        groups: dict[str, list[dict[str, Any]]] = {}
        for sym, label, group in pairs:
            q = by_symbol.get(sym)
            if q is None:
                continue
            groups.setdefault(group, []).append(
                {
                    "name": label,
                    "symbol": sym,
                    "price": q.get("price"),
                    "change_pct": q.get("change_pct"),
                    "currency": q.get("currency"),
                }
            )

        movers = [row for rows in groups.values() for row in rows if row.get("change_pct") is not None]
        movers.sort(key=lambda r: abs(r["change_pct"]), reverse=True)

        return clean(
            {
                "groups": groups,
                "biggest_movers": movers[:5],
                "note": "Percentages are versus the previous close in each instrument's own session.",
            }
        )
