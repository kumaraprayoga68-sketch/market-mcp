"""Binance public market-data provider — spot klines and 24h tickers.

No API key: these are the unauthenticated `/api/v3` market endpoints. The
binance.vision mirror is tried when the main host is unreachable, which is the
usual failure mode in geo-restricted regions.
"""

from __future__ import annotations

from typing import Any

from ..core.cache import ttl_cache
from ..core.errors import ToolError
from ..core.http import fetch_json
from ..models import Candle

HOSTS = ("https://api.binance.com", "https://data-api.binance.vision")

INTERVALS = {
    "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
}

# Quote assets we treat as "the USD leg" when screening pairs.
STABLE_QUOTES = ("USDT", "USDC", "FDUSD", "BUSD")

# Non-USD-named pegged assets. Anything whose base starts or ends with "USD"
# is caught by the heuristic in `_is_stablecoin` instead of being listed here.
PEGGED_BASES = frozenset({"DAI", "EURI", "EURT", "AEUR", "XUSD", "FRAX", "LUSD"})


# Errors that mean "this host cannot serve us" rather than "this request is
# wrong". Only these are worth retrying against a different host.
_FAILOVER_CODES = frozenset({"geo_blocked", "upstream_error", "timeout", "rate_limited"})

# Once a host answers, keep using it first. api.binance.com returns HTTP 451 to
# whole regions (US cloud providers among them), and without this every single
# call would pay for that rejection before falling through to the mirror.
_preferred_host: str | None = None


async def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    global _preferred_host

    hosts = list(HOSTS)
    if _preferred_host in hosts:
        hosts.remove(_preferred_host)
        hosts.insert(0, _preferred_host)

    last: ToolError | None = None
    for host in hosts:
        try:
            result = await fetch_json(f"{host}{path}", params=params, source="Binance")
        except ToolError as e:
            # A rejected symbol is our fault and the mirror would reject it too.
            if e.code not in _FAILOVER_CODES:
                raise
            last = e
            continue
        _preferred_host = host
        return result

    raise last or ToolError("upstream_error", "Binance unreachable")


def normalize_symbol(symbol: str, default_quote: str = "USDT") -> str:
    """Accept BTC, btc-usdt, BTC/USDT, BTCUSDT -> BTCUSDT."""
    s = symbol.strip().upper().replace("-", "").replace("/", "").replace("_", "")
    if not s:
        raise ValueError("symbol must not be empty")
    # `len(s) > len(q)` matters: bare "BTC" ends with the quote asset "BTC" but
    # has no base left over, so it is a base asset and still needs a quote.
    quotes = (*STABLE_QUOTES, "BTC", "ETH", "BNB", "EUR", "TRY")
    if any(len(s) > len(q) and s.endswith(q) for q in quotes):
        return s
    return s + default_quote


@ttl_cache(seconds=15)
async def ticker_24h(symbol: str | None = None) -> Any:
    """24h rolling stats for one symbol, or every symbol when omitted."""
    if symbol:
        return await _get("/api/v3/ticker/24hr", {"symbol": normalize_symbol(symbol)})
    return await _get("/api/v3/ticker/24hr")


async def price(symbol: str) -> dict[str, Any]:
    """Current price with 24h context."""
    sym = normalize_symbol(symbol)
    t = await ticker_24h(sym)
    if isinstance(t, list):  # defensive: the all-symbols shape
        raise ToolError("not_found", f"Binance has no ticker for '{sym}'")
    return {
        "symbol": t.get("symbol", sym),
        "price": _f(t.get("lastPrice")),
        "change_pct": _f(t.get("priceChangePercent")),
        "change": _f(t.get("priceChange")),
        "high_24h": _f(t.get("highPrice")),
        "low_24h": _f(t.get("lowPrice")),
        "open_24h": _f(t.get("openPrice")),
        "volume_base_24h": _f(t.get("volume")),
        "volume_quote_24h": _f(t.get("quoteVolume")),
        "trades_24h": t.get("count"),
        "weighted_avg_price": _f(t.get("weightedAvgPrice")),
    }


@ttl_cache(seconds=20)
async def candles(symbol: str, interval: str = "1d", limit: int = 500) -> list[Candle]:
    """OHLCV klines, oldest first."""
    if interval not in INTERVALS:
        raise ValueError(
            f"interval '{interval}' is not supported; use one of {sorted(INTERVALS)}"
        )
    limit = max(10, min(int(limit), 1000))
    rows = await _get(
        "/api/v3/klines",
        {"symbol": normalize_symbol(symbol), "interval": interval, "limit": limit},
    )
    if not rows:
        raise ToolError("not_found", f"Binance returned no klines for '{symbol}'")
    return [
        {
            "ts": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]),
        }
        for r in rows
    ]


def _is_stablecoin(base: str) -> bool:
    """True for assets pegged to a fiat currency."""
    return base in PEGGED_BASES or base.startswith("USD") or base.endswith("USD")


async def universe(
    quote: str = "USDT",
    min_quote_volume: float = 5_000_000,
    *,
    exclude_stablecoins: bool = True,
) -> list[dict[str, Any]]:
    """Liquid spot pairs against `quote`, sorted by 24h turnover.

    The volume floor is what makes screener output usable — without it the list
    is dominated by illiquid pairs whose percentage moves are meaningless.

    Stablecoin-to-stablecoin pairs (USDC/USDT and friends) are dropped by
    default: they carry enormous turnover but never move, so they crowd out the
    top of every volume-ranked list while telling you nothing.
    """
    quote = quote.upper()
    rows = await ticker_24h()
    if not isinstance(rows, list):
        raise ToolError("upstream_error", "Binance returned an unexpected ticker shape")

    out = []
    for t in rows:
        sym = t.get("symbol", "")
        if not sym.endswith(quote):
            continue
        base = sym[: -len(quote)]
        # Leveraged tokens (BTCUPUSDT / BTCDOWNUSDT) are derivatives, not spot.
        if base.endswith(("UP", "DOWN", "BULL", "BEAR")):
            continue
        if exclude_stablecoins and _is_stablecoin(base):
            continue
        qv = _f(t.get("quoteVolume")) or 0.0
        if qv < min_quote_volume:
            continue
        out.append(
            {
                "symbol": sym,
                "base": base,
                "price": _f(t.get("lastPrice")),
                "change_pct": _f(t.get("priceChangePercent")),
                "high_24h": _f(t.get("highPrice")),
                "low_24h": _f(t.get("lowPrice")),
                "volume_quote_24h": qv,
                "trades_24h": t.get("count"),
            }
        )
    out.sort(key=lambda r: r["volume_quote_24h"], reverse=True)
    return out


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
