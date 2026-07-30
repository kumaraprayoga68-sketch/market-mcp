"""Polymarket prediction-market tools.

Prices on Polymarket are probabilities: a share settles at $1 if the outcome
happens and $0 if it does not, so $0.63 means the market says 63%. Every tool
here surfaces that reading explicitly.
"""

from __future__ import annotations

from typing import Any

from ..core.errors import envelope
from ..models import clean
from ..providers import polymarket


def register(server: Any) -> None:
    @server.tool()
    @envelope
    async def prediction_markets(
        limit: int = 20, order: str = "volume24hr", tag: str | None = None
    ) -> dict[str, Any]:
        """List the most active Polymarket prediction markets.

        Args:
            limit: Markets to return (max 100).
            order: "volume24hr", "volume", "liquidity" or "endDate".
            tag: Optional category slug, e.g. "politics", "crypto", "sports".

        Prices are probabilities: 0.63 means the market prices that outcome at 63%.
        """
        rows = await polymarket.list_markets(limit, order=order, tag=tag)
        return clean({"count": len(rows), "order": order, "tag": tag, "markets": rows})

    @server.tool()
    @envelope
    async def prediction_search(query: str, limit: int = 15) -> dict[str, Any]:
        """Search active prediction markets by keyword.

        Args:
            query: Keywords, e.g. "fed rate", "bitcoin 100k", "election".
            limit: Matches to return.

        Matches in the market question rank above matches that only appear in
        the description. Polymarket has no server-side text search, so this
        scans the most active markets rather than every market ever created.
        """
        rows = await polymarket.search_markets(query, max(1, min(int(limit), 50)))
        return clean(
            {
                "query": query,
                "count": len(rows),
                "markets": rows,
                "note": "Searches active, high-volume markets only; resolved markets are excluded.",
            }
        )

    @server.tool()
    @envelope
    async def prediction_market_detail(slug_or_id: str) -> dict[str, Any]:
        """Get full detail for one prediction market, including outcome token ids.

        Args:
            slug_or_id: Market slug (from a polymarket.com URL) or numeric id.

        The `token_id` on each outcome is what prediction_price_history needs.
        """
        return clean(await polymarket.market_detail(slug_or_id))

    @server.tool()
    @envelope
    async def prediction_price_history(
        token_id: str, interval: str = "1w", fidelity: int = 60
    ) -> dict[str, Any]:
        """Get the probability history for one market outcome.

        Args:
            token_id: Outcome token id from prediction_market_detail.
            interval: Window to cover — "1h", "6h", "1d", "1w", "1m" or "max".
            fidelity: Bucket size in minutes. Larger values return fewer points.

        Use this to see whether the market's view has been drifting or is
        reacting to something recent.
        """
        points = await polymarket.price_history(token_id, interval, max(1, int(fidelity)))
        if not points:
            return {"token_id": token_id, "interval": interval, "count": 0, "history": []}

        prices = [p["price"] for p in points if p["price"] is not None]
        first, last = prices[0], prices[-1]

        # Downsample long series: a model reasoning about a trend does not need
        # 700 raw points, and sending them all crowds out its context.
        step = max(1, len(points) // 120)

        return clean(
            {
                "token_id": token_id,
                "interval": interval,
                "count": len(points),
                "start_probability_pct": round(first * 100, 2),
                "current_probability_pct": round(last * 100, 2),
                "change_pct_points": round((last - first) * 100, 2),
                "high_probability_pct": round(max(prices) * 100, 2),
                "low_probability_pct": round(min(prices) * 100, 2),
                "history": points[::step][-120:],
                "downsampled_every_n_points": step,
            }
        )
