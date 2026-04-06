"""Token savings benchmark: staged vs full schema delivery.

Counts approximate tokens (word-level split) for both approaches
with 50 tools of varying schema complexity.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from strip_mcp import StripMCP

MOCK = [sys.executable, str(Path(__file__).parent.parent / "tests" / "mock_mcp_server.py")]


def _approx_tokens(text: str) -> int:
    """Rough token estimate: 1 token ≈ 4 chars."""
    return max(1, len(text) // 4)


async def main() -> None:
    mcp_staged = StripMCP()
    mcp_full = StripMCP()

    mcp_staged.add_server("demo", command=MOCK + ["--tools", "50"], staged=True)
    mcp_full.add_server("demo", command=MOCK + ["--tools", "50"], staged=False)

    await mcp_staged.start()
    await mcp_full.start()

    try:
        # Staged: Stage 1 text (what LLM sees first)
        staged_text = await mcp_staged.list_tools_text()
        staged_tokens = _approx_tokens(staged_text)

        # Full: all schemas up front
        full_briefs = await mcp_full.list_tools()
        full_text = json.dumps([
            {"name": b.name, "description": b.description, "inputSchema": b.full_schema}
            for b in full_briefs
        ], indent=2)
        full_tokens = _approx_tokens(full_text)

        print(f"Tools:          50")
        print(f"Staged (Stage 1): ~{staged_tokens:,} tokens")
        print(f"Full upfront:     ~{full_tokens:,} tokens")
        print(f"Savings:          ~{full_tokens - staged_tokens:,} tokens ({(1 - staged_tokens/full_tokens)*100:.0f}%)")
        print()
        print("Note: Stage 2 adds tokens back for requested schemas only.")
        print("Break-even: if LLM needs all schemas anyway, staging adds latency with no savings.")
        print("Sweet spot: large tool registries where LLM uses <30% of tools per turn.")

    finally:
        await mcp_staged.stop()
        await mcp_full.stop()


if __name__ == "__main__":
    asyncio.run(main())
