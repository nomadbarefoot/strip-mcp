"""strip-mcp: MCP middleware with staged tool delivery."""

from .core import StripMCP
from .errors import (
    SchemaFetchError,
    ServerCrashedError,
    ServerStartError,
    StripError,
    ToolCollisionError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolTimeoutError,
)
from .sync import SyncStripMCP
from .types import ToolBrief, ToolResult, ToolSchema

__all__ = [
    "StripMCP",
    "SyncStripMCP",
    # errors
    "StripError",
    "ServerStartError",
    "ServerCrashedError",
    "ToolNotFoundError",
    "ToolCollisionError",
    "ToolExecutionError",
    "ToolTimeoutError",
    "SchemaFetchError",
    # types
    "ToolBrief",
    "ToolSchema",
    "ToolResult",
]
