"""IDX (Bursa Efek Indonesia) helpers layered on top of Yahoo Finance.

Yahoo carries Indonesian equities under a `.JK` suffix, so IDX support is
mostly a matter of ticker normalisation plus a curated universe file — there is
no free bulk screener endpoint for IDX worth relying on.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

from ..core.errors import ToolError
from . import yahoo

# data/ sits at the repo root, two levels above src/market_mcp/providers/.
DATA_DIR = Path(__file__).resolve().parents[3] / "data"

UNIVERSES = {"idx": "idx.txt", "us": "us.txt"}

IDX_COMPOSITE = "^JKSE"


def normalize_ticker(ticker: str) -> str:
    """BBCA -> BBCA.JK. Already-suffixed tickers pass through."""
    t = ticker.strip().upper()
    if not t:
        raise ValueError("ticker must not be empty")
    return t if "." in t else f"{t}.JK"


@functools.lru_cache(maxsize=8)
def load_universe(name: str = "idx") -> tuple[str, ...]:
    """Read a ticker list from data/. Blank lines and `#` comments are skipped."""
    filename = UNIVERSES.get(name)
    if filename is None:
        raise ValueError(f"unknown universe '{name}'; available: {sorted(UNIVERSES)}")

    path = DATA_DIR / filename
    if not path.exists():
        raise ToolError("not_found", f"universe file missing: {path}")

    tickers = []
    for line in path.read_text(encoding="utf-8").splitlines():
        t = line.strip()
        if t and not t.startswith("#"):
            tickers.append(t.upper())
    # dict.fromkeys dedupes while preserving the curated ordering.
    return tuple(dict.fromkeys(tickers))


async def quote(ticker: str) -> dict[str, Any]:
    return await yahoo.quote(normalize_ticker(ticker))


async def candles(ticker: str, interval: str = "1d", range_: str = "1y"):
    return await yahoo.candles(normalize_ticker(ticker), interval, range_)


async def scan(
    universe: str = "idx",
    *,
    max_symbols: int = 120,
    min_price: float = 0.0,
    concurrency: int = 8,
) -> list[dict[str, Any]]:
    """Quote a slice of a universe. Returns rows sorted by % change, desc.

    `max_symbols` is deliberately capped: the universe file is ordered by
    liquidity, so the head of the list is where tradeable names live, and
    quoting all ~290 IDX tickers costs a lot of latency for little extra signal.
    """
    tickers = load_universe(universe)
    if not tickers:
        raise ToolError("not_found", f"universe '{universe}' is empty")

    subset = list(tickers[: max(1, min(int(max_symbols), len(tickers)))])
    rows = await yahoo.quotes_bulk(subset, concurrency=concurrency)

    rows = [r for r in rows if (r.get("price") or 0) >= min_price]
    rows.sort(key=lambda r: r.get("change_pct") if r.get("change_pct") is not None else -999, reverse=True)
    return rows
