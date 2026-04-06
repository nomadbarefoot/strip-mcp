"""Compare Strip (staged) vs full-schema-upfront MCP: single or multi-iteration suite.

Uses discover_node_mcp_servers() for Node MCPs under node_modules.

Examples:
  python examples/strip_vs_full_benchmark.py
  python examples/strip_vs_full_benchmark.py --iterations 12 --output examples/report.json
  python examples/strip_vs_full_benchmark.py -v --iterations 1

Default: silent (no stdout). Use -v / --verbose for human-readable summary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from strip_mcp import StripMCP
from strip_mcp.errors import StripError
from strip_mcp.node_discovery import discover_node_mcp_servers

ROOT = Path(__file__).resolve().parent.parent


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class ScenarioTask:
    """One logical task: one or more tool calls."""

    id: str
    description: str
    steps: list[tuple[str, dict[str, Any] | None]]
    expect_failure: bool = False


@dataclass
class TaskRecord:
    id: str
    description: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    ok: bool = True
    error: str | None = None
    total_ms: float = 0.0
    expect_failure: bool = False
    outcome_passed: bool = True


@dataclass
class ModeReport:
    mode: Literal["strip", "full"]
    staged: bool
    servers: list[str]
    tool_count: int
    startup_ms: float
    list_tools_ms: float
    full_schema_json_build_ms: float
    stage1_list_tools_text_approx_tokens: int
    all_tools_full_schemas_json_approx_tokens: int
    full_vs_stage1_ratio: float
    agent_used_schemas_only_approx_tokens: int
    full_vs_agent_used_ratio: float
    tasks: list[TaskRecord]
    failure_summary: list[str]
    token_note: str = "approx_tokens = len(text)//4; not a real tokenizer"


def _wiki_queries() -> list[str]:
    return [
        "Model Context Protocol",
        "Python programming language",
        "Hypertext Transfer Protocol",
        "Open-source software",
        "GraphQL",
        "Docker container",
        "Kubernetes",
        "PostgreSQL",
        "Rust programming",
        "TypeScript",
        "Machine learning",
        "Unicode standard",
    ]


def _wiki_titles() -> list[str]:
    return [
        "Alan Turing",
        "HTTP",
        "Paris",
        "Kubernetes",
        "Python (programming language)",
        "World Wide Web",
        "Linux",
        "JSON",
        "Git",
        "Docker (software)",
        "TensorFlow",
        "ASCII",
    ]


def _urls() -> list[tuple[str, str]]:
    """Pairs: (label, url) for browser flows."""
    return [
        ("example.com", "https://example.com"),
        ("example.org", "https://example.org"),
        ("iana.org", "https://www.iana.org/help/example-domains"),
        ("httpbin", "https://httpbin.org/get"),
        ("jsonplaceholder", "https://jsonplaceholder.typicode.com/"),
    ]


def build_scenarios(iteration_index: int) -> list[ScenarioTask]:
    """Varied tasks + edge cases; deterministic per iteration."""
    i = iteration_index
    q = _wiki_queries()[i % len(_wiki_queries())]
    title = _wiki_titles()[i % len(_wiki_titles())]
    url_a, url_b = _urls()[i % len(_urls())][1], _urls()[(i + 1) % len(_urls())][1]
    tag = f"bench-i{i}-u{iteration_index}"

    return [
        ScenarioTask(
            f"t1_search_{i}",
            f"Wikipedia search (varied query): {q[:40]}…",
            [("wiki__search", {"query": q})],
        ),
        ScenarioTask(
            f"t2_article_{i}",
            f"Wikipedia readArticle (varied title): {title}",
            [("wiki__readArticle", {"title": title})],
        ),
        ScenarioTask(
            f"t3_memory_unicode_{i}",
            "Memory graph: unicode entity + recall",
            [
                (
                    "memory__create_entities",
                    {
                        "entities": [
                            {
                                "name": f"測試-{tag}-α",
                                "entityType": "benchmark",
                                "observations": [
                                    f"iteration={iteration_index}",
                                    "obs: café naïve résumé",
                                ],
                            }
                        ]
                    },
                ),
                ("memory__search_nodes", {"query": "測試"}),
                ("memory__read_graph", {}),
            ],
        ),
        ScenarioTask(
            f"t4_browser_chain_{i}",
            "Playwright: navigate A → snapshot → navigate B → snapshot",
            [
                ("playwright__browser_navigate", {"url": url_a}),
                ("playwright__browser_snapshot", {}),
                ("playwright__browser_navigate", {"url": url_b}),
                ("playwright__browser_snapshot", {}),
            ],
        ),
        ScenarioTask(
            "edge_unknown_tool",
            "Edge: namespaced tool that does not exist (expect ToolNotFound)",
            [("wiki__strip_benchmark_nonexistent_tool_xyz", {})],
            expect_failure=True,
        ),
        ScenarioTask(
            "edge_empty_search",
            "Edge: Wikipedia search with empty query string",
            [("wiki__search", {"query": ""})],
        ),
        ScenarioTask(
            "edge_idempotent_graph",
            "Edge: read_graph twice in a row (idempotent)",
            [
                ("memory__read_graph", {}),
                ("memory__read_graph", {}),
            ],
        ),
    ]


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


async def _run_one_step(
    mcp: StripMCP,
    tool_name: str,
    args: dict[str, Any] | None,
) -> tuple[dict[str, Any], int]:
    """Return step dict and schema JSON char count for token proxy."""
    step: dict[str, Any] = {"tool": tool_name}
    t_s = time.perf_counter()
    schemas = await mcp.get_schemas([tool_name])
    schema_ms = (time.perf_counter() - t_s) * 1000
    sch_chars = len(json.dumps(schemas[0].input_schema, sort_keys=True))

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
    return step, sch_chars


def _record_task_error(
    tr: TaskRecord,
    sc: ScenarioTask,
    exc: BaseException,
    failure_summary: list[str],
) -> None:
    tr.ok = False
    tr.error = f"{type(exc).__name__}: {exc}"
    if not sc.expect_failure:
        failure_summary.append(f"{sc.id}: {tr.error}")


async def _run_scenario_tasks(
    mcp: StripMCP,
    scenarios: list[ScenarioTask],
) -> tuple[list[TaskRecord], list[str], int]:
    tasks_out: list[TaskRecord] = []
    failure_summary: list[str] = []
    agent_schema_chars = 0

    for sc in scenarios:
        tr = TaskRecord(
            id=sc.id,
            description=sc.description,
            expect_failure=sc.expect_failure,
        )
        t_task0 = time.perf_counter()
        try:
            for tool_name, args in sc.steps:
                step, sch_chars = await _run_one_step(mcp, tool_name, args)
                agent_schema_chars += sch_chars
                tr.steps.append(step)
        except StripError as e:
            _record_task_error(tr, sc, e, failure_summary)
        except Exception as e:
            _record_task_error(tr, sc, e, failure_summary)

        tr.total_ms = round((time.perf_counter() - t_task0) * 1000, 2)
        tr.outcome_passed = (not tr.ok) if sc.expect_failure else tr.ok
        tasks_out.append(tr)

    return tasks_out, failure_summary, agent_schema_chars


async def run_one_mode(
    staged: bool,
    discovered: list,
    scenarios: list[ScenarioTask],
) -> ModeReport:
    mode: Literal["strip", "full"] = "strip" if staged else "full"

    t0 = time.perf_counter()
    async with StripMCP(default_timeout=120.0) as mcp:
        for d in discovered:
            mcp.add_server(d.server_id, command=d.command, staged=staged)

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

        tasks_out, failure_summary, agent_schema_chars = await _run_scenario_tasks(
            mcp, scenarios
        )

        agent_tok = max(1, agent_schema_chars // 4)
        server_ids = [d.server_id for d in discovered]

        return ModeReport(
            mode=mode,
            staged=staged,
            servers=server_ids,
            tool_count=len(tools),
            startup_ms=round(startup_ms, 2),
            list_tools_ms=round(list_tools_ms, 2),
            full_schema_json_build_ms=round(full_build_ms, 2),
            stage1_list_tools_text_approx_tokens=stage1_tok,
            all_tools_full_schemas_json_approx_tokens=full_tok,
            full_vs_stage1_ratio=round(full_tok / stage1_tok, 2) if stage1_tok else 0.0,
            agent_used_schemas_only_approx_tokens=agent_tok,
            full_vs_agent_used_ratio=round(full_tok / agent_tok, 2) if agent_tok else 0.0,
            tasks=tasks_out,
            failure_summary=failure_summary,
        )


def total_task_ms(r: ModeReport) -> float:
    return sum(t.total_ms for t in r.tasks)


def _stdev(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    return round(statistics.stdev(xs), 4)


def _agg(name: str, values: list[float]) -> dict[str, Any]:
    if not values:
        return {"metric": name, "count": 0}
    return {
        "metric": name,
        "count": len(values),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "mean": round(statistics.mean(values), 4),
        "stdev": _stdev(values),
    }


async def run_single_iteration(
    discovered: list,
    iteration_index: int,
) -> dict[str, Any]:
    scenarios = build_scenarios(iteration_index)

    strip_r = await run_one_mode(True, discovered, scenarios)
    full_r = await run_one_mode(False, discovered, scenarios)

    comparison = {
        "strip_stage1_tokens": strip_r.stage1_list_tools_text_approx_tokens,
        "full_stage1_tokens": full_r.stage1_list_tools_text_approx_tokens,
        "strip_all_tools_json_tokens": strip_r.all_tools_full_schemas_json_approx_tokens,
        "full_all_tools_json_tokens": full_r.all_tools_full_schemas_json_approx_tokens,
        "strip_startup_ms": strip_r.startup_ms,
        "full_startup_ms": full_r.startup_ms,
        "strip_sum_task_ms": round(total_task_ms(strip_r), 2),
        "full_sum_task_ms": round(total_task_ms(full_r), 2),
        "strip_build_full_schema_json_ms": strip_r.full_schema_json_build_ms,
        "full_build_full_schema_json_ms": full_r.full_schema_json_build_ms,
        "strip_list_tools_ms": strip_r.list_tools_ms,
        "full_list_tools_ms": full_r.list_tools_ms,
        "token_savings_stage1_vs_all_tools_json_strip": (
            strip_r.all_tools_full_schemas_json_approx_tokens
            - strip_r.stage1_list_tools_text_approx_tokens
        ),
        "edge_tasks_expected_pass_strip": sum(
            1 for t in strip_r.tasks if t.expect_failure and t.outcome_passed
        ),
        "edge_tasks_expected_pass_full": sum(
            1 for t in full_r.tasks if t.expect_failure and t.outcome_passed
        ),
        "tasks_passed_strip": sum(1 for t in strip_r.tasks if t.outcome_passed),
        "tasks_passed_full": sum(1 for t in full_r.tasks if t.outcome_passed),
    }

    return {
        "iteration": iteration_index,
        "scenarios_built": len(scenarios),
        "scenario_task_ids": [s.id for s in scenarios],
        "strip": asdict(strip_r),
        "full": asdict(full_r),
        "comparison": comparison,
    }


async def run_benchmark(
    iterations: int,
) -> dict[str, Any]:
    discovered = discover_node_mcp_servers(ROOT)
    missing = {"playwright", "wiki", "memory"} - {d.server_id for d in discovered}
    if missing:
        raise SystemExit(
            f"Missing MCP servers (need playwright, wiki, memory). "
            f"Found: {[d.server_id for d in discovered]}. Missing: {sorted(missing)}. "
            f"Run: cd {ROOT} && npm install && npx playwright install chromium"
        )

    per_iteration: list[dict[str, Any]] = []
    wall0 = time.perf_counter()

    for it in range(1, iterations + 1):
        t_iter0 = time.perf_counter()
        row = await run_single_iteration(discovered, it)
        row["iteration_wall_ms"] = round((time.perf_counter() - t_iter0) * 1000, 2)
        per_iteration.append(row)

    total_wall_ms = round((time.perf_counter() - wall0) * 1000, 2)

    strip_startups = [r["comparison"]["strip_startup_ms"] for r in per_iteration]
    full_startups = [r["comparison"]["full_startup_ms"] for r in per_iteration]
    strip_sums = [r["comparison"]["strip_sum_task_ms"] for r in per_iteration]
    full_sums = [r["comparison"]["full_sum_task_ms"] for r in per_iteration]
    iter_walls = [r["iteration_wall_ms"] for r in per_iteration]

    summary = {
        "iterations": iterations,
        "total_suite_wall_ms": total_wall_ms,
        "strip_startup_ms": _agg("strip_startup_ms", strip_startups),
        "full_startup_ms": _agg("full_startup_ms", full_startups),
        "strip_sum_task_ms": _agg("strip_sum_task_ms", strip_sums),
        "full_sum_task_ms": _agg("full_sum_task_ms", full_sums),
        "iteration_wall_ms": _agg("iteration_wall_ms", iter_walls),
        "strip_stage1_tokens": per_iteration[0]["comparison"]["strip_stage1_tokens"],
        "full_stage1_tokens": per_iteration[0]["comparison"]["full_stage1_tokens"],
        "all_tools_json_tokens_sample": per_iteration[0]["comparison"][
            "strip_all_tools_json_tokens"
        ],
    }

    return {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": {
            "iterations": iterations,
            "suite_version": 2,
            "silent_default": True,
        },
        "discovery": [
            {"package": d.package_name, "server_id": d.server_id, "command": d.command}
            for d in discovered
        ],
        "summary": summary,
        "per_iteration": per_iteration,
    }


def run_sync(iterations: int) -> dict[str, Any]:
    return asyncio.run(run_benchmark(iterations))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Strip vs full-schema MCP benchmark (single or multi-iteration)."
    )
    parser.add_argument(
        "--iterations",
        "-n",
        type=int,
        default=1,
        help="Number of full benchmark passes (default: 1).",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="JSON report path (default: strip_vs_full_benchmark_report.json or aggregate name).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print human-readable summary (default: silent).",
    )
    args = parser.parse_args()

    if args.iterations < 1:
        parser.error("--iterations must be >= 1")

    default_out = (
        ROOT / "examples" / "strip_benchmark_aggregate_report.json"
        if args.iterations > 1
        else ROOT / "examples" / "strip_vs_full_benchmark_report.json"
    )
    out_path = args.output or default_out

    logging.getLogger("strip_mcp").setLevel(logging.CRITICAL)
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)

    payload = run_sync(args.iterations)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if args.verbose:
        print("=== Strip vs full-schema benchmark ===\n")
        print(f"Iterations: {args.iterations}")
        print(f"Wrote: {out_path}")
        if "summary" in payload:
            s = payload["summary"]
            print(f"Total suite wall: {s['total_suite_wall_ms']} ms")
            print(
                f"strip_sum_task_ms mean: {s['strip_sum_task_ms'].get('mean')} "
                f"(min {s['strip_sum_task_ms'].get('min')}, max {s['strip_sum_task_ms'].get('max')})"
            )
            print(
                f"full_sum_task_ms mean: {s['full_sum_task_ms'].get('mean')} "
                f"(min {s['full_sum_task_ms'].get('min')}, max {s['full_sum_task_ms'].get('max')})"
            )


if __name__ == "__main__":
    main()
