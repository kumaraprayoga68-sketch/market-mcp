"""Host failover for geo-blocked venues.

Binance answers US cloud regions — GitHub Actions runners and Vercel functions
among them — with HTTP 451. The data mirror is reachable from those regions, so
a 451 must move to the next host instead of failing the call. Verified against
a real US region: api.binance.com returned 451 while data-api.binance.vision
returned 200.
"""

import pytest

from market_mcp.core.errors import RETRYABLE, ToolError
from market_mcp.providers import binance


@pytest.fixture(autouse=True)
def reset_preferred_host():
    binance._preferred_host = None
    yield
    binance._preferred_host = None


def fake_fetch(behaviour):
    """Build a fetch_json stand-in driven by {host_substring: outcome}."""
    calls: list[str] = []

    async def _fetch(url, *, params=None, timeout=None, attempts=3, source="upstream"):
        calls.append(url)
        for marker, outcome in behaviour.items():
            if marker in url:
                if isinstance(outcome, ToolError):
                    raise outcome
                return outcome
        raise AssertionError(f"unexpected host: {url}")

    return _fetch, calls


async def test_geo_block_falls_over_to_the_mirror(monkeypatch):
    fetch, calls = fake_fetch(
        {
            "api.binance.com": ToolError("geo_blocked", "region refused"),
            "data-api.binance.vision": {"ok": True},
        }
    )
    monkeypatch.setattr(binance, "fetch_json", fetch)

    assert await binance._get("/api/v3/ping") == {"ok": True}
    assert len(calls) == 2
    assert "api.binance.com" in calls[0]
    assert "data-api.binance.vision" in calls[1]


async def test_a_bad_symbol_does_not_waste_a_call_on_the_mirror(monkeypatch):
    """The mirror would reject an invalid symbol identically."""
    fetch, calls = fake_fetch(
        {"api.binance.com": ToolError("bad_input", "Invalid symbol.")}
    )
    monkeypatch.setattr(binance, "fetch_json", fetch)

    with pytest.raises(ToolError) as excinfo:
        await binance._get("/api/v3/klines")
    assert excinfo.value.code == "bad_input"
    assert len(calls) == 1


async def test_not_found_does_not_fail_over(monkeypatch):
    fetch, calls = fake_fetch({"api.binance.com": ToolError("not_found", "nope")})
    monkeypatch.setattr(binance, "fetch_json", fetch)

    with pytest.raises(ToolError):
        await binance._get("/api/v3/klines")
    assert len(calls) == 1


async def test_the_working_host_is_remembered(monkeypatch):
    """After one 451 the mirror is tried first, so later calls skip the block."""
    fetch, calls = fake_fetch(
        {
            "api.binance.com": ToolError("geo_blocked", "region refused"),
            "data-api.binance.vision": {"ok": True},
        }
    )
    monkeypatch.setattr(binance, "fetch_json", fetch)

    await binance._get("/api/v3/ping")
    calls.clear()
    await binance._get("/api/v3/ping")

    assert len(calls) == 1
    assert "data-api.binance.vision" in calls[0]


async def test_every_host_failing_raises_the_last_error(monkeypatch):
    fetch, calls = fake_fetch(
        {
            "api.binance.com": ToolError("geo_blocked", "region refused"),
            "data-api.binance.vision": ToolError("upstream_error", "mirror down"),
        }
    )
    monkeypatch.setattr(binance, "fetch_json", fetch)

    with pytest.raises(ToolError) as excinfo:
        await binance._get("/api/v3/ping")
    assert excinfo.value.code == "upstream_error"
    assert len(calls) == len(binance.HOSTS)


async def test_timeouts_and_rate_limits_also_fail_over(monkeypatch):
    for code in ("timeout", "rate_limited", "upstream_error"):
        binance._preferred_host = None
        fetch, calls = fake_fetch(
            {
                "api.binance.com": ToolError(code, "x"),
                "data-api.binance.vision": {"ok": True},
            }
        )
        monkeypatch.setattr(binance, "fetch_json", fetch)
        assert await binance._get("/api/v3/ping") == {"ok": True}, code
        assert len(calls) == 2, code


def test_geo_blocked_is_not_advertised_as_retryable():
    """Retrying the same region cannot help, so the envelope must not suggest it."""
    assert "geo_blocked" not in RETRYABLE
    assert ToolError("geo_blocked", "x").retryable is False
