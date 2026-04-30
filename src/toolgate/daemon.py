"""Local JSON-line daemon for low-context CLI tool calls."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from .core import ToolGate
from .errors import ToolGateError, ToolExecutionError, ToolNotFoundError
from .profiles import ToolProfile
from .proxy.config import ProxyConfig

logger = logging.getLogger(__name__)

DEFAULT_DAEMON_HOST = "127.0.0.1"
DEFAULT_DAEMON_PORT = 8765


@dataclass(frozen=True)
class DaemonAddress:
    """Network address for the local daemon."""

    host: str = DEFAULT_DAEMON_HOST
    port: int = DEFAULT_DAEMON_PORT


class ToolDaemon:
    """Owns live MCP server processes and serves compact JSON-line requests."""

    def __init__(self, config: ProxyConfig, *, profile: ToolProfile | None = None) -> None:
        self._config = config
        self._profile = profile
        self._gate = ToolGate()

    async def run(self, address: DaemonAddress = DaemonAddress()) -> None:
        for server_id, entry in self._config.servers.items():
            if self._profile and not self._server_needed(server_id):
                continue
            self._gate.add_server(
                server_id,
                command=entry.command,
                cwd=entry.cwd,
                env=entry.env,
                staged=True,
                namespace=True,
            )
        await self._gate.start()
        server = await asyncio.start_server(self._handle_client, address.host, address.port)
        sockets = ", ".join(str(sock.getsockname()) for sock in (server.sockets or []))
        logger.info("toolgate daemon listening on %s", sockets)
        try:
            async with server:
                await server.serve_forever()
        finally:
            await self._gate.stop()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw = await reader.readline()
            response = await self._handle_request(raw)
            writer.write(json.dumps(response).encode() + b"\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def _handle_request(self, raw: bytes) -> dict[str, Any]:
        try:
            req = json.loads(raw.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return _error(f"Invalid JSON request: {exc}")
        if not isinstance(req, dict):
            return _error("Request must be a JSON object")

        action = req.get("action")
        try:
            if action == "tools":
                return await self._tools()
            if action == "schema":
                return await self._schema(_string_arg(req, "tool_id"))
            if action == "call":
                return await self._call(
                    _string_arg(req, "tool_id"),
                    _dict_arg(req, "arguments", default={}),
                )
            if action == "status":
                return {"ok": True, "result": {"started": True}}
            return _error(f"Unknown action: {action!r}")
        except ValueError as exc:
            return _error(str(exc))
        except ToolExecutionError as exc:
            content = exc.mcp_error if isinstance(exc.mcp_error, list) else [{"type": "text", "text": str(exc.mcp_error)}]
            return {"ok": True, "result": {"content": content, "is_error": True}}
        except (ToolGateError, KeyError) as exc:
            return _error(str(exc))

    async def _tools(self) -> dict[str, Any]:
        briefs = await self._gate.list_tools()
        if self._profile:
            briefs = self._profile.filter_briefs(briefs)
        return {
            "ok": True,
            "result": [
                {
                    "name": brief.name,
                    "description": brief.description,
                    "server_id": brief.server_id,
                    "requires_params": brief.requires_params,
                }
                for brief in briefs
            ],
        }

    async def _schema(self, tool_id: str) -> dict[str, Any]:
        self._ensure_allowed(tool_id)
        schemas = await self._gate.get_schemas([tool_id])
        return {"ok": True, "result": schemas[0].input_schema}

    async def _call(self, tool_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._ensure_allowed(tool_id)
        result = await self._gate.call(tool_id, arguments)
        return {
            "ok": True,
            "result": {
                "name": result.name,
                "content": result.content,
                "is_error": result.is_error,
            },
        }

    def _ensure_allowed(self, tool_id: str) -> None:
        if self._profile is None:
            return
        server_id = _server_id_from_tool(tool_id)
        if not self._profile.allows(tool_id, server_id):
            raise ToolNotFoundError(tool_id)

    def _server_needed(self, server_id: str) -> bool:
        assert self._profile is not None
        return self._profile.needs_server(server_id)


async def request_daemon(
    payload: dict[str, Any],
    address: DaemonAddress = DaemonAddress(),
) -> dict[str, Any]:
    reader, writer = await asyncio.open_connection(address.host, address.port)
    try:
        writer.write(json.dumps(payload).encode() + b"\n")
        await writer.drain()
        raw = await reader.readline()
    finally:
        writer.close()
        await writer.wait_closed()
    if not raw:
        raise RuntimeError("Daemon returned no response")
    response = json.loads(raw.decode())
    if not isinstance(response, dict):
        raise RuntimeError("Daemon returned a non-object response")
    return response


def _server_id_from_tool(tool_id: str) -> str:
    return tool_id.split("__", 1)[0] if "__" in tool_id else ""


def _string_arg(req: dict[str, Any], key: str) -> str:
    value = req.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is required")
    return value


def _dict_arg(
    req: dict[str, Any],
    key: str,
    *,
    default: dict[str, Any],
) -> dict[str, Any]:
    value = req.get(key, default)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def _error(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}
