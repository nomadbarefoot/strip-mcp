"""Tests for StripMCP (core orchestrator)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from strip_mcp import StripMCP
from strip_mcp.errors import ToolCollisionError, ToolNotFoundError

MOCK = [sys.executable, str(Path(__file__).parent / "mock_mcp_server.py")]
MOCK_5 = MOCK + ["--tools", "5"]
MOCK_FAIL = MOCK + ["--fail", "tool_1"]


@pytest.mark.asyncio
async def test_full_three_stage_flow() -> None:
    async with StripMCP() as mcp:
        mcp.add_server("s1", command=MOCK_5)
        await mcp.start()

        # Stage 1
        tools = await mcp.list_tools()
        assert len(tools) == 5
        assert all(t.name.startswith("s1__") for t in tools)

        # Stage 2
        param_tool = next(t for t in tools if t.requires_params)
        schemas = await mcp.get_schemas([param_tool.name])
        assert len(schemas) == 1
        assert schemas[0].name == param_tool.name
        assert "properties" in schemas[0].input_schema

        # Stage 3
        result = await mcp.call(param_tool.name, {"arg_a": "hello"})
        assert result.is_error is False


@pytest.mark.asyncio
async def test_list_tools_cached() -> None:
    async with StripMCP() as mcp:
        mcp.add_server("s1", command=MOCK_5)
        await mcp.start()

        t1 = await mcp.list_tools()
        t2 = await mcp.list_tools()
        assert t1 is t2  # same object


@pytest.mark.asyncio
async def test_refresh_clears_cache() -> None:
    async with StripMCP() as mcp:
        mcp.add_server("s1", command=MOCK_5)
        await mcp.start()

        t1 = await mcp.list_tools()
        await mcp.refresh()
        t2 = await mcp.list_tools()
        assert t1 is not t2  # rebuilt


@pytest.mark.asyncio
async def test_refresh_single_server() -> None:
    async with StripMCP() as mcp:
        mcp.add_server("s1", command=MOCK_5)
        mcp.add_server("s2", command=MOCK_5)
        await mcp.start()

        await mcp.refresh("s1")
        tools = await mcp.list_tools()
        assert len(tools) == 10


@pytest.mark.asyncio
async def test_multiple_servers_namespaced() -> None:
    async with StripMCP() as mcp:
        mcp.add_server("alpha", command=MOCK_5)
        mcp.add_server("beta", command=MOCK_5)
        await mcp.start()

        tools = await mcp.list_tools()
        alpha = [t for t in tools if t.name.startswith("alpha__")]
        beta = [t for t in tools if t.name.startswith("beta__")]
        assert len(alpha) == 5
        assert len(beta) == 5


@pytest.mark.asyncio
async def test_staged_false_in_list_tools() -> None:
    async with StripMCP() as mcp:
        mcp.add_server("s1", command=MOCK_5, staged=False)
        await mcp.start()

        tools = await mcp.list_tools()
        assert all(t.full_schema is not None for t in tools)


@pytest.mark.asyncio
async def test_tool_not_found_raises() -> None:
    async with StripMCP() as mcp:
        mcp.add_server("s1", command=MOCK_5)
        await mcp.start()

        with pytest.raises(ToolNotFoundError):
            await mcp.call("s1__nonexistent_tool")


@pytest.mark.asyncio
async def test_direct_execution_no_params() -> None:
    async with StripMCP() as mcp:
        mcp.add_server("s1", command=MOCK_5)
        await mcp.start()

        tools = await mcp.list_tools()
        no_param = next(t for t in tools if not t.requires_params)
        # call with no arguments
        result = await mcp.call(no_param.name)
        assert result.is_error is False


@pytest.mark.asyncio
async def test_namespace_false_collision_raises() -> None:
    async with StripMCP() as mcp:
        mcp.add_server("s1", command=MOCK_5, namespace=False)
        mcp.add_server("s2", command=MOCK_5, namespace=False)

        with pytest.raises(ToolCollisionError):
            await mcp.start()


@pytest.mark.asyncio
async def test_namespace_false_no_collision_works() -> None:
    mock_3 = MOCK + ["--tools", "3"]
    mock_other = [
        sys.executable,
        "-c",
        # a mock server with different tool names
        """
import sys, json
for line in sys.stdin:
    msg = json.loads(line.strip())
    method = msg.get("method")
    rid = msg.get("id")
    if method == "initialize":
        print(json.dumps({"jsonrpc":"2.0","id":rid,"result":{"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"x","version":"0"}}}), flush=True)
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        print(json.dumps({"jsonrpc":"2.0","id":rid,"result":{"tools":[{"name":"unique_tool","description":"unique","inputSchema":{}}]}}), flush=True)
    elif method == "tools/call":
        print(json.dumps({"jsonrpc":"2.0","id":rid,"result":{"content":[{"type":"text","text":"ok"}],"isError":False}}), flush=True)
""",
    ]
    async with StripMCP() as mcp:
        mcp.add_server("s1", command=mock_other, namespace=False)
        await mcp.start()
        tools = await mcp.list_tools()
        assert any(t.name == "unique_tool" for t in tools)


@pytest.mark.asyncio
async def test_list_tools_text() -> None:
    async with StripMCP() as mcp:
        mcp.add_server("s1", command=MOCK_5)
        await mcp.start()

        text = await mcp.list_tools_text()
        assert "s1__tool_0" in text
        assert "[no params]" in text
        assert "[params required]" in text
