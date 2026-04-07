"""StripMCP orchestrator, registry, and error types."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from strip_mcp import StripMCP
from strip_mcp.errors import (
    SchemaFetchError,
    ServerCrashedError,
    ServerStartError,
    StripError,
    ToolCollisionError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolTimeoutError,
)
from strip_mcp.registry import ToolRegistry

MOCK = [sys.executable, str(Path(__file__).parent / "mock_mcp_server.py")]
MOCK_5 = MOCK + ["--tools", "5"]


# ── errors ───────────────────────────────────────────────────────────────────


def test_error_hierarchy() -> None:
    for cls in (
        ServerStartError,
        ServerCrashedError,
        ToolNotFoundError,
        ToolCollisionError,
        ToolExecutionError,
        ToolTimeoutError,
        SchemaFetchError,
    ):
        assert issubclass(cls, StripError)


def test_tool_not_found_message_with_suggestion() -> None:
    exc = ToolNotFoundError("playwright__naviage", "playwright__navigate")
    assert "playwright__naviage" in str(exc)
    assert "playwright__navigate" in str(exc)


def test_tool_not_found_no_suggestion() -> None:
    exc = ToolNotFoundError("xyz")
    assert "xyz" in str(exc)
    assert "Did you mean" not in str(exc)


def test_tool_timeout_message() -> None:
    exc = ToolTimeoutError("my_tool", 30.0)
    assert "my_tool" in str(exc)
    assert "30" in str(exc)


def test_tool_execution_error() -> None:
    exc = ToolExecutionError("my_tool", {"message": "oops"})
    assert "my_tool" in str(exc)


# ── registry ─────────────────────────────────────────────────────────────────


def test_register_and_resolve() -> None:
    reg = ToolRegistry()
    reg.register("playwright__navigate", "playwright")
    assert reg.resolve("playwright__navigate") == "playwright"


def test_collision_raises() -> None:
    reg = ToolRegistry()
    reg.register("tool_a", "server1")
    with pytest.raises(ToolCollisionError):
        reg.register("tool_a", "server2")


def test_not_found_raises() -> None:
    reg = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        reg.resolve("nonexistent")


def test_not_found_suggestion() -> None:
    reg = ToolRegistry()
    reg.register("playwright__navigate", "playwright")
    exc = pytest.raises(ToolNotFoundError, reg.resolve, "playwright__naviage")
    assert exc.value.suggestion == "playwright__navigate"


def test_deregister_server() -> None:
    reg = ToolRegistry()
    reg.register("playwright__navigate", "playwright")
    reg.register("git__status", "git")
    reg.deregister_server("playwright")
    with pytest.raises(ToolNotFoundError):
        reg.resolve("playwright__navigate")
    assert reg.resolve("git__status") == "git"


def test_all_names() -> None:
    reg = ToolRegistry()
    reg.register("a", "s1")
    reg.register("b", "s2")
    assert set(reg.all_names()) == {"a", "b"}


# ── StripMCP ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_three_stage_flow() -> None:
    async with StripMCP() as mcp:
        mcp.add_server("s1", command=MOCK_5)
        await mcp.start()

        tools = await mcp.list_tools()
        assert len(tools) == 5
        assert all(t.name.startswith("s1__") for t in tools)

        param_tool = next(t for t in tools if t.requires_params)
        schemas = await mcp.get_schemas([param_tool.name])
        assert len(schemas) == 1
        assert schemas[0].name == param_tool.name
        assert "properties" in schemas[0].input_schema

        result = await mcp.call(param_tool.name, {"arg_a": "hello"})
        assert result.is_error is False


@pytest.mark.asyncio
async def test_list_tools_cached() -> None:
    async with StripMCP() as mcp:
        mcp.add_server("s1", command=MOCK_5)
        await mcp.start()

        t1 = await mcp.list_tools()
        t2 = await mcp.list_tools()
        assert t1 is t2


@pytest.mark.asyncio
async def test_refresh_clears_cache() -> None:
    async with StripMCP() as mcp:
        mcp.add_server("s1", command=MOCK_5)
        await mcp.start()

        t1 = await mcp.list_tools()
        await mcp.refresh()
        t2 = await mcp.list_tools()
        assert t1 is not t2


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
    mock_other = [
        sys.executable,
        "-c",
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


@pytest.mark.asyncio
async def test_restart_after_stop_works() -> None:
    mcp = StripMCP()
    mcp.add_server("s1", command=MOCK_5)

    await mcp.start()
    await mcp.stop()
    await mcp.start()

    tools = await mcp.list_tools()
    assert len(tools) == 5
    await mcp.stop()


@pytest.mark.asyncio
async def test_failed_start_rolls_back_running_servers() -> None:
    mcp = StripMCP()
    mcp.add_server("ok", command=MOCK_5)
    mcp.add_server("bad", command=["nonexistent_binary_xyz"])

    with pytest.raises(ServerStartError):
        await mcp.start()

    assert mcp._servers["ok"].healthy is False
