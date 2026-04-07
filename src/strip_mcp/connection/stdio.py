"""StdioConnection — MCP client over subprocess stdin/stdout."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from asyncio.subprocess import Process
from typing import Any

from ..errors import RemoteRPCError, ServerCrashedError, ServerStartError, ToolTimeoutError
from .base import MCPConnection

logger = logging.getLogger(__name__)

_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "strip-mcp", "version": "0.1.0"}
_STARTUP_TIMEOUT = 10.0  # seconds to wait for initialize response
# Default asyncio line iteration caps ~64 KiB per line; MCP tool results (e.g. long Wikipedia articles) exceed that.
_MAX_JSON_LINE_BYTES = 64 * 1024 * 1024


class StdioConnection(MCPConnection):
    """Manages an MCP server subprocess and speaks JSON-RPC over stdio."""

    def __init__(
        self,
        command: list[str],
        server_id: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._command = command
        self._server_id = server_id
        self._cwd = cwd
        self._env = env
        self._process: Process | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def initialize(self) -> dict[str, Any]:
        """Spawn subprocess and run the MCP initialize handshake."""
        self._next_id = 1
        self._pending.clear()
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
                env=({**os.environ, **self._env} if self._env else None),
            )
        except (FileNotFoundError, PermissionError) as exc:
            raise ServerStartError(
                f"Failed to start {self._server_id!r}: {exc}"
            ) from exc

        self._reader_task = asyncio.create_task(
            self._read_loop(), name=f"strip-reader-{self._server_id}"
        )
        self._stderr_task = asyncio.create_task(
            self._stderr_loop(), name=f"strip-stderr-{self._server_id}"
        )

        try:
            result = await asyncio.wait_for(
                self._rpc("initialize", {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": _CLIENT_INFO,
                }),
                timeout=_STARTUP_TIMEOUT,
            )
        except asyncio.TimeoutError as exc:
            await self.close()
            raise ServerStartError(
                f"Server {self._server_id!r} initialize timed out"
            ) from exc
        except Exception as exc:
            await self.close()
            raise ServerStartError(
                f"Server {self._server_id!r} initialize failed: {exc}"
            ) from exc

        # Send initialized notification (no response expected)
        await self._notify("notifications/initialized")
        return result

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._rpc("tools/list", {})
        return result.get("tools", [])

    async def call_tool(
        self, name: str, arguments: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        self._check_alive()
        try:
            return await asyncio.wait_for(
                self._rpc("tools/call", {"name": name, "arguments": arguments}),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise ToolTimeoutError(name, timeout) from exc

    async def close(self) -> None:
        """Terminate subprocess; cancel background tasks."""
        tasks = [t for t in (self._reader_task, self._stderr_task) if t is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._reader_task = None
        self._stderr_task = None

        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=3.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=1.0)
                except (asyncio.TimeoutError, ProcessLookupError):
                    pass

        self._fail_all_pending(f"Server {self._server_id!r} shut down")
        self._process = None

    # ── internal ───────────────────────────────────────────────────────────

    def _check_alive(self) -> None:
        if self._process is None or self._process.returncode is not None:
            raise ServerCrashedError(
                f"Server {self._server_id!r} is not running "
                f"(exit code {self._process.returncode if self._process else 'never started'})"
            )

    async def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._check_alive()
        rpc_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[rpc_id] = fut

        msg = json.dumps({"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params})
        try:
            assert self._process and self._process.stdin
            self._process.stdin.write((msg + "\n").encode())
            await self._process.stdin.drain()
            return await fut
        except asyncio.CancelledError:
            raise
        except (BrokenPipeError, ConnectionResetError, OSError, RuntimeError) as exc:
            raise ServerCrashedError(
                f"Server {self._server_id!r} write failed: {exc}"
            ) from exc
        finally:
            pending = self._pending.pop(rpc_id, None)
            if pending is not None and not pending.done():
                pending.cancel()

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._check_alive()
        msg = json.dumps({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})
        try:
            assert self._process and self._process.stdin
            self._process.stdin.write((msg + "\n").encode())
            await self._process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError, RuntimeError) as exc:
            raise ServerCrashedError(
                f"Server {self._server_id!r} notify failed: {exc}"
            ) from exc

    async def _read_loop(self) -> None:
        assert self._process and self._process.stdout
        stream = self._process.stdout
        buf = bytearray()
        try:
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    break
                buf.extend(chunk)
                while True:
                    idx = buf.find(b"\n")
                    if idx < 0:
                        if len(buf) > _MAX_JSON_LINE_BYTES:
                            raise ValueError(
                                f"JSON-RPC line exceeds {_MAX_JSON_LINE_BYTES // (1024 * 1024)} MiB "
                                "before newline (increase _MAX_JSON_LINE_BYTES)"
                            )
                        break
                    raw_line = bytes(buf[: idx + 1])
                    del buf[: idx + 1]
                    self._dispatch_json_line(raw_line)

            if buf:
                tail = buf.decode(errors="replace").strip()
                if tail:
                    self._dispatch_json_line(buf)

            if self._pending:
                self._fail_all_pending(
                    f"Server {self._server_id!r} closed stdout while requests were pending"
                )

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("%s: read loop died: %s", self._server_id, exc)
            self._fail_all_pending(f"Server {self._server_id!r} read loop failed: {exc}")

    def _dispatch_json_line(self, raw_line: bytes) -> None:
        line = raw_line.decode(errors="replace").strip()
        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("%s: invalid JSON line: %r", self._server_id, line)
            return

        rpc_id = msg.get("id")
        if rpc_id is None:
            logger.debug("%s: notification: %s", self._server_id, msg.get("method"))
            return

        fut = self._pending.pop(rpc_id, None)
        if fut is None:
            logger.warning("%s: unexpected id %s", self._server_id, rpc_id)
            return

        if fut.done():
            return

        if "error" in msg:
            fut.set_exception(RemoteRPCError(msg["error"]))
        else:
            fut.set_result(msg.get("result", {}))

    async def _stderr_loop(self) -> None:
        assert self._process and self._process.stderr
        try:
            async for raw_line in self._process.stderr:
                line = raw_line.decode(errors="replace").strip()
                if line:
                    logger.debug("%s stderr: %s", self._server_id, line)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug("%s: stderr loop error: %s", self._server_id, exc)

    def _fail_all_pending(self, reason: str) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ServerCrashedError(reason))
        self._pending.clear()
