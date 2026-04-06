"""Tests for StdioConnection against mock_mcp_server."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from strip_mcp.connection.stdio import StdioConnection
from strip_mcp.errors import ServerStartError, ToolTimeoutError

MOCK = [sys.executable, str(Path(__file__).parent / "mock_mcp_server.py")]


@pytest.mark.asyncio
async def test_initialize_and_list_tools() -> None:
    conn = StdioConnection(MOCK, "mock")
    try:
        result = await conn.initialize()
        assert "protocolVersion" in result or "serverInfo" in result
        tools = await conn.list_tools()
        assert len(tools) == 20  # default
        assert all("name" in t for t in tools)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_call_tool() -> None:
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
