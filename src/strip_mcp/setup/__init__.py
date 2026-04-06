"""Setup CLI support modules."""

from .discovery import (
    discover_global_node_mcp_servers,
    discover_installed_mcps,
    resolve_npm_global_root,
)
from .hosts import discover_apps, get_host_adapters
from .models import ApplyResult, DiscoveredApp, DiscoveredMCP, PlannedChange

__all__ = [
    "DiscoveredMCP",
    "DiscoveredApp",
    "PlannedChange",
    "ApplyResult",
    "discover_installed_mcps",
    "discover_global_node_mcp_servers",
    "resolve_npm_global_root",
    "discover_apps",
    "get_host_adapters",
]
