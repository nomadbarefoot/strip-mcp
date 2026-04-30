"""ProxyServer unit tests.

Uses StdioConnection as a JSON-RPC client against a live proxy subprocess,
which in turn spawns the mock MCP server as its upstream.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import pytest

from toolgate.connection.stdio import StdioConnection

PYTHON = sys.executable
MOCK_SERVER = str(Path(__file__).parent / "mock_mcp_server.py")
PROXY_MODULE = "toolgate.cli"

_TIMEOUT = 10.0


def _make_proxy_config(mock_args: list[str] | None = None) -> str:
    """Write a temp ProxyConfig JSON and return its path."""
    cmd = [PYTHON, MOCK_SERVER] + (mock_args or [])
    data = {
        "version": 1,
        "servers": {
            "mock": {"command": cmd}
        },
    }
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, prefix="strip_proxy_test_"
    )
    json.dump(data, tmp)
    tmp.flush()
    return tmp.name


def _proxy_cmd(config_path: str) -> list[str]:
    return [PYTHON, "-m", PROXY_MODULE, "proxy", "--config", config_path]


async def _get_conn(config_path: str) -> StdioConnection:
    conn = StdioConnection(_proxy_cmd(config_path), "proxy-test")
    await conn.initialize()
    return conn


# ── initialize ────────────────────────────────────────────────────────────────

async def test_initialize_returns_immediately() -> None:
    """Proxy must respond to initialize before upstreams are ready."""
    config = _make_proxy_config()
    conn = StdioConnection(_proxy_cmd(config), "proxy-test")
    result = await asyncio.wait_for(conn.initialize(), timeout=5.0)
    assert result.get("serverInfo", {}).get("name") == "toolgate-proxy"
    await conn.close()


# ── tools/list (Stage 1) ──────────────────────────────────────────────────────

async def test_tools_list_includes_meta_tool() -> None:
    config = _make_proxy_config()
    conn = await _get_conn(config)
    try:
        tools = await conn.list_tools()
        names = {t["name"] for t in tools}
        assert "__toolgate__get_schema" in names
    finally:
        await conn.close()


async def test_tools_list_stubs_have_empty_properties() -> None:
    """All upstream tools must have stub schemas (empty properties, additionalProperties: true)."""
    config = _make_proxy_config(["--tools", "5"])
    conn = await _get_conn(config)
    try:
        tools = await conn.list_tools()
        upstream = [t for t in tools if t["name"] != "__toolgate__get_schema"]
        assert len(upstream) == 5
        for t in upstream:
            schema = t.get("inputSchema", {})
            assert schema.get("properties") == {}, f"{t['name']} has non-empty stub"
            assert schema.get("additionalProperties") is True
    finally:
        await conn.close()


async def test_tools_list_names_are_namespaced() -> None:
    """Tools from server_id 'mock' must be prefixed mock__."""
    config = _make_proxy_config(["--tools", "3"])
    conn = await _get_conn(config)
    try:
        tools = await conn.list_tools()
        upstream = [t for t in tools if t["name"] != "__toolgate__get_schema"]
        for t in upstream:
            assert t["name"].startswith("mock__"), f"Expected namespace: {t['name']}"
    finally:
        await conn.close()


async def test_tools_list_param_tools_include_hint() -> None:
    """Tools that require params should mention __toolgate__get_schema in description."""
    config = _make_proxy_config(["--tools", "5"])
    conn = await _get_conn(config)
    try:
        tools = await conn.list_tools()
        upstream = [t for t in tools if t["name"] != "__toolgate__get_schema"]
        param_tools = [t for t in upstream if "no params" not in t["description"]]
        for t in param_tools:
            assert "__toolgate__get_schema" in t["description"], (
                f"{t['name']} missing schema hint: {t['description']}"
            )
    finally:
        await conn.close()


async def test_meta_tool_has_full_schema() -> None:
    """__toolgate__get_schema must have a real inputSchema with tool_name property."""
    config = _make_proxy_config()
    conn = await _get_conn(config)
    try:
        tools = await conn.list_tools()
        meta = next(t for t in tools if t["name"] == "__toolgate__get_schema")
        props = meta["inputSchema"].get("properties", {})
        assert "tool_name" in props
        assert meta["inputSchema"].get("required") == ["tool_name"]
    finally:
        await conn.close()


# ── __toolgate__get_schema (Stage 2) ─────────────────────────────────────────────

async def test_get_schema_returns_full_schema() -> None:
    """Stage 2: get_schema for a param tool returns non-empty properties."""
    config = _make_proxy_config(["--tools", "5"])
    conn = await _get_conn(config)
    try:
        tools = await conn.list_tools()
        # mock tool_1 has arg_a and arg_b
        target = next(t["name"] for t in tools if t["name"].endswith("__tool_1"))
        result = await conn.call_tool("__toolgate__get_schema", {"tool_name": target}, timeout=_TIMEOUT)
        schema_text = result["content"][0]["text"]
        schema = json.loads(schema_text)
        assert "arg_a" in schema.get("properties", {}), f"Expected arg_a in schema: {schema}"
    finally:
        await conn.close()


async def test_get_schema_for_no_param_tool() -> None:
    """Stage 2: get_schema for a no-param tool returns empty properties."""
    config = _make_proxy_config(["--tools", "5"])
    conn = await _get_conn(config)
    try:
        tools = await conn.list_tools()
        # mock tool_0 has no params (i % 5 == 0)
        target = next(t["name"] for t in tools if t["name"].endswith("__tool_0"))
        result = await conn.call_tool("__toolgate__get_schema", {"tool_name": target}, timeout=_TIMEOUT)
        schema_text = result["content"][0]["text"]
        schema = json.loads(schema_text)
        assert schema.get("properties") == {}
    finally:
        await conn.close()


async def test_get_schema_unknown_tool_returns_error_content() -> None:
    """Unknown tool_name returns isError result, not a JSON-RPC protocol error."""
    config = _make_proxy_config()
    conn = await _get_conn(config)
    try:
        result = await conn.call_tool(
            "__toolgate__get_schema", {"tool_name": "nonexistent__tool"}, timeout=_TIMEOUT
        )
        assert result.get("isError") is True
        assert result["content"]
    finally:
        await conn.close()


async def test_get_schema_missing_tool_name_returns_error() -> None:
    """Calling __toolgate__get_schema with no tool_name returns JSON-RPC error."""
    config = _make_proxy_config()
    conn = await _get_conn(config)
    try:
        with pytest.raises(Exception):
            await conn.call_tool("__toolgate__get_schema", {}, timeout=_TIMEOUT)
    finally:
        await conn.close()


# ── tools/call (Stage 3) ──────────────────────────────────────────────────────

async def test_call_tool_routes_upstream() -> None:
    """Stage 3: calling an upstream tool returns its result."""
    config = _make_proxy_config(["--tools", "3"])
    conn = await _get_conn(config)
    try:
        tools = await conn.list_tools()
        target = next(t["name"] for t in tools if t["name"] != "__toolgate__get_schema")
        result = await conn.call_tool(target, {}, timeout=_TIMEOUT)
        assert result["content"]
        assert result.get("isError") is not True
    finally:
        await conn.close()


async def test_call_tool_execution_error_maps_to_is_error() -> None:
    """ToolExecutionError from upstream → isError:true MCP result, NOT JSON-RPC error."""
    config = _make_proxy_config(["--tools", "3", "--fail", "tool_1"])
    conn = await _get_conn(config)
    try:
        tools = await conn.list_tools()
        target = next(t["name"] for t in tools if t["name"].endswith("__tool_1"))
        result = await conn.call_tool(target, {"arg_a": "test"}, timeout=_TIMEOUT)
        # Must be a valid MCP result with isError flag, not a raised exception
        assert result.get("isError") is True
        assert result["content"]
    finally:
        await conn.close()


async def test_call_unknown_tool_returns_rpc_error() -> None:
    """Calling a completely unknown tool returns JSON-RPC error (not isError result)."""
    config = _make_proxy_config()
    conn = await _get_conn(config)
    try:
        with pytest.raises(Exception):
            await conn.call_tool("totally__unknown__tool", {}, timeout=_TIMEOUT)
    finally:
        await conn.close()


async def test_tools_call_invalid_params_returns_invalid_params_error() -> None:
    """Malformed tools/call params should return deterministic -32602 errors."""
    config = _make_proxy_config()
    conn = await _get_conn(config)
    try:
        with pytest.raises(Exception) as exc:
            await conn._rpc("tools/call", [])  # type: ignore[arg-type]
        assert "RPC error -32602" in str(exc.value)

        with pytest.raises(Exception) as exc:
            await conn._rpc("tools/call", {"name": "mock__tool_1", "arguments": "bad"})  # type: ignore[arg-type]
        assert "RPC error -32602" in str(exc.value)
    finally:
        await conn.close()


# ── concurrency ───────────────────────────────────────────────────────────────

async def test_concurrent_tool_calls_complete() -> None:
    """Multiple in-flight tool calls must all complete without serialization issues."""
    config = _make_proxy_config(["--tools", "5"])
    conn = await _get_conn(config)
    try:
        tools = await conn.list_tools()
        targets = [t["name"] for t in tools if t["name"] != "__toolgate__get_schema"][:3]

        results = await asyncio.gather(
            *[conn.call_tool(name, {}, timeout=_TIMEOUT) for name in targets]
        )
        assert len(results) == len(targets)
        for r in results:
            assert r["content"]
    finally:
        await conn.close()


async def test_high_concurrency_burst_remains_usable() -> None:
    """A larger burst should complete and leave the session usable."""
    config = _make_proxy_config(["--tools", "5"])
    conn = await _get_conn(config)
    try:
        tools = await conn.list_tools()
        target = next(t["name"] for t in tools if t["name"] != "__toolgate__get_schema")
        results = await asyncio.gather(
            *[conn.call_tool(target, {}, timeout=_TIMEOUT) for _ in range(100)]
        )
        assert len(results) == 100
        assert all(r["content"] for r in results)

        follow_up = await conn.call_tool(target, {}, timeout=_TIMEOUT)
        assert follow_up["content"]
    finally:
        await conn.close()


# ── graceful shutdown ─────────────────────────────────────────────────────────

async def test_proxy_handles_stdin_eof() -> None:
    """Closing stdin (EOF) must cause proxy to shut down cleanly (no hung process)."""
    config = _make_proxy_config()
    conn = StdioConnection(_proxy_cmd(config), "proxy-eof-test")
    await conn.initialize()
    await conn.close()
    # If we get here without hanging, the proxy exited cleanly
