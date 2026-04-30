#!/usr/bin/env python3
"""Live test: spawns toolgate proxy against Playwright MCP and exercises the 2-stage flow."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from toolgate.connection.stdio import StdioConnection


async def main() -> None:
    config = Path(__file__).parent / "test-proxy-config.json"
    venv_python = Path(__file__).parent / ".venv" / "bin" / "python"
    python = str(venv_python) if venv_python.exists() else sys.executable
    proxy_cmd = [python, "-m", "toolgate.cli", "proxy", "--config", str(config)]

    print("=== Spawning toolgate proxy ===")
    conn = StdioConnection(proxy_cmd, "proxy")

    # Stage 0: initialize handshake
    init = await conn.initialize()
    print(f"\n[initialize] serverInfo: {init.get('serverInfo')}")

    # Stage 1: tools/list — should be brief entries only
    raw_tools = await conn.list_tools()
    print(f"\n[tools/list] Got {len(raw_tools)} tools (including meta-tool)")

    meta = next((t for t in raw_tools if t["name"] == "__toolgate__get_schema"), None)
    upstream = [t for t in raw_tools if t["name"] != "__toolgate__get_schema"]

    print(f"  Meta-tool present: {meta is not None}")
    print(f"  Upstream tools: {len(upstream)}")
    print(f"\n  First 5 tool entries:")
    for t in upstream[:5]:
        schema_keys = list(t.get("inputSchema", {}).get("properties", {}).keys())
        print(f"    {t['name']!r}")
        print(f"      desc: {t['description'][:80]}")
        print(f"      schema properties: {schema_keys} (stub = empty)")

    # Stage 2: get schema for a specific tool
    if upstream:
        target = upstream[0]["name"]
        print(f"\n[Stage 2] Calling __toolgate__get_schema for {target!r}")
        result = await conn.call_tool(
            "__toolgate__get_schema",
            {"tool_name": target},
            timeout=15.0,
        )
        schema_text = result.get("content", [{}])[0].get("text", "")
        schema = json.loads(schema_text)
        print(f"  inputSchema keys: {list(schema.get('properties', {}).keys())}")
        print(f"  required: {schema.get('required', [])}")
        print(f"  Full schema:\n{json.dumps(schema, indent=4)}")

    # Stage 2 on a tool that has actual parameters
    param_tools = [t for t in upstream if "(call" in t["description"]]
    if param_tools:
        target2 = param_tools[0]["name"]
        print(f"\n[Stage 2] Fetching schema for {target2!r} (has params)")
        result2 = await conn.call_tool(
            "__toolgate__get_schema", {"tool_name": target2}, timeout=10.0
        )
        schema2_text = result2.get("content", [{}])[0].get("text", "")
        schema2 = json.loads(schema2_text)
        print(f"  properties: {list(schema2.get('properties', {}).keys())}")
        print(f"  required:   {schema2.get('required', [])}")

    # Token comparison: proxy brief list vs direct raw Playwright tools/list
    playwright_conn = StdioConnection(
        ["node", str(Path(__file__).parent / "node_modules" / "@playwright" / "mcp" / "cli.js")],
        "playwright-direct",
    )
    await playwright_conn.initialize()
    raw_playwright = await playwright_conn.list_tools()
    await playwright_conn.close()

    brief_json = json.dumps(raw_tools)   # proxy output (all 22 incl meta-tool)
    full_json = json.dumps(raw_playwright)  # direct playwright output

    brief_tokens = len(brief_json) // 4
    full_tokens = len(full_json) // 4
    reduction = (1 - brief_tokens / full_tokens) * 100

    print(f"\n=== Token comparison ===")
    print(f"  Direct Playwright tools/list:  ~{full_tokens:,} tokens  ({len(full_json):,} chars)")
    print(f"  Proxy brief list (21 + meta):  ~{brief_tokens:,} tokens  ({len(brief_json):,} chars)")
    print(f"  Reduction: {reduction:.1f}%")

    await conn.close()
    print("\n=== Done ===")


if __name__ == "__main__":
    asyncio.run(main())
