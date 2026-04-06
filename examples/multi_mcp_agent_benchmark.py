"""Simulate a multi-tool agent across several setup-light MCP servers.

Measures: cold start, Stage 1 vs full-schema token proxy, per-task latency,
schema-fetch cost for tools actually used, and failures.

Servers (local npm):
  - @playwright/mcp (+ chromium installed)
  - wikipedia-mcp
  - @modelcontextprotocol/server-memory

Run from repo root:
  cd strip-mcp && .venv/bin/python examples/multi_mcp_agent_benchmark.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from strip_mcp import StripMCP
from strip_mcp.errors import StripError
from strip_mcp.node_discovery import discover_node_mcp_servers

ROOT = Path(__file__).resolve().parent.parent


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class TaskResult:
    id: str
    description: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    ok: bool = True
    error: str | None = None
    total_ms: float = 0.0


@dataclass
class BenchmarkReport:
    servers: list[str]
    tool_count: int
    startup_ms: float
    list_tools_ms: float
    full_schema_json_build_ms: float
    stage1_approx_tokens: int
    full_schemas_approx_tokens: int
    full_vs_stage1_ratio: float
    agent_schemas_approx_tokens: int
    agent_vs_full_ratio: float
    tasks: list[TaskResult]
    failure_summary: list[str]
    token_note: str = "approx_tokens = len(text)//4; not a real tokenizer"


async def build_full_schemas_json(mcp: StripMCP, tools: list) -> str:
    payload = []
    for b in tools:
        sch = (await mcp.get_schemas([b.name]))[0]
        payload.append(
            {
                "name": b.name,
                "description": b.description,
                "inputSchema": sch.input_schema,
            }
        )
    return json.dumps(payload, indent=2)


async def run() -> BenchmarkReport:
    discovered = discover_node_mcp_servers(ROOT)
    need = {"playwright", "wiki", "memory"}
    have = {d.server_id for d in discovered}
    if not need <= have:
        raise SystemExit(
            f"Need MCP servers {sorted(need)}, found {sorted(have)}. "
            f"Run: cd {ROOT} && npm install && npx playwright install chromium"
        )

    tasks_out: list[TaskResult] = []
    failure_summary: list[str] = []

    t0 = time.perf_counter()
    async with StripMCP(default_timeout=120.0) as mcp:
        for d in discovered:
            mcp.add_server(d.server_id, command=d.command)

        await mcp.start()
        startup_ms = (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        tools = await mcp.list_tools()
        list_tools_ms = (time.perf_counter() - t1) * 1000

        stage1_text = await mcp.list_tools_text()
        stage1_tok = approx_tokens(stage1_text)

        t2 = time.perf_counter()
        full_json = await build_full_schemas_json(mcp, tools)
        full_tok = approx_tokens(full_json)
        full_build_ms = (time.perf_counter() - t2) * 1000

        # --- Scripted "day in the life" tasks (no LLM) ---
        scenarios: list[tuple[str, str, list[tuple[str, dict[str, Any] | None]]]] = [
            (
                "t1",
                "Look up a topic on Wikipedia",
                [
                    ("wiki__search", {"query": "Model Context Protocol"}),
                ],
            ),
            (
                "t2",
                "Open a short canonical article",
                [
                    ("wiki__readArticle", {"title": "Alan Turing"}),
                ],
            ),
            (
                "t3",
                "Capture notes in memory graph, then recall",
                [
                    (
                        "memory__create_entities",
                        {
                            "entities": [
                                {
                                    "name": "STRIP benchmark",
                                    "entityType": "task",
                                    "observations": [
                                        "Ran multi-MCP benchmark",
                                        "Servers: playwright, wiki, memory",
                                    ],
                                }
                            ]
                        },
                    ),
                    ("memory__search_nodes", {"query": "STRIP"}),
                    ("memory__read_graph", {}),
                ],
            ),
            (
                "t4",
                "Smoke-test browser automation",
                [
                    (
                        "playwright__browser_navigate",
                        {"url": "https://example.com"},
                    ),
                    ("playwright__browser_snapshot", {}),
                ],
            ),
        ]

        agent_schema_chars = 0

        for tid, desc, calls in scenarios:
            tr = TaskResult(id=tid, description=desc)
            t_task0 = time.perf_counter()
            try:
                for tool_name, args in calls:
                    step: dict[str, Any] = {"tool": tool_name}
                    t_s = time.perf_counter()
                    schemas = await mcp.get_schemas([tool_name])
                    schema_ms = (time.perf_counter() - t_s) * 1000
                    agent_schema_chars += len(
                        json.dumps(schemas[0].input_schema, sort_keys=True)
                    )

                    t_c = time.perf_counter()
                    result = await mcp.call(tool_name, args or {})
                    call_ms = (time.perf_counter() - t_c) * 1000

                    text = ""
                    if result.content:
                        c0 = result.content[0]
                        text = c0.get("text", str(c0))[:240]
                    step.update(
                        {
                            "schema_fetch_ms": round(schema_ms, 2),
                            "call_ms": round(call_ms, 2),
                            "response_preview": text
                            + ("…" if len(str(result.content)) > 240 else ""),
                            "content_items": len(result.content),
                        }
                    )
                    tr.steps.append(step)
            except StripError as e:
                tr.ok = False
                tr.error = f"{type(e).__name__}: {e}"
                failure_summary.append(f"{tid}: {tr.error}")
            except Exception as e:
                tr.ok = False
                tr.error = f"{type(e).__name__}: {e}"
                failure_summary.append(f"{tid}: {tr.error}")

            tr.total_ms = round((time.perf_counter() - t_task0) * 1000, 2)
            tasks_out.append(tr)

        agent_tok = max(1, agent_schema_chars // 4)

        report = BenchmarkReport(
            servers=sorted(have),
            tool_count=len(tools),
            startup_ms=round(startup_ms, 2),
            list_tools_ms=round(list_tools_ms, 2),
            full_schema_json_build_ms=round(full_build_ms, 2),
            stage1_approx_tokens=stage1_tok,
            full_schemas_approx_tokens=full_tok,
            full_vs_stage1_ratio=round(full_tok / stage1_tok, 2) if stage1_tok else 0,
            agent_schemas_approx_tokens=agent_tok,
            agent_vs_full_ratio=round(full_tok / agent_tok, 2) if agent_tok else 0,
            tasks=tasks_out,
            failure_summary=failure_summary,
        )

    return report


def main() -> None:
    report = asyncio.run(run())

    out_path = ROOT / "examples" / "multi_mcp_benchmark_report.json"
    serializable = {
        "servers": report.servers,
        "tool_count": report.tool_count,
        "startup_ms": report.startup_ms,
        "list_tools_ms": report.list_tools_ms,
        "full_schema_json_build_ms": report.full_schema_json_build_ms,
        "token_proxy": {
            "stage1_approx": report.stage1_approx_tokens,
            "all_tools_full_schemas_approx": report.full_schemas_approx_tokens,
            "full_vs_stage1_ratio": report.full_vs_stage1_ratio,
            "schemas_for_tools_used_in_tasks_only_approx": report.agent_schemas_approx_tokens,
            "full_vs_agent_used_ratio": report.agent_vs_full_ratio,
            "note": report.token_note,
        },
        "tasks": [asdict(t) for t in report.tasks],
        "failure_summary": report.failure_summary,
    }
    out_path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")

    print("=== Multi-MCP agent benchmark ===\n")
    print(f"Servers: {', '.join(report.servers)}")
    print(f"Total tools (namespaced): {report.tool_count}")
    print(f"Cold start (add_server + start all): {report.startup_ms:.1f} ms")
    print(f"list_tools() after start:            {report.list_tools_ms:.1f} ms")
    print(f"Build full-schema JSON for all tools:{report.full_schema_json_build_ms} ms")
    print()
    print("--- Token proxy (len/4) ---")
    print(f"Stage 1 (list_tools_text):              ~{report.stage1_approx_tokens:,}")
    print(f"All tools with full inputSchema JSON:   ~{report.full_schemas_approx_tokens:,}")
    print(f"Ratio full / stage1:                    {report.full_vs_stage1_ratio:.1f}x")
    print(f"Schemas for only tools used in tasks:   ~{report.agent_schemas_approx_tokens:,}")
    print(f"Ratio full / task-used schemas:         {report.agent_vs_full_ratio:.1f}x")
    print()
    for t in report.tasks:
        status = "OK" if t.ok else "FAIL"
        print(f"[{status}] {t.id} — {t.description} ({t.total_ms} ms total)")
        if t.error:
            print(f"       Error: {t.error}")
        for s in t.steps:
            print(
                f"       {s['tool']}: schema {s.get('schema_fetch_ms')} ms, "
                f"call {s.get('call_ms')} ms"
            )
            if "response_preview" in s:
                prev = s["response_preview"].replace("\n", " ")[:120]
                print(f"         → {prev}…" if len(prev) >= 120 else f"         → {prev}")
        print()

    if report.failure_summary:
        print("Failure points:")
        for f in report.failure_summary:
            print(f"  - {f}")
    else:
        print("No hard failures (StripError) recorded.")

    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
