"""SyncStripMCP — blocking wrapper around StripMCP."""

from __future__ import annotations

import asyncio
from typing import Any

from .core import StripMCP
from .types import ToolBrief, ToolResult, ToolSchema


class SyncStripMCP:
    """Synchronous interface to StripMCP. Runs an internal event loop."""

    def __init__(self, default_timeout: float = 30.0) -> None:
        self._loop = asyncio.new_event_loop()
        self._async = StripMCP(default_timeout=default_timeout)

    def add_server(
        self,
        server_id: str,
        *,
        command: list[str] | None = None,
        url: str | None = None,
        staged: bool = True,
        namespace: bool = True,
        timeout: float | None = None,
        description_overrides: dict[str, str] | None = None,
    ) -> None:
        self._async.add_server(
            server_id,
            command=command,
            url=url,
            staged=staged,
            namespace=namespace,
            timeout=timeout,
            description_overrides=description_overrides,
        )

    def start(self) -> None:
        self._loop.run_until_complete(self._async.start())

    def stop(self) -> None:
        self._loop.run_until_complete(self._async.stop())
        self._loop.close()

    def list_tools(self) -> list[ToolBrief]:
        return self._loop.run_until_complete(self._async.list_tools())

    def list_tools_text(self) -> str:
        return self._loop.run_until_complete(self._async.list_tools_text())

    def get_schemas(self, tool_names: list[str]) -> list[ToolSchema]:
        return self._loop.run_until_complete(self._async.get_schemas(tool_names))

    def call(
        self, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> ToolResult:
        return self._loop.run_until_complete(self._async.call(tool_name, arguments))

    def refresh(self, server_id: str | None = None) -> None:
        self._loop.run_until_complete(self._async.refresh(server_id))

    def __enter__(self) -> "SyncStripMCP":
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()
