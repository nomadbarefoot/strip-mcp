"""ServerHandle — per-server manager."""

from __future__ import annotations

import logging
from typing import Any

from .connection.base import MCPConnection
from .connection.stdio import StdioConnection
from .errors import ServerStartError, ToolExecutionError
from .types import ToolBrief, ToolResult

logger = logging.getLogger(__name__)


def _requires_params(input_schema: dict[str, Any]) -> bool:
    """Return True if a tool has non-empty properties (LLM may need the schema).

    Decision #5: Only truly empty schemas ({}  or {"type": "object", "properties": {}})
    get requires_params=False.
    """
    props = input_schema.get("properties", {})
    return bool(props)


class ServerHandle:
    """Owns one MCP server connection, caches schemas, produces ToolBrief list."""

    def __init__(
        self,
        server_id: str,
        *,
        command: list[str] | None = None,
        url: str | None = None,
        staged: bool = True,
        namespace: bool = True,
        timeout: float = 30.0,
        description_overrides: dict[str, str] | None = None,
    ) -> None:
        if command is None and url is None:
            raise ValueError(f"Server {server_id!r}: provide command= or url=")
        if command is not None and url is not None:
            raise ValueError(f"Server {server_id!r}: provide command= or url=, not both")

        self.server_id = server_id
        self.staged = staged
        self.namespace = namespace
        self.timeout = timeout
        self._overrides: dict[str, str] = description_overrides or {}

        if command is not None:
            self._conn: MCPConnection = StdioConnection(command, server_id)
        else:
            # Phase 2: HTTP
            from .connection.http import HTTPConnection  # noqa: PLC0415
            self._conn = HTTPConnection(url, server_id)  # type: ignore[arg-type]

        # populated after start()
        self._schema_cache: dict[str, dict[str, Any]] = {}  # raw_name → inputSchema
        self._raw_tools: list[dict[str, Any]] = []
        self._healthy = False

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize connection and cache schemas."""
        await self._conn.initialize()
        await self._load_tools()
        self._healthy = True
        logger.info("Server %r started with %d tools", self.server_id, len(self._raw_tools))

    async def stop(self) -> None:
        self._healthy = False
        await self._conn.close()

    async def refresh(self) -> None:
        """Reload tool list and schema cache from server."""
        self._schema_cache.clear()
        self._raw_tools.clear()
        await self._load_tools()
        logger.debug("Server %r refreshed", self.server_id)

    # ── public API ─────────────────────────────────────────────────────────

    def tool_briefs(self) -> list[ToolBrief]:
        """Return Stage 1 output for all tools on this server."""
        briefs = []
        for raw in self._raw_tools:
            raw_name: str = raw["name"]
            namespaced = self._namespaced(raw_name)
            description = self._overrides.get(raw_name, raw.get("description", ""))
            input_schema = raw.get("inputSchema", {})

            if self.staged:
                brief = ToolBrief(
                    name=namespaced,
                    description=description,
                    server_id=self.server_id,
                    requires_params=_requires_params(input_schema),
                )
            else:
                # staged=False: include full schema in Stage 1
                brief = ToolBrief(
                    name=namespaced,
                    description=description,
                    server_id=self.server_id,
                    requires_params=_requires_params(input_schema),
                    full_schema=input_schema,
                )
            briefs.append(brief)
        return briefs

    def get_schema(self, namespaced_name: str) -> dict[str, Any]:
        """Return full inputSchema for a namespaced tool name."""
        raw_name = self._raw_name(namespaced_name)
        return self._schema_cache[raw_name]

    def has_tool(self, namespaced_name: str) -> bool:
        raw_name = self._raw_name(namespaced_name)
        return raw_name in self._schema_cache

    async def call_tool(
        self, namespaced_name: str, arguments: dict[str, Any]
    ) -> ToolResult:
        """Execute a tool on this server."""
        raw_name = self._raw_name(namespaced_name)
        raw_result = await self._conn.call_tool(raw_name, arguments, self.timeout)

        content = raw_result.get("content", [])
        is_error = bool(raw_result.get("isError", False))

        result = ToolResult(name=namespaced_name, content=content, is_error=is_error)
        if is_error:
            raise ToolExecutionError(namespaced_name, content)
        return result

    @property
    def healthy(self) -> bool:
        return self._healthy

    # ── helpers ────────────────────────────────────────────────────────────

    def _namespaced(self, raw_name: str) -> str:
        if self.namespace:
            return f"{self.server_id}__{raw_name}"
        return raw_name

    def _raw_name(self, namespaced: str) -> str:
        if self.namespace:
            prefix = f"{self.server_id}__"
            if namespaced.startswith(prefix):
                return namespaced[len(prefix):]
        return namespaced

    async def _load_tools(self) -> None:
        raw_tools = await self._conn.list_tools()
        self._raw_tools = raw_tools
        self._schema_cache = {
            t["name"]: t.get("inputSchema", {}) for t in raw_tools
        }
