"""Tests for ServerHandle."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from strip_mcp.errors import ToolExecutionError
from strip_mcp.server import ServerHandle, _requires_params

MOCK = [sys.executable, str(Path(__file__).parent / "mock_mcp_server.py")]
MOCK_FAIL = MOCK + ["--fail", "tool_1"]


def test_requires_params_empty_schema() -> None:
    assert _requires_params({}) is False
    assert _requires_params({"type": "object", "properties": {}}) is False


def test_requires_params_with_properties() -> None:
    assert _requires_params({"type": "object", "properties": {"url": {"type": "string"}}}) is True


def test_requires_params_required_empty_but_has_props() -> None:
    # required=[] but props non-empty → requires_params=True (decision #5)
    schema = {"type": "object", "properties": {"opt": {"type": "string"}}, "required": []}
    assert _requires_params(schema) is True


@pytest.mark.asyncio
async def test_start_and_tool_briefs() -> None:
    handle = ServerHandle("mock", command=MOCK, staged=True)
    try:
        await handle.start()
        briefs = handle.tool_briefs()
        assert len(briefs) == 20
        # tool_0, tool_5, tool_10, tool_15 are no-param tools (i % 5 == 0)
        no_param = [b for b in briefs if not b.requires_params]
        assert len(no_param) == 4
        param_tools = [b for b in briefs if b.requires_params]
        assert len(param_tools) == 16
        # all names namespaced
        assert all(b.name.startswith("mock__") for b in briefs)
        # staged=True → full_schema is None
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
async def test_refresh() -> None:
    handle = ServerHandle("mock", command=MOCK)
    try:
        await handle.start()
        original = handle.tool_briefs()
        await handle.refresh()
        refreshed = handle.tool_briefs()
        assert [b.name for b in original] == [b.name for b in refreshed]
    finally:
        await handle.stop()
