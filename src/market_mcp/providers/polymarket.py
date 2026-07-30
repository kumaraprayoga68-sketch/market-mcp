"""Polymarket provider — prediction-market odds via the public Gamma + CLOB APIs.

Gamma serves market metadata and current outcome prices; the CLOB serves the
price history for a single outcome token. Neither needs a key for reads.
"""

from __future__ import annotations

import json
from typing import Any

from ..core.cache import ttl_cache
from ..core.errors import ToolError
from ..core.http import fetch_json
from ..models import iso

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

SORT_FIELDS = {"volume24hr", "volume", "liquidity", "endDate", "startDate"}


def _jsonish(value: Any) -> Any:
    """Gamma returns some array fields as JSON-encoded strings."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _summarize(m: dict[str, Any]) -> dict[str, Any]:
    """Flatten one Gamma market into the fields a model actually reasons over."""
    outcomes = _jsonish(m.get("outcomes")) or []
    prices = _jsonish(m.get("outcomePrices")) or []
    token_ids = _jsonish(m.get("clobTokenIds")) or []

    book = []
    for i, name in enumerate(outcomes if isinstance(outcomes, list) else []):
        p = None
        if isinstance(prices, list) and i < len(prices):
            try:
                p = float(prices[i])
            except (TypeError, ValueError):
                p = None
        book.append(
            {
                "outcome": name,
                "price": p,
                "implied_probability_pct": None if p is None else round(p * 100, 2),
                "token_id": token_ids[i] if isinstance(token_ids, list) and i < len(token_ids) else None,
            }
        )

    return {
        "id": m.get("id"),
        "slug": m.get("slug"),
        "question": m.get("question"),
        "outcomes": book,
        "volume_24h": _f(m.get("volume24hr")),
        "volume_total": _f(m.get("volume")),
        "liquidity": _f(m.get("liquidity")),
        "spread": _f(m.get("spread")),
        "best_bid": _f(m.get("bestBid")),
        "best_ask": _f(m.get("bestAsk")),
        "active": m.get("active"),
        "closed": m.get("closed"),
        "end_date": m.get("endDate"),
        "url": f"https://polymarket.com/market/{m['slug']}" if m.get("slug") else None,
    }


@ttl_cache(seconds=30)
async def _markets_raw(params_key: str) -> list[dict[str, Any]]:
    payload = await fetch_json(
        f"{GAMMA}/markets", params=json.loads(params_key), source="Polymarket Gamma"
    )
    if isinstance(payload, dict):  # some deployments wrap the list
        payload = payload.get("data") or []
    return payload or []


async def list_markets(
    limit: int = 20,
    *,
    order: str = "volume24hr",
    active_only: bool = True,
    tag: str | None = None,
) -> list[dict[str, Any]]:
    """Markets ranked by activity."""
    if order not in SORT_FIELDS:
        raise ValueError(f"order must be one of {sorted(SORT_FIELDS)}")
    params: dict[str, Any] = {
        "limit": max(1, min(int(limit), 100)),
        "order": order,
        "ascending": "false",
    }
    if active_only:
        params["active"] = "true"
        params["closed"] = "false"
    if tag:
        params["tag_slug"] = tag
    rows = await _markets_raw(json.dumps(params, sort_keys=True))
    return [_summarize(m) for m in rows]


PAGE_SIZE = 100  # Gamma silently caps `limit` at 100, so paginate with `offset`.


async def _markets_paged(base: dict[str, Any], pages: int) -> list[dict[str, Any]]:
    """Walk `pages` pages of markets, stopping early on a short page."""
    out: list[dict[str, Any]] = []
    for page in range(pages):
        params = {**base, "limit": PAGE_SIZE, "offset": page * PAGE_SIZE}
        rows = await _markets_raw(json.dumps(params, sort_keys=True))
        out.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
    return out


async def search_markets(
    query: str, limit: int = 20, *, scan_pages: int = 5
) -> list[dict[str, Any]]:
    """Keyword search over active markets.

    Gamma exposes no full-text search for markets, so we page through the most
    active ones and filter locally. Matches in the question outrank matches that
    only appear in the description, so a market actually *about* the query wins
    over one that merely mentions it in fine print.
    """
    q = query.strip().lower()
    if not q:
        raise ValueError("query must not be empty")
    terms = q.split()

    rows = await _markets_paged(
        {"order": "volume24hr", "ascending": "false", "active": "true", "closed": "false"},
        pages=max(1, min(int(scan_pages), 20)),
    )

    scored: list[tuple[int, float, dict[str, Any]]] = []
    for m in rows:
        title = f"{m.get('question', '')} {m.get('slug', '')}".lower()
        body = (m.get("description") or "").lower()
        if not all(t in title or t in body for t in terms):
            continue
        score = sum(2 if t in title else 1 for t in terms)
        scored.append((score, _f(m.get("volume24hr")) or 0.0, m))

    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [_summarize(m) for _score, _vol, m in scored[:limit]]


async def market_detail(slug_or_id: str) -> dict[str, Any]:
    """Full detail for one market, by slug or numeric id."""
    key = slug_or_id.strip()
    if not key:
        raise ValueError("slug_or_id must not be empty")

    params = {"id": key} if key.isdigit() else {"slug": key}
    rows = await _markets_raw(json.dumps(params, sort_keys=True))
    if not rows:
        raise ToolError("not_found", f"Polymarket has no market '{key}'")

    m = rows[0]
    detail = _summarize(m)
    detail["description"] = m.get("description")
    detail["start_date"] = m.get("startDate")
    detail["accepting_orders"] = m.get("acceptingOrders")
    return detail


@ttl_cache(seconds=60)
async def price_history(
    token_id: str, interval: str = "1d", fidelity: int = 60
) -> list[dict[str, Any]]:
    """Historical probability for one outcome token.

    `token_id` comes from the `token_id` field of an outcome in market_detail.
    `fidelity` is the bucket size in minutes.
    """
    payload = await fetch_json(
        f"{CLOB}/prices-history",
        params={"market": token_id, "interval": interval, "fidelity": fidelity},
        source="Polymarket CLOB",
    )
    points = (payload or {}).get("history") or []
    return [
        {
            "time": iso(int(p["t"]) * 1000),
            "price": _f(p.get("p")),
            "implied_probability_pct": None if _f(p.get("p")) is None else round(_f(p["p"]) * 100, 2),
        }
        for p in points
        if p.get("t") is not None
    ]


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
