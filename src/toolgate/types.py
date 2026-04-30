"""Public data types for toolgate."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolBrief:
    """Stage 1 output for a single tool."""

    name: str               # namespaced: "playwright__browser_navigate"
    description: str        # from MCP or override
    server_id: str          # "playwright"
    requires_params: bool   # True if inputSchema has non-empty properties
    full_schema: dict[str, Any] | None = field(default=None)
    # populated only if server is staged=False


@dataclass
class ToolSchema:
    """Stage 2 output for a single tool."""

    name: str                       # namespaced
    input_schema: dict[str, Any]    # full inputSchema from MCP


@dataclass
class ToolResult:
    """Stage 3 output."""

    name: str
    content: list[dict[str, Any]]   # MCP content array (text, image, etc.)
    is_error: bool
