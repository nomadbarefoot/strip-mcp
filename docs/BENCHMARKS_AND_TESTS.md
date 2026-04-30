# Benchmarks and Tests

Reference for toolgate performance benchmarks and the pytest suite. Generated JSON under `examples/` is gitignored unless force-added.

---

## 1. Key results (32 tools, 8 workflow profiles)

| Metric | Value |
|--------|-------|
| **Naive full-registry prompt** (all schemas upfront) | ~**6,343** tokens |
| **Strip staged workflow** (Stage 1 + only schemas used) | ~**861** tokens |
| **Token reduction** | ~**7.4×** fewer tokens |
| **Tokens saved per workflow** | ~**5,300** |

These are approximate (`len(utf8_text) // 4`), not vendor-tokenizer counts. Use for ratios and deltas.

The ratio varies with tool diversity used in a workflow (5.2×–7.5× across profiles). The heavier the tool catalog relative to the tools actually invoked, the larger the saving.

---

## 2. Running benchmarks

```bash
# Install
pip install -e ".[dev]"
npm install && npx playwright install chromium

# Single run, verbose summary
python examples/toolgate_vs_full_benchmark.py --iterations 1 -v

# 8-iteration aggregate (one full profile cycle)
python examples/toolgate_vs_full_benchmark.py --iterations 8 --output examples/toolgate_benchmark_aggregate_report.json

# Legacy single-pass multi-MCP script
python examples/multi_mcp_agent_benchmark.py
```

Servers required: `@playwright/mcp`, `wikipedia-mcp`, `@modelcontextprotocol/server-memory` (all under `node_modules`).

---

## 3. Benchmark scripts

| Script | Role | Output |
|--------|------|--------|
| `toolgate_vs_full_benchmark.py` | Staged vs `staged=False`; workflow profiles; token + timing metrics | `toolgate_vs_full_benchmark_report.json` or `-o` path |
| `multi_mcp_agent_benchmark.py` | Single-pass scripted tasks across 3 servers | `multi_mcp_benchmark_report.json` |
| `benchmark_tokens.py` | 50-tool mock: Stage 1 vs full-schema token proxy | stdout only |
| `playwright_smoke.py`, `basic_usage.py` | Manual smoke tests | — |

---

## 4. Aggregate benchmark (8 iterations, 2026-04-06)

`python examples/toolgate_vs_full_benchmark.py -n 8 -o examples/toolgate_benchmark_aggregate_report.json`

| Field | Value |
|-------|-------|
| **Servers** | `memory`, `playwright`, `wiki` — **32** namespaced tools |
| **Playwright** | `node …/cli.js --headless --isolated` + `PLAYWRIGHT_MCP_HEADLESS=1` |
| **Total suite wall** | **79,254 ms** |

### 4.1 Timing (8 iterations)

| Metric | Min | Max | Mean | Stdev |
|--------|-----|-----|------|-------|
| `staged_startup_ms` | 396.8 | 438.2 | 410.6 | 14.1 |
| `full_startup_ms` | 379.1 | 409.9 | 398.9 | 9.6 |
| `staged_sum_task_ms` | 8.0 | 9,994.9 | 4,855.4 | 3,402.0 |
| `full_sum_task_ms` | 7.3 | 7,941.8 | 4,151.8 | 2,764.0 |
| `iteration_wall_ms` | 832.5 | 18,839.6 | 9,906.7 | 6,171.0 |

Wall time is dominated by Wikipedia and Playwright I/O — not a clean proxy for staging overhead. Use token proxies for comparing catalog delivery strategies.

### 4.2 Token proxies (8 iterations)

| Metric | Min | Max | Mean | Stdev |
|--------|-----|-----|------|-------|
| `approx_tokens_saved_staged_vs_naive_upfront` | 5,121 | 5,496 | 5,327.8 | 158.5 |
| `approx_ratio_naive_upfront_to_staged_workflow` | 5.19 | 7.49 | 6.38 | 0.99 |

**Fixed catalog size (any iteration):** Stage 1 staged **717** / full **762** tokens; naive full-registry blob **6,343** tokens.

### 4.3 Per-iteration breakdown

| # | Profile | MCP calls | Strip ~tokens | Saved vs naive | Ratio | Strip tasks ms |
|---|---------|----------:|---------------:|----------------:|------:|---------------:|
| 1 | `low_tool_flash` | 4 | 861 | 5,482 | 7.37× | 2,074 |
| 2 | `wiki_heavy` | 6 | 847 | 5,496 | 7.49× | 5,044 |
| 3 | `browser_heavy` | 14 | 861 | 5,482 | 7.37× | 8,269 |
| 4 | `memory_heavy` | 6 | 1,103 | 5,240 | 5.75× | 8 |
| 5 | `balanced` | 13 | 1,156 | 5,187 | 5.49× | 3,801 |
| 6 | `wiki_short_browser_micro` | 7 | 916 | 5,427 | 6.92× | 2,529 |
| 7 | `full_stack_complex` | 15 | 1,156 | 5,187 | 5.49× | 7,125 |
| 8 | `mixed_stress` | 14 | 1,222 | 5,121 | 5.19× | 9,995 |

---

## 5. Live in-session benchmark (2026-04-07)

Verified live via the toolgate proxy MCP server (Claude Code session, `toolgate` server connected). Same 3-stage flow, single `low_tool_flash` iteration against a running proxy.

| Stage | Call | Result |
|-------|------|--------|
| 1 — list | Deferred tool list | 22 tools, stub schemas |
| 2 — schema | `__toolgate__get_schema(playwright__browser_navigate)` | Real `inputSchema` returned |
| 3 — execute | `playwright__browser_navigate(https://example.com)` | Title "Example Domain" |

Token reading confirmed against script output: **7.37× ratio** on `low_tool_flash` (861 staged vs 6,343 naive). Schema fetch overhead: **~0 ms** (in-process cache after first call).

---

## 6. Token model

| Client pattern | Benchmark proxy |
|----------------|-----------------|
| **Naive:** all tools' name + description + full `inputSchema` JSON upfront | `naive_full_registry_prompt_approx_tokens` |
| **Staged:** compact Stage 1 list + one JSON Schema per **distinct** tool invoked | `staged_workflow_prompt_approx_tokens` |
| **Upper bound** if schema is repeated on every call (no caching) | `workflow_catalog_plus_schema_on_every_tool_call_approx_tokens` |

Full field definitions live in `workflow_interpretation` in the JSON report.

---

## 7. Workflow profiles

Iteration *n* uses profile index `(n − 1) mod 8`. JSON: `per_iteration[].workflow_profile`.

| Profile | Intent |
|---------|--------|
| `low_tool_flash` | Few calls: memory read, one browser hop, unknown-tool edge |
| `wiki_heavy` | Many Wikipedia calls: 3 searches, 2 articles, empty-query edge |
| `browser_heavy` | 6 sites × navigate + snapshot |
| `memory_heavy` | Graph: entities, relations, add_observations, search, read |
| `balanced` | Wiki + unicode memory + 2 sites + standard edges |
| `wiki_short_browser_micro` | 1 search, 2 browser hops |
| `full_stack_complex` | Wiki + memory + 4 browser hops + edges |
| `mixed_stress` | Mixed high load across wiki, memory, browser |

---

## 8. Silent run and headless config

| Behavior | Detail |
|----------|--------|
| **Default stdout/stderr** | Without `-v`, Python stdout/stderr go to `os.devnull` during the run |
| **Verbose** | `-v` / `--verbose` prints a short summary and still writes the JSON |
| **Chromium** | `--headless --isolated` on Playwright MCP; `PLAYWRIGHT_MCP_HEADLESS=1` set in-process |
| **Logging** | `toolgate` and `asyncio` loggers at CRITICAL for the benchmark process |

---

## 9. JSON field cheat sheet

| Area | Fields |
|------|--------|
| Meta | `generated_utc`, `config`, `workflow_interpretation`, `discovery` |
| Summary | `summary.*` with `_agg` blocks (`min`/`max`/`mean`/`stdev`) for timings and token deltas |
| Per iteration | `workflow_profile`, `planned_mcp_tool_calls`, `iteration_wall_ms`, `toolgate` / `full` mode reports, `comparison` |
| Tasks | `tasks[].steps[]` → `schema_fetch_ms`, `call_ms`; `expect_failure`, `outcome_passed` |

---

## 10. Git ignore

Patterns: `examples/*report*.json`, `examples/benchmark_run*.json`.

---

## 11. Unit tests

```bash
pytest -q
```

| Module | Covers |
|--------|--------|
| `test_orchestrator.py` | `ToolGate`, `ToolRegistry`, error types/messages |
| `test_transport.py` | `StdioConnection`, `ServerHandle` |
| `test_setup_discovery.py` | `discover_node_mcp_servers`, `toolgate.setup.*`, `toolgate setup` CLI |

Fixtures: `conftest.py`. Mock server: `tests/mock_mcp_server.py`.

**Last verified:** `51 passed` (`pytest -q`).

`pytest.ini_options` sets `asyncio_mode = "auto"` — no manual `@pytest.mark.asyncio` needed.
