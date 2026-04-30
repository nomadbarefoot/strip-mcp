"""Compare ToolGate staged delivery vs full-schema-upfront MCP.

Uses discover_node_mcp_servers() for Node MCPs under node_modules.

Examples:
  python examples/toolgate_vs_full_benchmark.py
  python examples/toolgate_vs_full_benchmark.py --iterations 12 --output examples/report.json
  python examples/toolgate_vs_full_benchmark.py -v --iterations 1

Default: silent (no stdout). Use -v / --verbose for human-readable summary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import statistics
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toolgate import ToolGate
from toolgate.errors import ToolGateError
from toolgate.node_discovery import DiscoveredNodeServer, discover_node_mcp_servers

ROOT = Path(__file__).resolve().parent.parent

WORKFLOW_INTERPRETATION: dict[str, str] = {
    "tokenizer": (
        "Token counts use approx_tokens = max(1, len(utf8_text)//4). This is a rough byte/char proxy, "
        "not a real BPE/tokenizer count; use for ratios and deltas, not billing."
    ),
    "naive_full_registry": (
        "Real-world analogue: the client puts every tool's name, description, and full inputSchema JSON "
        "in the system prompt (or first-turn tool payload) before the model plans any call. "
        "Benchmark proxy: all_tools_full_schemas_json_approx_tokens (pretty-printed JSON of all tools)."
    ),
    "staged_workflow": (
        "Real-world analogue: Stage 1 exposes a compact catalog (list_tools_text: names, descriptions, "
        "param hints). Each tool's inputSchema is materialized only when the agent needs to call it. "
        "Benchmark proxy: stage1_list_tools_text_approx_tokens plus JSON Schema once per distinct tool "
        "name executed in the scenario (workflow_catalog_plus_unique_schemas_approx_tokens)."
    ),
    "schema_refetch_penalty": (
        "If the client re-attaches full inputSchema on every tool invocation instead of caching per tool, "
        "token cost moves toward workflow_catalog_plus_schema_on_every_tool_call_approx_tokens. "
        "The delta vs the unique-schema workflow is reported as approx_extra_tokens_schema_every_call_vs_unique_cached."
    ),
    "latency_note": (
        "Scenario wall times are dominated by Wikipedia and browser I/O. Latency is not a clean proxy for "
        "staging overhead; prefer token proxies for comparing catalog delivery strategies."
    ),
    "playwright_headless": (
        "Playwright MCP is launched with --headless --isolated (and PLAYWRIGHT_MCP_HEADLESS=1) so Chromium "
        "runs in the background with an in-memory profile—no visible browser window and no persistent MCP profile."
    ),
    "silent_stdio": (
        "Unless you pass -v/--verbose, this process redirects Python stdout/stderr to os.devnull during the "
        "benchmark so library chatter does not interrupt your terminal; MCP subprocess stderr stays DEBUG-only "
        "in toolgate loggers (raised to CRITICAL for the run)."
    ),
    "workflow_profiles": (
        "Each iteration uses a named workflow_profile (low_tool_flash, wiki_heavy, browser_heavy, memory_heavy, "
        "balanced, wiki_short_browser_micro, full_stack_complex, mixed_stress) cycling in order—different site "
        "URLs, Wikipedia depth, memory graph complexity, and tool-call counts to approximate high vs low usage."
    ),
}


def _with_headless_playwright(servers: list[DiscoveredNodeServer]) -> list[DiscoveredNodeServer]:
    """No headed browser: --headless --isolated on Playwright MCP only."""
    out: list[DiscoveredNodeServer] = []
    for d in servers:
        if d.server_id == "playwright":
            cmd = list(d.command)
            for flag in ("--headless", "--isolated"):
                if flag not in cmd:
                    cmd.append(flag)
            out.append(replace(d, command=cmd))
        else:
            out.append(d)
    return out


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
    mode: Literal["staged", "full"]
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
    unique_tool_schemas_approx_tokens: int
    workflow_catalog_plus_unique_schemas_approx_tokens: int
    workflow_catalog_plus_schema_on_every_tool_call_approx_tokens: int
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


# Public-ish sites only; rotated per iteration / profile.
_BENCHMARK_URLS: list[str] = [
    "https://example.com",
    "https://example.org",
    "https://www.iana.org/help/example-domains",
    "https://httpbin.org/get",
    "https://jsonplaceholder.typicode.com/",
    "https://www.w3.org/",
    "https://developer.mozilla.org/en-US/",
    "https://github.com",
    "https://registry.npmjs.org/",
    "https://playwright.dev/",
]

WORKFLOW_PROFILES: tuple[str, ...] = (
    "low_tool_flash",
    "wiki_heavy",
    "browser_heavy",
    "memory_heavy",
    "balanced",
    "wiki_short_browser_micro",
    "full_stack_complex",
    "mixed_stress",
)


def _browser_hops(urls: list[str]) -> list[tuple[str, dict[str, Any] | None]]:
    steps: list[tuple[str, dict[str, Any] | None]] = []
    for u in urls:
        steps.append(("playwright__browser_navigate", {"url": u}))
        steps.append(("playwright__browser_snapshot", {}))
    return steps


def _edge_unknown() -> ScenarioTask:
    return ScenarioTask(
        "edge_unknown_tool",
        "Edge: namespaced tool that does not exist (expect ToolNotFound)",
        [("wiki__toolgate_benchmark_nonexistent_tool_xyz", {})],
        expect_failure=True,
    )


def _edge_empty() -> ScenarioTask:
    return ScenarioTask(
        "edge_empty_search",
        "Edge: Wikipedia search with empty query string",
        [("wiki__search", {"query": ""})],
    )


def _edge_double_read() -> ScenarioTask:
    return ScenarioTask(
        "edge_idempotent_graph",
        "Edge: read_graph twice in a row (idempotent)",
        [
            ("memory__read_graph", {}),
            ("memory__read_graph", {}),
        ],
    )


def build_scenarios(iteration_index: int) -> tuple[list[ScenarioTask], str]:
    """Varied workflow profiles: tool volume, sites, Wikipedia depth, memory complexity."""
    i = iteration_index
    profile = WORKFLOW_PROFILES[(i - 1) % len(WORKFLOW_PROFILES)]
    tag = f"bench-i{i}"
    wq = _wiki_queries()
    wt = _wiki_titles()
    qi, ti = i % len(wq), i % len(wt)
    q0, q1, q2 = wq[qi], wq[(qi + 4) % len(wq)], wq[(qi + 9) % len(wq)]
    t0, t1 = wt[ti], wt[(ti + 5) % len(wt)]
    rot = [(i + k) % len(_BENCHMARK_URLS) for k in range(len(_BENCHMARK_URLS))]
    u = [_BENCHMARK_URLS[k] for k in rot]

    ea = f"BenchA-{tag}"
    eb = f"BenchB-{tag}"

    if profile == "low_tool_flash":
        tasks = [
            ScenarioTask(
                "low_mem_read",
                "Low usage: memory read_graph only",
                [("memory__read_graph", {})],
            ),
            ScenarioTask(
                "low_browser_one_page",
                "Low usage: one navigate + snapshot",
                _browser_hops([u[0]]),
            ),
            _edge_unknown(),
        ]
    elif profile == "wiki_heavy":
        tasks = [
            ScenarioTask(f"wh_s0_{i}", "Wiki-heavy: search A", [("wiki__search", {"query": q0})]),
            ScenarioTask(f"wh_s1_{i}", "Wiki-heavy: search B", [("wiki__search", {"query": q1})]),
            ScenarioTask(f"wh_s2_{i}", "Wiki-heavy: search C", [("wiki__search", {"query": q2})]),
            ScenarioTask(f"wh_a0_{i}", "Wiki-heavy: article A", [("wiki__readArticle", {"title": t0})]),
            ScenarioTask(f"wh_a1_{i}", "Wiki-heavy: article B", [("wiki__readArticle", {"title": t1})]),
            _edge_empty(),
        ]
    elif profile == "browser_heavy":
        hops = u[0:6]
        tasks = [
            ScenarioTask(
                f"bh_chain_{i}",
                f"Browser-heavy: {len(hops)} sites × (navigate + snapshot)",
                _browser_hops(hops),
            ),
            _edge_double_read(),
        ]
    elif profile == "memory_heavy":
        tasks = [
            ScenarioTask(
                f"mh_create_{i}",
                "Memory-heavy: entities, relation, observations, search, read",
                [
                    (
                        "memory__create_entities",
                        {
                            "entities": [
                                {
                                    "name": ea,
                                    "entityType": "benchmark",
                                    "observations": [f"iter={i}", "node A"],
                                },
                                {
                                    "name": eb,
                                    "entityType": "benchmark",
                                    "observations": [f"iter={i}", "node B"],
                                },
                            ]
                        },
                    ),
                    (
                        "memory__create_relations",
                        {
                            "relations": [
                                {
                                    "from": ea,
                                    "to": eb,
                                    "relationType": "benchmark_linked_to",
                                }
                            ]
                        },
                    ),
                    (
                        "memory__add_observations",
                        {
                            "observations": [
                                {
                                    "entityName": ea,
                                    "contents": [f"obs-extra-{i}", "post-relation note"],
                                }
                            ]
                        },
                    ),
                    ("memory__search_nodes", {"query": "Bench"}),
                    ("memory__read_graph", {}),
                ],
            ),
            _edge_unknown(),
        ]
    elif profile == "balanced":
        url_a, url_b = u[1], u[3]
        tasks = [
            ScenarioTask(
                f"bal_s_{i}",
                f"Balanced: Wikipedia search: {q0[:32]}…",
                [("wiki__search", {"query": q0})],
            ),
            ScenarioTask(
                f"bal_a_{i}",
                f"Balanced: article {t0}",
                [("wiki__readArticle", {"title": t0})],
            ),
            ScenarioTask(
                f"bal_mem_{i}",
                "Balanced: unicode memory + recall",
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
                f"bal_br_{i}",
                "Balanced: browser two sites",
                _browser_hops([url_a, url_b]),
            ),
            _edge_unknown(),
            _edge_empty(),
            _edge_double_read(),
        ]
    elif profile == "wiki_short_browser_micro":
        tasks = [
            ScenarioTask(
                f"wsm_s_{i}",
                "Short: single Wikipedia search",
                [("wiki__search", {"query": q1})],
            ),
            ScenarioTask(
                f"wsm_br_{i}",
                "Short: two quick browser hops",
                _browser_hops([u[2], u[5]]),
            ),
            _edge_double_read(),
        ]
    elif profile == "full_stack_complex":
        tasks = [
            ScenarioTask(
                f"fsc_s_{i}", "Complex: search", [("wiki__search", {"query": q2})]
            ),
            ScenarioTask(
                f"fsc_a_{i}",
                "Complex: article",
                [("wiki__readArticle", {"title": t1})],
            ),
            ScenarioTask(
                f"fsc_mem_{i}",
                "Complex: memory graph",
                [
                    (
                        "memory__create_entities",
                        {
                            "entities": [
                                {
                                    "name": f"Cx-{tag}",
                                    "entityType": "task",
                                    "observations": ["full stack run", "unicode: öæß"],
                                }
                            ]
                        },
                    ),
                    ("memory__search_nodes", {"query": "Cx-"}),
                    ("memory__read_graph", {}),
                ],
            ),
            ScenarioTask(
                f"fsc_br_{i}",
                "Complex: four browser hops",
                _browser_hops(u[0:4]),
            ),
            _edge_unknown(),
            _edge_empty(),
        ]
    elif profile == "mixed_stress":
        mx1, mx2 = f"Mx1-{tag}", f"Mx2-{tag}"
        tasks = [
            ScenarioTask(
                f"ms_s1_{i}", "Mixed: search 1", [("wiki__search", {"query": q0})]
            ),
            ScenarioTask(
                f"ms_s2_{i}", "Mixed: search 2", [("wiki__search", {"query": q2})]
            ),
            ScenarioTask(
                f"ms_a_{i}",
                "Mixed: article",
                [("wiki__readArticle", {"title": t0})],
            ),
            ScenarioTask(
                f"ms_mem_{i}",
                "Mixed: entities + relation + read",
                [
                    (
                        "memory__create_entities",
                        {
                            "entities": [
                                {
                                    "name": mx1,
                                    "entityType": "stress",
                                    "observations": ["mixed"],
                                },
                                {
                                    "name": mx2,
                                    "entityType": "stress",
                                    "observations": ["mixed"],
                                },
                            ]
                        },
                    ),
                    (
                        "memory__create_relations",
                        {
                            "relations": [
                                {
                                    "from": mx1,
                                    "to": mx2,
                                    "relationType": "paired_with",
                                }
                            ]
                        },
                    ),
                    ("memory__read_graph", {}),
                ],
            ),
            ScenarioTask(
                f"ms_br_{i}",
                "Mixed: three browser hops",
                _browser_hops([u[6], u[7], u[8]]),
            ),
            _edge_unknown(),
            _edge_empty(),
        ]
    else:
        tasks = []

    return tasks, profile


async def build_full_schemas_json(mcp: ToolGate, tools: list) -> str:
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
    mcp: ToolGate,
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
    mcp: ToolGate,
    scenarios: list[ScenarioTask],
) -> tuple[list[TaskRecord], list[str], int, int]:
    tasks_out: list[TaskRecord] = []
    failure_summary: list[str] = []
    agent_schema_chars = 0
    unique_schema_chars = 0
    seen_tool_names: set[str] = set()

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
                if tool_name not in seen_tool_names:
                    seen_tool_names.add(tool_name)
                    unique_schema_chars += sch_chars
                tr.steps.append(step)
        except ToolGateError as e:
            _record_task_error(tr, sc, e, failure_summary)
        except Exception as e:
            _record_task_error(tr, sc, e, failure_summary)

        tr.total_ms = round((time.perf_counter() - t_task0) * 1000, 2)
        tr.outcome_passed = (not tr.ok) if sc.expect_failure else tr.ok
        tasks_out.append(tr)

    return tasks_out, failure_summary, agent_schema_chars, unique_schema_chars


async def run_one_mode(
    staged: bool,
    discovered: list,
    scenarios: list[ScenarioTask],
) -> ModeReport:
    mode: Literal["staged", "full"] = "staged" if staged else "full"

    t0 = time.perf_counter()
    async with ToolGate(default_timeout=120.0) as mcp:
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

        tasks_out, failure_summary, agent_schema_chars, unique_schema_chars = (
            await _run_scenario_tasks(mcp, scenarios)
        )

        agent_tok = agent_schema_chars // 4
        unique_tok = unique_schema_chars // 4
        workflow_cat_unique = stage1_tok + unique_tok
        workflow_cat_every_call = stage1_tok + agent_tok
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
            full_vs_agent_used_ratio=(
                round(full_tok / max(1, agent_tok), 2) if agent_tok else 0.0
            ),
            unique_tool_schemas_approx_tokens=unique_tok,
            workflow_catalog_plus_unique_schemas_approx_tokens=workflow_cat_unique,
            workflow_catalog_plus_schema_on_every_tool_call_approx_tokens=(
                workflow_cat_every_call
            ),
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
    scenarios, workflow_profile = build_scenarios(iteration_index)
    planned_calls = sum(len(s.steps) for s in scenarios)

    staged_r = await run_one_mode(True, discovered, scenarios)
    full_r = await run_one_mode(False, discovered, scenarios)

    upfront = staged_r.all_tools_full_schemas_json_approx_tokens
    staged_wf = staged_r.workflow_catalog_plus_unique_schemas_approx_tokens
    comparison = {
        "staged_stage1_tokens": staged_r.stage1_list_tools_text_approx_tokens,
        "full_stage1_tokens": full_r.stage1_list_tools_text_approx_tokens,
        "staged_all_tools_json_tokens": staged_r.all_tools_full_schemas_json_approx_tokens,
        "full_all_tools_json_tokens": full_r.all_tools_full_schemas_json_approx_tokens,
        "naive_full_registry_prompt_approx_tokens": upfront,
        "staged_workflow_prompt_approx_tokens": staged_wf,
        "approx_tokens_saved_staged_vs_naive_upfront": upfront - staged_wf,
        "approx_ratio_naive_upfront_to_staged_workflow": (
            round(upfront / staged_wf, 2) if staged_wf else 0.0
        ),
        "staged_unique_executed_tool_schemas_approx_tokens": (
            staged_r.unique_tool_schemas_approx_tokens
        ),
        "approx_extra_tokens_schema_every_call_vs_unique_cached": (
            staged_r.workflow_catalog_plus_schema_on_every_tool_call_approx_tokens
            - staged_wf
        ),
        "full_mode_catalog_plus_unique_approx_tokens": (
            full_r.workflow_catalog_plus_unique_schemas_approx_tokens
        ),
        "staged_startup_ms": staged_r.startup_ms,
        "full_startup_ms": full_r.startup_ms,
        "staged_sum_task_ms": round(total_task_ms(staged_r), 2),
        "full_sum_task_ms": round(total_task_ms(full_r), 2),
        "staged_build_full_schema_json_ms": staged_r.full_schema_json_build_ms,
        "full_build_full_schema_json_ms": full_r.full_schema_json_build_ms,
        "staged_list_tools_ms": staged_r.list_tools_ms,
        "full_list_tools_ms": full_r.list_tools_ms,
        "token_savings_stage1_vs_all_tools_json_staged": (
            staged_r.all_tools_full_schemas_json_approx_tokens
            - staged_r.stage1_list_tools_text_approx_tokens
        ),
        "edge_tasks_expected_pass_staged": sum(
            1 for t in staged_r.tasks if t.expect_failure and t.outcome_passed
        ),
        "edge_tasks_expected_pass_full": sum(
            1 for t in full_r.tasks if t.expect_failure and t.outcome_passed
        ),
        "tasks_passed_staged": sum(1 for t in staged_r.tasks if t.outcome_passed),
        "tasks_passed_full": sum(1 for t in full_r.tasks if t.outcome_passed),
    }

    return {
        "iteration": iteration_index,
        "workflow_profile": workflow_profile,
        "planned_mcp_tool_calls": planned_calls,
        "scenarios_built": len(scenarios),
        "scenario_task_ids": [s.id for s in scenarios],
        "toolgate": asdict(staged_r),
        "full": asdict(full_r),
        "comparison": comparison,
    }


async def run_benchmark(
    iterations: int,
) -> dict[str, Any]:
    os.environ.setdefault("PLAYWRIGHT_MCP_HEADLESS", "1")
    discovered = _with_headless_playwright(discover_node_mcp_servers(ROOT))
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

    staged_startups = [r["comparison"]["staged_startup_ms"] for r in per_iteration]
    full_startups = [r["comparison"]["full_startup_ms"] for r in per_iteration]
    staged_sums = [r["comparison"]["staged_sum_task_ms"] for r in per_iteration]
    full_sums = [r["comparison"]["full_sum_task_ms"] for r in per_iteration]
    iter_walls = [r["iteration_wall_ms"] for r in per_iteration]

    saved_vs_naive = [
        float(r["comparison"]["approx_tokens_saved_staged_vs_naive_upfront"])
        for r in per_iteration
    ]
    ratio_naive_staged = [
        float(r["comparison"]["approx_ratio_naive_upfront_to_staged_workflow"])
        for r in per_iteration
    ]

    summary = {
        "iterations": iterations,
        "total_suite_wall_ms": total_wall_ms,
        "staged_startup_ms": _agg("staged_startup_ms", staged_startups),
        "full_startup_ms": _agg("full_startup_ms", full_startups),
        "staged_sum_task_ms": _agg("staged_sum_task_ms", staged_sums),
        "full_sum_task_ms": _agg("full_sum_task_ms", full_sums),
        "iteration_wall_ms": _agg("iteration_wall_ms", iter_walls),
        "staged_stage1_tokens": per_iteration[0]["comparison"]["staged_stage1_tokens"],
        "full_stage1_tokens": per_iteration[0]["comparison"]["full_stage1_tokens"],
        "all_tools_json_tokens_sample": per_iteration[0]["comparison"][
            "staged_all_tools_json_tokens"
        ],
        "naive_full_registry_prompt_approx_tokens_sample": per_iteration[0]["comparison"][
            "naive_full_registry_prompt_approx_tokens"
        ],
        "staged_workflow_prompt_approx_tokens_sample": per_iteration[0]["comparison"][
            "staged_workflow_prompt_approx_tokens"
        ],
        "approx_tokens_saved_staged_vs_naive_upfront": _agg(
            "approx_tokens_saved_staged_vs_naive_upfront", saved_vs_naive
        ),
        "approx_ratio_naive_upfront_to_staged_workflow": _agg(
            "approx_ratio_naive_upfront_to_staged_workflow", ratio_naive_staged
        ),
    }

    return {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": {
            "iterations": iterations,
            "suite_version": 4,
            "silent_default": True,
            "playwright_mcp_flags": ["--headless", "--isolated"],
            "playwright_env": {"PLAYWRIGHT_MCP_HEADLESS": "1"},
        },
        "workflow_interpretation": WORKFLOW_INTERPRETATION,
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
        description="Staged vs full-schema MCP benchmark (single or multi-iteration)."
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
        help="JSON report path (default: toolgate_vs_full_benchmark_report.json or aggregate name).",
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
        ROOT / "examples" / "toolgate_benchmark_aggregate_report.json"
        if args.iterations > 1
        else ROOT / "examples" / "toolgate_vs_full_benchmark_report.json"
    )
    out_path = args.output or default_out

    logging.getLogger("toolgate").setLevel(logging.CRITICAL)
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)

    if args.verbose:
        payload = run_sync(args.iterations)
    else:
        with open(os.devnull, "w", encoding="utf-8") as _dn_out:
            with open(os.devnull, "w", encoding="utf-8") as _dn_err:
                with redirect_stdout(_dn_out), redirect_stderr(_dn_err):
                    payload = run_sync(args.iterations)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if args.verbose:
        print("=== Staged vs full-schema benchmark ===\n")
        print(f"Iterations: {args.iterations}")
        print(f"Wrote: {out_path}")
        p0 = payload["per_iteration"][0]
        print(f"First iteration workflow_profile: {p0.get('workflow_profile')}")
        print(f"Planned MCP tool calls (iter 1): {p0.get('planned_mcp_tool_calls')}")
        if "summary" in payload:
            s = payload["summary"]
            print(f"Total suite wall: {s['total_suite_wall_ms']} ms")
            print(
                f"staged_sum_task_ms mean: {s['staged_sum_task_ms'].get('mean')} "
                f"(min {s['staged_sum_task_ms'].get('min')}, max {s['staged_sum_task_ms'].get('max')})"
            )
            print(
                f"full_sum_task_ms mean: {s['full_sum_task_ms'].get('mean')} "
                f"(min {s['full_sum_task_ms'].get('min')}, max {s['full_sum_task_ms'].get('max')})"
            )
            print("\n--- Workflow token proxies (see workflow_interpretation in JSON) ---")
            c0 = payload["per_iteration"][0]["comparison"]
            print(
                f"Naive full-registry prompt ~tokens: {c0['naive_full_registry_prompt_approx_tokens']}"
            )
            print(
                f"ToolGate staged workflow ~tokens:  {c0['staged_workflow_prompt_approx_tokens']}"
            )
            print(
                f"Approx saved (naive − staged):      {c0['approx_tokens_saved_staged_vs_naive_upfront']}"
            )
            print(
                f"Approx ratio naive / staged:        {c0['approx_ratio_naive_upfront_to_staged_workflow']}"
            )
            print(
                f"Extra ~tokens if schema every call: {c0['approx_extra_tokens_schema_every_call_vs_unique_cached']}"
            )


if __name__ == "__main__":
    main()
