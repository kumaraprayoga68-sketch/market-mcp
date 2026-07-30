"""Server wiring: every tool is registered, documented and callable."""

import json

import pytest

from market_mcp.server import build_server
from market_mcp.tools import MODULES

EXPECTED_TOOLS = {
    "get_price", "get_prices", "search_symbol", "market_snapshot",
    "technical_analysis", "multi_timeframe_analysis", "candlestick_patterns",
    "crypto_screener", "crypto_top_movers", "stock_screener", "technical_scan",
    "list_universes",
    "prediction_markets", "prediction_search", "prediction_market_detail",
    "prediction_price_history",
    "list_strategies", "backtest_strategy", "compare_strategies",
    "walk_forward_backtest",
}


@pytest.fixture(scope="module")
def server():
    return build_server()


async def test_all_expected_tools_are_registered(server):
    names = {t.name for t in await server.list_tools()}
    assert names == EXPECTED_TOOLS


async def test_every_tool_has_a_description(server):
    for tool in await server.list_tools():
        assert tool.description, f"{tool.name} has no description"
        assert len(tool.description) > 40, f"{tool.name} description is too thin"


async def test_every_tool_exposes_an_input_schema(server):
    for tool in await server.list_tools():
        schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", {})
        assert schema.get("type") == "object", tool.name


def test_every_tool_module_registers_something():
    for module in MODULES:
        assert hasattr(module, "register")


def unwrap(result):
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError(f"no payload in {result!r}")


async def test_offline_tools_work_without_network(server):
    """list_strategies and list_universes read local state only."""
    strategies = unwrap(await server.call_tool("list_strategies", {}))
    assert strategies["ok"] is True
    assert strategies["data"]["count"] >= 9

    universes = unwrap(await server.call_tool("list_universes", {}))
    assert universes["ok"] is True
    assert {u["name"] for u in universes["data"]["universes"]} == {"idx", "us"}


async def test_bad_arguments_return_an_error_envelope_not_an_exception(server):
    """The MCP boundary must never surface a traceback to the model."""
    result = unwrap(
        await server.call_tool(
            "backtest_strategy",
            {"symbol": "BTC", "strategy": "no_such_strategy", "market": "crypto"},
        )
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "bad_input"
    assert result["error"]["retryable"] is False
    assert "no_such_strategy" in result["error"]["message"]


async def test_server_carries_usage_instructions(server):
    assert "not financial advice" in server.instructions.lower()
    assert "polymarket" in server.instructions.lower()
