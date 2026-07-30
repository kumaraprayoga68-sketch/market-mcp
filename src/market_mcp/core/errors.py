"""Structured result envelope shared by every tool.

Tools never let exceptions escape into the MCP layer. They return a plain dict
so the model can tell the difference between "the query worked and matched
nothing" and "the upstream API is down", and can decide whether a retry is
worth it.
"""

from __future__ import annotations

import functools
import inspect
from typing import Any, Awaitable, Callable, Literal, TypeVar

ErrorCode = Literal[
    "bad_input",
    "not_found",
    "upstream_error",
    "rate_limited",
    "timeout",
    "internal",
]

# Codes where trying the exact same call again may succeed.
RETRYABLE: frozenset[str] = frozenset({"upstream_error", "rate_limited", "timeout"})


class ToolError(Exception):
    """Raised inside providers/tools; converted to an error envelope at the edge."""

    def __init__(self, code: ErrorCode, message: str, *, retryable: bool | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = code in RETRYABLE if retryable is None else retryable


def ok(data: Any) -> dict[str, Any]:
    """Wrap a successful payload."""
    return {"ok": True, "data": data, "error": None}


def err(code: ErrorCode, message: str, *, retryable: bool | None = None) -> dict[str, Any]:
    """Wrap a failure."""
    return {
        "ok": False,
        "data": None,
        "error": {
            "code": code,
            "message": message,
            "retryable": code in RETRYABLE if retryable is None else retryable,
        },
    }


F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def envelope(fn: F) -> F:
    """Wrap an async tool so it always returns an envelope instead of raising.

    A function that already returns an envelope dict is passed through
    untouched, which lets tools compose without double-wrapping.
    """

    if not inspect.iscoroutinefunction(fn):
        raise TypeError(f"@envelope expects an async function, got {fn!r}")

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            result = await fn(*args, **kwargs)
        except ToolError as e:
            return err(e.code, e.message, retryable=e.retryable)
        except ValueError as e:
            return err("bad_input", str(e))
        except Exception as e:  # noqa: BLE001 - the MCP boundary must not leak tracebacks
            return err("internal", f"{type(e).__name__}: {e}")
        if isinstance(result, dict) and "ok" in result and "error" in result:
            return result
        return ok(result)

    return wrapper  # type: ignore[return-value]
