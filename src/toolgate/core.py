"""ToolGate — top-level orchestrator."""

from __future__ import annotations

import logging
from typing import Any

from .errors import SchemaFetchError
from .registry import ToolRegistry
from .server import ServerHandle
from .types import ToolBrief, ToolResult, ToolSchema

logger = logging.getLogger(__name__)


class ToolGate:
    """Async orchestrator for multi-server staged MCP tool access."""

    def __init__(self, default_timeout: float = 30.0) -> None:
        self._default_timeout = default_timeout
        self._servers: dict[str, ServerHandle] = {}
        self._registry = ToolRegistry()
        self._tools_cache: list[ToolBrief] | None = None
        self._started = False

    # ── context manager ────────────────────────────────────────────────────

    async def __aenter__(self) -> "ToolGate":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    # ── server registration ────────────────────────────────────────────────

    def add_server(
        self,
        server_id: str,
        *,
        command: list[str] | None = None,
        url: str | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        staged: bool = True,
        namespace: bool = True,
        timeout: float | None = None,
        description_overrides: dict[str, str] | None = None,
    ) -> None:
        """Register an MCP server. Must be called before start()."""
        if self._started:
            raise RuntimeError("Cannot add_server after start(); call refresh() instead")
        if server_id in self._servers:
            raise ValueError(f"Server {server_id!r} already registered")

        self._servers[server_id] = ServerHandle(
            server_id,
            command=command,
            url=url,
            cwd=cwd,
            env=env,
            staged=staged,
            namespace=namespace,
            timeout=timeout if timeout is not None else self._default_timeout,
            description_overrides=description_overrides,
        )

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start all servers, run handshakes, build registry."""
        if self._started:
            raise RuntimeError("Already started")
        self._tools_cache = None
        self._registry = ToolRegistry()
        started: list[ServerHandle] = []
        try:
            for handle in self._servers.values():
                await handle.start()
                started.append(handle)
                self._register_server_tools(handle)
            self._started = True
        except Exception:
            for handle in reversed(started):
                try:
                    await handle.stop()
                except Exception as exc:
                    logger.warning("Error stopping %r after failed start: %s", handle.server_id, exc)
            self._registry = ToolRegistry()
            self._started = False
            raise

    async def stop(self) -> None:
        for handle in self._servers.values():
            try:
                await handle.stop()
            except Exception as exc:
                logger.warning("Error stopping %r: %s", handle.server_id, exc)
        self._registry = ToolRegistry()
        self._tools_cache = None
        self._started = False

    # ── Stage 1 ───────────────────────────────────────────────────────────

    async def list_tools(self) -> list[ToolBrief]:
        """Return all tools as lightweight ToolBrief objects (Stage 1).

        Result is cached until refresh() is called.
        """
        if self._tools_cache is not None:
            return self._tools_cache

        briefs: list[ToolBrief] = []
        for handle in self._servers.values():
            briefs.extend(handle.tool_briefs())

        self._tools_cache = briefs
        return briefs

    async def list_tools_text(self) -> str:
        """Return formatted tool list suitable for system prompt injection."""
        briefs = await self.list_tools()
        lines = []
        for b in briefs:
            if b.full_schema is not None:
                suffix = "[full schema attached]"
            elif b.requires_params:
                suffix = "[params required]"
            else:
                suffix = "[no params]"
            lines.append(f"{b.name}: {b.description} {suffix}")
        return "\n".join(lines)

    # ── Stage 2 ───────────────────────────────────────────────────────────

    async def get_schemas(self, tool_names: list[str]) -> list[ToolSchema]:
        """Return full inputSchemas for the named tools (Stage 2)."""
        results = []
        for name in tool_names:
            server_id = self._registry.resolve(name)  # raises ToolNotFoundError
            handle = self._servers[server_id]
            try:
                schema = handle.get_schema(name)
            except KeyError as exc:
                raise SchemaFetchError(f"Schema not found for {name!r}") from exc
            results.append(ToolSchema(name=name, input_schema=schema))
        return results

    # ── Stage 3 ───────────────────────────────────────────────────────────

    async def call(
        self, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> ToolResult:
        """Execute a tool (Stage 3). arguments defaults to {} for no-param tools."""
        server_id = self._registry.resolve(tool_name)  # raises ToolNotFoundError
        handle = self._servers[server_id]
        return await handle.call_tool(tool_name, arguments or {})

    # ── refresh ────────────────────────────────────────────────────────────

    async def refresh(self, server_id: str | None = None) -> None:
        """Rebuild schema cache. Pass server_id to refresh only one server."""
        self._tools_cache = None

        if server_id and server_id not in self._servers:
            raise ValueError(f"Unknown server {server_id!r}")

        targets = (
            [self._servers[server_id]] if server_id else list(self._servers.values())
        )

        for handle in targets:
            self._registry.deregister_server(handle.server_id)
            await handle.refresh()
            self._register_server_tools(handle)

    # ── helpers ────────────────────────────────────────────────────────────

    def _register_server_tools(self, handle: ServerHandle) -> None:
        for brief in handle.tool_briefs():
            self._registry.register(brief.name, handle.server_id)
