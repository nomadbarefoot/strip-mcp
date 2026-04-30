"""Local JSON-line daemon integration tests."""

from __future__ import annotations

import asyncio
import socket

from toolgate.daemon import DaemonAddress, ToolDaemon, request_daemon
from toolgate.proxy.config import ProxyConfig, ServerEntry


async def test_daemon_routes_tool_call(mock_server_cmd: list[str]) -> None:
    port = _free_port()
    config = ProxyConfig(
        servers={"mock": ServerEntry(command=mock_server_cmd + ["--tools", "2"])}
    )
    daemon = ToolDaemon(config)
    task = asyncio.create_task(daemon.run(DaemonAddress(port=port)))
    try:
        await _wait_for_daemon(port)
        response = await request_daemon(
            {"action": "call", "tool_id": "mock__tool_0", "arguments": {}},
            DaemonAddress(port=port),
        )
        assert response["ok"] is True
        assert response["result"]["is_error"] is False
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_for_daemon(port: int) -> None:
    for _ in range(50):
        try:
            response = await request_daemon(
                {"action": "status"},
                DaemonAddress(port=port),
            )
            if response.get("ok"):
                return
        except OSError:
            await asyncio.sleep(0.05)
    raise TimeoutError("daemon did not start")
