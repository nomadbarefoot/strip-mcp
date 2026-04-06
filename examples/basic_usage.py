"""Basic async usage example."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add src to path for running without install
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from strip_mcp import StripMCP

MOCK = [sys.executable, str(Path(__file__).parent.parent / "tests" / "mock_mcp_server.py")]


async def main() -> None:
    async with StripMCP() as mcp:
        mcp.add_server("demo", command=MOCK + ["--tools", "5"])
        await mcp.start()

        # Stage 1: Discovery
        tools = await mcp.list_tools()
        print(f"Discovered {len(tools)} tools\n")

        text = await mcp.list_tools_text()
        print("Tool list for system prompt:")
        print(text)
        print()

        # Stage 2: Schema fetch for a tool that needs params
        param_tool = next(t for t in tools if t.requires_params)
        schemas = await mcp.get_schemas([param_tool.name])
        print(f"Schema for {param_tool.name}:")
        print(schemas[0].input_schema)
        print()

        # Stage 3: Execute
        result = await mcp.call(param_tool.name, {"arg_a": "hello"})
        print(f"Result: {result.content[0]['text']}")

        # Direct execution (no-param tool)
        no_param_tool = next(t for t in tools if not t.requires_params)
        print(f"\nDirect execute {no_param_tool.name} (no params):")
        result2 = await mcp.call(no_param_tool.name)
        print(f"Result: {result2.content[0]['text']}")


if __name__ == "__main__":
    asyncio.run(main())
