"""Tiny async TTL cache.

Market endpoints are hit repeatedly with identical arguments during a single
conversation (a screener and a technicals call both want BTCUSDT candles).
Caching for a few seconds removes most of that duplication and keeps us well
under the public rate limits.
"""

from __future__ import annotations

import asyncio
import functools
import time
from typing import Any, Awaitable, Callable, TypeVar

_store: dict[str, tuple[float, Any]] = {}
_locks: dict[str, asyncio.Lock] = {}

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def _key(prefix: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    parts = [prefix, *(repr(a) for a in args)]
    parts += [f"{k}={v!r}" for k, v in sorted(kwargs.items())]
    return "|".join(parts)


def ttl_cache(seconds: float) -> Callable[[F], F]:
    """Memoize an async function for `seconds`, de-duplicating concurrent calls."""

    def decorator(fn: F) -> F:
        prefix = f"{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = _key(prefix, args, kwargs)
            now = time.monotonic()

            hit = _store.get(key)
            if hit is not None and hit[0] > now:
                return hit[1]

            # One in-flight call per key: a burst of identical requests waits on
            # the first rather than stampeding the upstream API.
            lock = _locks.setdefault(key, asyncio.Lock())
            async with lock:
                hit = _store.get(key)
                if hit is not None and hit[0] > time.monotonic():
                    return hit[1]
                value = await fn(*args, **kwargs)
                _store[key] = (time.monotonic() + seconds, value)
                return value

        return wrapper  # type: ignore[return-value]

    return decorator


def clear_cache() -> int:
    """Drop every cached entry. Returns how many were removed."""
    n = len(_store)
    _store.clear()
    _locks.clear()
    return n


def cache_stats() -> dict[str, int]:
    now = time.monotonic()
    live = sum(1 for expiry, _ in _store.values() if expiry > now)
    return {"entries": len(_store), "live": live, "expired": len(_store) - live}
