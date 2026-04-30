"""HTTPConnection — Phase 2 stub.

HTTP/SSE MCP transport is not implemented in Phase 1.
Importing this module raises NotImplementedError at instantiation.
"""

from __future__ import annotations

from typing import Any

from .base import MCPConnection


class HTTPConnection(MCPConnection):
    """HTTP/SSE MCP transport. Not implemented — Phase 2."""

    def __init__(self, url: str, server_id: str) -> None:
        raise NotImplementedError(
            "HTTP MCP transport is not available in Phase 1. "
            "Use StdioConnection (command=[...]) instead."
        )

    async def initialize(self) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    async def list_tools(self) -> list[dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    async def call_tool(
        self, name: str, arguments: dict[str, Any], timeout: float
    ) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover
        raise NotImplementedError
