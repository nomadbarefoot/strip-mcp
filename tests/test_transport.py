"""Stdio MCP transport and ServerHandle."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from strip_mcp.connection.stdio import StdioConnection
from strip_mcp.errors import ServerStartError, ToolExecutionError, ToolTimeoutError
from strip_mcp.server import ServerHandle, _requires_params

MOCK = [sys.executable, str(Path(__file__).parent / "mock_mcp_server.py")]
MOCK_FAIL = MOCK + ["--fail", "tool_1"]


# ── _requires_params ──────────────────────────────────────────────────────────


def test_requires_params_empty_schema() -> None:
    assert _requires_params({}) is False
    assert _requires_params({"type": "object", "properties": {}}) is False


def test_requires_params_with_properties() -> None:
    assert _requires_params(
        {"type": "object", "properties": {"url": {"type": "string"}}}
    ) is True


def test_requires_params_required_empty_but_has_props() -> None:
    schema = {"type": "object", "properties": {"opt": {"type": "string"}}, "required": []}
    assert _requires_params(schema) is True


# ── StdioConnection ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_initialize_and_list_tools() -> None:
    conn = StdioConnection(MOCK, "mock")
    try:
        result = await conn.initialize()
        assert "protocolVersion" in result or "serverInfo" in result
        tools = await conn.list_tools()
        assert len(tools) == 20
        assert all("name" in t for t in tools)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_call_tool_stdio() -> None:
    conn = StdioConnection(MOCK, "mock")
    try:
        await conn.initialize()
        result = await conn.call_tool("tool_1", {"arg_a": "hello"}, timeout=5.0)
        assert result["isError"] is False
        assert result["content"][0]["text"] == "Called tool_1 successfully"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_server_start_error() -> None:
    conn = StdioConnection(["nonexistent_binary_xyz"], "bad")
    with pytest.raises(ServerStartError):
        await conn.initialize()


@pytest.mark.asyncio
async def test_tool_timeout() -> None:
    conn = StdioConnection(MOCK + ["--latency", "2.0"], "slow")
    try:
        await conn.initialize()
        with pytest.raises(ToolTimeoutError):
            await conn.call_tool("tool_1", {}, timeout=0.1)
    finally:
        await conn.close()


# ── ServerHandle ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_and_tool_briefs() -> None:
    handle = ServerHandle("mock", command=MOCK, staged=True)
    try:
        await handle.start()
        briefs = handle.tool_briefs()
        assert len(briefs) == 20
        no_param = [b for b in briefs if not b.requires_params]
        assert len(no_param) == 4
        param_tools = [b for b in briefs if b.requires_params]
        assert len(param_tools) == 16
        assert all(b.name.startswith("mock__") for b in briefs)
        assert all(b.full_schema is None for b in briefs)
    finally:
        await handle.stop()


@pytest.mark.asyncio
async def test_staged_false_includes_full_schema() -> None:
    handle = ServerHandle("mock", command=MOCK, staged=False)
    try:
        await handle.start()
        briefs = handle.tool_briefs()
        for b in briefs:
            assert b.full_schema is not None
    finally:
        await handle.stop()


@pytest.mark.asyncio
async def test_description_override() -> None:
    handle = ServerHandle(
        "mock",
        command=MOCK,
        description_overrides={"tool_1": "Custom description"},
    )
    try:
        await handle.start()
        briefs = handle.tool_briefs()
        t1 = next(b for b in briefs if b.name == "mock__tool_1")
        assert t1.description == "Custom description"
    finally:
        await handle.stop()


@pytest.mark.asyncio
async def test_call_tool_success() -> None:
    handle = ServerHandle("mock", command=MOCK)
    try:
        await handle.start()
        result = await handle.call_tool("mock__tool_1", {"arg_a": "x"})
        assert result.is_error is False
    finally:
        await handle.stop()


@pytest.mark.asyncio
async def test_call_tool_error_raises() -> None:
    handle = ServerHandle("mock", command=MOCK_FAIL)
    try:
        await handle.start()
        with pytest.raises(ToolExecutionError):
            await handle.call_tool("mock__tool_1", {})
    finally:
        await handle.stop()


@pytest.mark.asyncio
async def test_namespace_false() -> None:
    handle = ServerHandle("mock", command=MOCK, namespace=False)
    try:
        await handle.start()
        briefs = handle.tool_briefs()
        assert all(not b.name.startswith("mock__") for b in briefs)
    finally:
        await handle.stop()


@pytest.mark.asyncio
async def test_server_refresh() -> None:
    handle = ServerHandle("mock", command=MOCK)
    try:
        await handle.start()
        original = handle.tool_briefs()
        await handle.refresh()
        refreshed = handle.tool_briefs()
        assert [b.name for b in original] == [b.name for b in refreshed]
    finally:
        await handle.stop()
