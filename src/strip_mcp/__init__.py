"""strip-mcp: MCP middleware with staged tool delivery."""

from .core import StripMCP
from .node_discovery import (
    DEFAULT_NODE_MCP_REGISTRY,
    DiscoveredNodeServer,
    discover_node_mcp_servers,
)
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
    "DEFAULT_NODE_MCP_REGISTRY",
    "DiscoveredNodeServer",
    "discover_node_mcp_servers",
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
