"""Tests for error types."""

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


def test_hierarchy() -> None:
    for cls in (
        ServerStartError, ServerCrashedError, ToolNotFoundError,
        ToolCollisionError, ToolExecutionError, ToolTimeoutError, SchemaFetchError,
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
