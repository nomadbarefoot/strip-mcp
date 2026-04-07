"""ProxyServer — MCP server-side stdio proxy.

Sits between Claude Code (or any MCP client) and upstream MCP servers.
Implements 2-stage tool delivery:

  Stage 1 — tools/list returns brief entries (name + description, stub schema).
  Stage 2 — __strip__get_schema returns full inputSchema on demand.
  Stage 3 — tools/call routes to the correct upstream server.

Option C (future): instead of a meta-tool, each tool description could carry
a _schema flag directive. Deferred; option A (meta-tool) is implemented here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from typing import Any

from ..core import StripMCP
from ..errors import SchemaFetchError, StripError, ToolExecutionError, ToolNotFoundError
from .config import ProxyConfig

logger = logging.getLogger(__name__)

_META_TOOL_NAME = "__strip__get_schema"
_STUB_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": True,
}
_META_TOOL_DEF: dict[str, Any] = {
    "name": _META_TOOL_NAME,
    "description": (
        "Returns the full parameter schema for any upstream tool. "
        "Call this before using a tool whose parameters you don't know."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "tool_name": {
                "type": "string",
                "description": "Exact namespaced tool name, e.g. 'playwright__browser_navigate'",
            }
        },
        "required": ["tool_name"],
    },
}
_STARTUP_TIMEOUT = 15.0  # seconds to wait for upstreams before serving partial list


class ProxyServer:
    """MCP server-side stdio proxy between an MCP client and upstream MCP servers."""

    def __init__(self, config: ProxyConfig) -> None:
        self._config = config
        self._strip = StripMCP()
        self._startup_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._shutdown = False

    async def run(self) -> None:
        """Register upstream servers, handle signals, serve stdio until EOF."""
        for server_id, entry in self._config.servers.items():
            self._strip.add_server(
                server_id,
                command=entry.command,
                staged=True,
                namespace=True,
            )

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._on_signal)

        try:
            await self._serve_stdio()
        finally:
            if self._startup_task and not self._startup_task.done():
                self._startup_task.cancel()
            await self._strip.stop()

    def _on_signal(self) -> None:
        self._shutdown = True

    async def _serve_stdio(self) -> None:
        loop = asyncio.get_running_loop()

        # Use asyncio native pipe reading — avoids thread-pool races from run_in_executor
        reader = asyncio.StreamReader(limit=64 * 1024 * 1024)
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin.buffer)

        in_flight: set[asyncio.Task[None]] = set()

        try:
            while not self._shutdown:
                line = await reader.readline()
                if not line:  # EOF — client closed the session
                    break

                raw = line.strip()
                if not raw:
                    continue

                try:
                    req = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("Malformed JSON from client: %.200r", raw)
                    continue

                task = asyncio.create_task(self._dispatch(req))
                in_flight.add(task)
                task.add_done_callback(in_flight.discard)
        finally:
            if in_flight:
                await asyncio.gather(*in_flight, return_exceptions=True)

    async def _dispatch(self, req: dict[str, Any]) -> None:
        try:
            response = await self._handle(req)
        except Exception as exc:
            logger.exception("Unhandled error dispatching %r", req.get("method"))
            response = self._make_error(req.get("id"), -32603, f"Internal error: {exc}")

        if response is not None:
            await self._write(response)

    async def _write(self, msg: dict[str, Any]) -> None:
        try:
            async with self._write_lock:
                sys.stdout.write(json.dumps(msg) + "\n")
                sys.stdout.flush()
        except BrokenPipeError:
            self._shutdown = True

    # ── request handlers ───────────────────────────────────────────────────

    async def _handle(self, req: dict[str, Any]) -> dict[str, Any] | None:
        method = req.get("method", "")
        rpc_id = req.get("id")

        match method:
            case "initialize":
                return self._on_initialize(rpc_id)
            case "notifications/initialized":
                return None  # startup already kicked off in _on_initialize
            case "tools/list":
                return await self._on_list_tools(rpc_id)
            case "tools/call":
                return await self._on_call_tool(req)
            case _:
                if rpc_id is not None:
                    return self._make_error(rpc_id, -32601, f"Method not found: {method}")
                return None

    def _on_initialize(self, rpc_id: int | None) -> dict[str, Any]:
        # Kick off upstream connections immediately so they're ready for tools/list
        if self._startup_task is None:
            self._startup_task = asyncio.create_task(self._start_upstreams())

        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "strip-mcp-proxy", "version": "0.1.0"},
            },
        }

    async def _start_upstreams(self) -> None:
        try:
            await self._strip.start()
            logger.info("All upstream servers started")
        except Exception as exc:
            logger.error("Upstream startup failed: %s", exc)

    async def _on_list_tools(self, rpc_id: int | None) -> dict[str, Any]:
        # Wait for startup, but don't block forever
        if self._startup_task is not None and not self._startup_task.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._startup_task), timeout=_STARTUP_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.warning("Upstream startup timed out — returning partial tool list")
            except Exception as exc:
                logger.warning("Startup error: %s", exc)

        briefs = await self._strip.list_tools()
        tools: list[dict[str, Any]] = []

        for brief in briefs:
            desc = brief.description
            if brief.requires_params:
                desc = f"{desc} (call {_META_TOOL_NAME} to get parameters before use)"
            tools.append({
                "name": brief.name,
                "description": desc,
                "inputSchema": _STUB_SCHEMA,
            })

        tools.append(_META_TOOL_DEF)
        return {"jsonrpc": "2.0", "id": rpc_id, "result": {"tools": tools}}

    async def _on_call_tool(self, req: dict[str, Any]) -> dict[str, Any]:
        rpc_id = req.get("id")
        params = req.get("params", {})
        tool_name: str = params.get("name", "")
        arguments: dict[str, Any] = params.get("arguments") or {}

        if tool_name == _META_TOOL_NAME:
            return await self._handle_get_schema(rpc_id, arguments)

        try:
            result = await self._strip.call(tool_name, arguments)
        except ToolNotFoundError as exc:
            return self._make_error(rpc_id, -32602, str(exc))
        except ToolExecutionError as exc:
            # Tool ran but returned isError — valid MCP result, not a protocol error
            content = exc.mcp_error if isinstance(exc.mcp_error, list) else [{"type": "text", "text": str(exc.mcp_error)}]
            return self._make_tool_result(rpc_id, content, is_error=True)
        except StripError as exc:
            return self._make_error(rpc_id, -32603, str(exc))

        return self._make_tool_result(rpc_id, result.content, is_error=result.is_error)

    async def _handle_get_schema(
        self, rpc_id: int | None, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        tool_name = arguments.get("tool_name", "")
        if not tool_name:
            return self._make_error(rpc_id, -32602, "tool_name is required")

        try:
            schemas = await self._strip.get_schemas([tool_name])
            schema_text = json.dumps(schemas[0].input_schema, indent=2)
            return self._make_tool_result(rpc_id, [{"type": "text", "text": schema_text}])
        except (ToolNotFoundError, SchemaFetchError) as exc:
            return self._make_tool_result(
                rpc_id,
                [{"type": "text", "text": f"Error: {exc}"}],
                is_error=True,
            )

    # ── helpers ────────────────────────────────────────────────────────────

    def _make_tool_result(
        self,
        rpc_id: int | None,
        content: list[dict[str, Any]],
        is_error: bool = False,
    ) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {"content": content, "isError": is_error},
        }

    def _make_error(self, rpc_id: int | None, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {"code": code, "message": message},
        }
