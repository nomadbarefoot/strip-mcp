"""toolgate: MCP middleware with staged tool delivery."""

from .core import ToolGate
from .catalog import Catalog, CatalogTool
from .collector import CollectionReport, ServerCollectResult, collect_inventory
from .node_discovery import (
    DEFAULT_NODE_MCP_REGISTRY,
    DiscoveredNodeServer,
    discover_node_mcp_servers,
)
from .errors import (
    RemoteRPCError,
    SchemaFetchError,
    ServerCrashedError,
    ServerStartError,
    ToolGateError,
    ToolCollisionError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolTimeoutError,
)
from .sync import SyncToolGate
from .types import ToolBrief, ToolResult, ToolSchema

__all__ = [
    "ToolGate",
    "SyncToolGate",
    "Catalog",
    "CatalogTool",
    "CollectionReport",
    "ServerCollectResult",
    "collect_inventory",
    "DEFAULT_NODE_MCP_REGISTRY",
    "DiscoveredNodeServer",
    "discover_node_mcp_servers",
    # errors
    "ToolGateError",
    "ServerStartError",
    "ServerCrashedError",
    "RemoteRPCError",
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
