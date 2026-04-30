"""Probe configured MCP servers and persist their tool inventory."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .catalog import Catalog, CatalogTool
from .proxy.config import ProxyConfig, ServerEntry
from .server import ServerHandle


@dataclass(frozen=True)
class ServerCollectResult:
    """Collection outcome for one upstream server."""

    server_id: str
    status: str
    tool_count: int = 0
    error: str | None = None


@dataclass(frozen=True)
class CollectionReport:
    """Summary returned by inventory collection."""

    results: list[ServerCollectResult] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for result in self.results if result.status == "ok")

    @property
    def failed_count(self) -> int:
        return sum(1 for result in self.results if result.status != "ok")


async def collect_inventory(config: ProxyConfig, catalog: Catalog) -> CollectionReport:
    """Probe every configured server and persist normalized tool metadata."""
    catalog.initialize()
    results: list[ServerCollectResult] = []
    for server_id, entry in config.servers.items():
        result = await _collect_server(server_id, entry, catalog)
        results.append(result)
    return CollectionReport(results=results)


async def _collect_server(
    server_id: str,
    entry: ServerEntry,
    catalog: Catalog,
) -> ServerCollectResult:
    updated_at = _utc_now()
    handle = ServerHandle(
        server_id,
        command=entry.command,
        cwd=entry.cwd,
        env=entry.env,
        staged=True,
        namespace=True,
    )
    try:
        await handle.start()
        tools = _catalog_tools(handle)
        catalog.upsert_server(
            server_id=server_id,
            command=entry.command,
            cwd=entry.cwd,
            env=entry.env,
            status="ok",
            error=None,
            tool_count=len(tools),
            updated_at=updated_at,
        )
        catalog.replace_server_tools(
            server_id=server_id,
            tools=tools,
            updated_at=updated_at,
        )
        return ServerCollectResult(server_id=server_id, status="ok", tool_count=len(tools))
    except Exception as exc:  # noqa: BLE001 - external server failures should be recorded.
        catalog.upsert_server(
            server_id=server_id,
            command=entry.command,
            cwd=entry.cwd,
            env=entry.env,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            tool_count=0,
            updated_at=updated_at,
        )
        catalog.replace_server_tools(server_id=server_id, tools=[], updated_at=updated_at)
        return ServerCollectResult(
            server_id=server_id,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        await handle.stop()


def _catalog_tools(handle: ServerHandle) -> list[CatalogTool]:
    tools: list[CatalogTool] = []
    raw_tools = handle.raw_tools
    for raw in raw_tools:
        raw_name = str(raw["name"])
        input_schema = raw.get("inputSchema", {})
        if not isinstance(input_schema, dict):
            input_schema = {}
        output_schema = _optional_dict(raw.get("outputSchema"))
        annotations = _optional_dict(raw.get("annotations"))
        tool_id = f"{handle.server_id}__{raw_name}"
        tools.append(
            CatalogTool(
                tool_id=tool_id,
                server_id=handle.server_id,
                raw_name=raw_name,
                description=str(raw.get("description", "")),
                input_schema=input_schema,
                output_schema=output_schema,
                annotations=annotations,
                requires_params=bool(input_schema.get("properties", {})),
            )
        )
    return tools


def _optional_dict(value: object) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
