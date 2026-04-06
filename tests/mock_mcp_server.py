#!/usr/bin/env python3
"""Minimal MCP server over stdio for testing.

Usage:
    python mock_mcp_server.py [--tools N] [--latency SECS] [--fail TOOL_NAME]
"""

from __future__ import annotations

import argparse
import json
import sys
import time


def _make_tools(count: int) -> list[dict]:
    tools = []
    for i in range(count):
        if i % 5 == 0:
            # no-param tool
            tools.append({
                "name": f"tool_{i}",
                "description": f"Tool {i} (no params)",
                "inputSchema": {"type": "object", "properties": {}},
            })
        else:
            tools.append({
                "name": f"tool_{i}",
                "description": f"Tool {i} description",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "arg_a": {"type": "string", "description": "First arg"},
                        "arg_b": {"type": "integer", "description": "Second arg"},
                    },
                    "required": ["arg_a"],
                },
            })
    return tools


def _send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _respond(rpc_id: int, result: dict) -> None:
    _send({"jsonrpc": "2.0", "id": rpc_id, "result": result})


def _error(rpc_id: int, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tools", type=int, default=20)
    parser.add_argument("--latency", type=float, default=0.0)
    parser.add_argument("--fail", type=str, default=None, help="Tool name to fail")
    args = parser.parse_args()

    tools = _make_tools(args.tools)

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        if args.latency:
            time.sleep(args.latency)

        method = msg.get("method", "")
        rpc_id = msg.get("id")

        if method == "initialize":
            _respond(rpc_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mock-mcp", "version": "0.0.1"},
            })

        elif method == "notifications/initialized":
            pass  # notification, no response

        elif method == "tools/list":
            _respond(rpc_id, {"tools": tools})

        elif method == "tools/call":
            tool_name = msg.get("params", {}).get("name", "")
            if tool_name == args.fail:
                _respond(rpc_id, {
                    "content": [{"type": "text", "text": f"Simulated failure for {tool_name}"}],
                    "isError": True,
                })
            else:
                _respond(rpc_id, {
                    "content": [{"type": "text", "text": f"Called {tool_name} successfully"}],
                    "isError": False,
                })

        elif rpc_id is not None:
            _error(rpc_id, -32601, f"Method not found: {method}")


if __name__ == "__main__":
    main()
