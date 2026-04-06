"""Abstract base for MCP transport connections."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class MCPConnection(ABC):
    """Transport abstraction for MCP protocol."""

    @abstractmethod
    async def initialize(self) -> dict[str, Any]:
        """Run the MCP initialize handshake. Returns server capabilities."""

    @abstractmethod
    async def list_tools(self) -> list[dict[str, Any]]:
        """Run tools/list. Returns raw MCP tool objects."""

    @abstractmethod
    async def call_tool(
        self, name: str, arguments: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        """Run tools/call. Returns raw MCP result."""

    @abstractmethod
    async def close(self) -> None:
        """Shut down the connection cleanly."""
