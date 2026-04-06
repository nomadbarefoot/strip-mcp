"""Tests for ToolRegistry."""

import pytest

from strip_mcp.errors import ToolCollisionError, ToolNotFoundError
from strip_mcp.registry import ToolRegistry


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
