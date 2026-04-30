"""Integration smoke: real @playwright/mcp via toolgate.

Requires: npm install in toolgate/ and `npx playwright install chromium`.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toolgate import ToolGate

ROOT = Path(__file__).resolve().parent.parent
PLAYWRIGHT_MCP_CLI = ROOT / "node_modules" / "@playwright" / "mcp" / "cli.js"


async def main() -> None:
    if not PLAYWRIGHT_MCP_CLI.is_file():
        print(
            "Missing Playwright MCP. Run:\n"
            f"  cd {ROOT}\n"
            "  npm install\n"
            "  npx playwright install chromium\n",
            file=sys.stderr,
        )
        sys.exit(1)

    async with ToolGate(default_timeout=120.0) as mcp:
        mcp.add_server(
            "playwright",
            command=["node", str(PLAYWRIGHT_MCP_CLI)],
        )
        await mcp.start()

        tools = await mcp.list_tools()
        print(f"Stage 1: {len(tools)} tools (namespaced)")
        for b in tools[:5]:
            print(f"  - {b.name}: {b.description[:70]}…" if len(b.description) > 70 else f"  - {b.name}: {b.description}")
        if len(tools) > 5:
            print(f"  … and {len(tools) - 5} more")

        nav = "playwright__browser_navigate"
        brief_by_name = {b.name: b for b in tools}
        if nav not in brief_by_name:
            raise SystemExit(f"Expected tool {nav!r} not in list")

        schemas = await mcp.get_schemas([nav])
        print(f"\nStage 2: fetched schema for {nav} (keys: {list(schemas[0].input_schema.keys())})")

        result = await mcp.call(nav, {"url": "https://example.com"})
        text = result.content[0].get("text", "") if result.content else ""
        preview = text[:500] + ("…" if len(text) > 500 else "")
        print(f"\nStage 3: browser_navigate → content[0] preview ({len(text)} chars):\n{preview}")

        # Token-ish size comparison: Stage 1 text vs full schemas JSON
        stage1 = await mcp.list_tools_text()
        full_blob = json.dumps(
            [
                {
                    "name": b.name,
                    "description": b.description,
                    "inputSchema": (await mcp.get_schemas([b.name]))[0].input_schema,
                }
                for b in tools
            ],
            indent=2,
        )
        approx = lambda s: max(1, len(s) // 4)
        print("\n--- Approx token comparison (len/4) ---")
        print(f"Stage 1 list_tools_text:  ~{approx(stage1):,}")
        print(f"All tools full schemas:   ~{approx(full_blob):,}")
        print(f"Ratio (full/stage1):      {approx(full_blob) / approx(stage1):.1f}x")


if __name__ == "__main__":
    asyncio.run(main())
