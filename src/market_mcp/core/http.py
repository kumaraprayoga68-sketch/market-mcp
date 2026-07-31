"""Shared HTTP client with bounded concurrency and retry.

Every provider goes through `fetch_json`. Keeping one client alive reuses
connections, and one global semaphore stops a 200-symbol screener from opening
200 sockets against the same host.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from .errors import ToolError

# Public market endpoints reject the default httpx UA often enough to be worth
# spoofing a normal browser.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

DEFAULT_TIMEOUT = 15.0
MAX_CONCURRENCY = 8
MAX_ATTEMPTS = 3

_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()
_semaphore: asyncio.Semaphore | None = None


async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        async with _client_lock:
            if _client is None or _client.is_closed:
                _client = httpx.AsyncClient(
                    headers=_HEADERS,
                    timeout=DEFAULT_TIMEOUT,
                    follow_redirects=True,
                    limits=httpx.Limits(max_connections=MAX_CONCURRENCY * 2),
                )
    return _client


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    return _semaphore


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def fetch_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float | None = None,
    attempts: int = MAX_ATTEMPTS,
    source: str = "upstream",
) -> Any:
    """GET `url` and parse JSON, retrying transient failures with backoff.

    Raises ToolError with a code the model can act on: `rate_limited` and
    `upstream_error` are worth retrying later, `not_found` and `bad_input`
    are not.
    """
    client = await get_client()
    sem = _get_semaphore()
    last: str = "unknown error"

    for attempt in range(1, attempts + 1):
        try:
            async with sem:
                resp = await client.get(url, params=params, timeout=timeout or DEFAULT_TIMEOUT)
        except httpx.TimeoutException:
            last = f"{source} timed out after {timeout or DEFAULT_TIMEOUT}s"
            if attempt == attempts:
                raise ToolError("timeout", last)
        except httpx.HTTPError as e:
            last = f"{source} connection failed: {type(e).__name__}"
            if attempt == attempts:
                raise ToolError("upstream_error", last)
        else:
            if resp.status_code == 429:
                last = f"{source} rate-limited this client"
                if attempt == attempts:
                    raise ToolError("rate_limited", last)
            elif resp.status_code == 404:
                raise ToolError("not_found", f"{source} has no data at {resp.request.url.path}")
            elif resp.status_code >= 500:
                last = f"{source} returned HTTP {resp.status_code}"
                if attempt == attempts:
                    raise ToolError("upstream_error", last)
            elif resp.status_code == 451:
                # "Unavailable for legal reasons" — the venue is refusing this
                # region, not this request. Retrying the same host is pointless,
                # but a different host may well be reachable, so this gets its
                # own code for callers that keep mirrors.
                raise ToolError(
                    "geo_blocked",
                    f"{source} refuses requests from this region (HTTP 451)",
                )
            elif resp.status_code >= 400:
                # Other 4xx means we sent something wrong; retrying the
                # identical request cannot help.
                raise ToolError(
                    "bad_input",
                    f"{source} rejected the request (HTTP {resp.status_code}): "
                    f"{resp.text[:200]}",
                )
            else:
                try:
                    return resp.json()
                except ValueError:
                    raise ToolError(
                        "upstream_error", f"{source} returned a non-JSON body"
                    ) from None

        # Exponential backoff with jitter so parallel callers do not resynchronise.
        await asyncio.sleep(min(2 ** (attempt - 1) * 0.5, 4.0) + random.uniform(0, 0.3))

    raise ToolError("upstream_error", last)
