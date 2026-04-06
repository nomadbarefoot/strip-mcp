# Benchmarks and tests

Single reference for **pytest** layout, how to run benchmarks, and what gets recorded. Generated JSON benchmark dumps are **not** committed; regenerate locally and paste summary rows here when you want a snapshot in git.

## Unit and integration tests (`tests/`)

| File | Scope |
|------|--------|
| `test_orchestrator.py` | `StripMCP` (multi-server, staged vs full, refresh, cache, namespaces), `ToolRegistry`, error types and messages |
| `test_transport.py` | `StdioConnection` (handshake, call, timeouts, bad binary), `ServerHandle` (briefs, overrides, call/refresh, `staged` flag) |
| `test_setup_discovery.py` | `discover_node_mcp_servers`, `strip_mcp.setup.discovery` / `hosts`, `strip-mcp setup` CLI |

Shared fixtures: `conftest.py`. Mock MCP server: `mock_mcp_server.py`.

```bash
pytest -q
pytest tests/test_orchestrator.py -v
```

**Latest run (local):** `51 passed` — run `pytest` in a venv with `pip install -e ".[dev]"` to refresh.

---

## Benchmark scripts (`examples/`)

| Script | Purpose | Default output |
|--------|---------|----------------|
| `strip_vs_full_benchmark.py` | Staged (`strip`) vs `staged=False` (`full`) on auto-discovered Node MCPs; optional multi-iteration suite (varied queries, browser chains, unicode memory, edge cases) | Single: `examples/strip_vs_full_benchmark_report.json`; multi: `examples/strip_benchmark_aggregate_report.json` |
| `multi_mcp_agent_benchmark.py` | Scripted tasks only (legacy single-pass); same discovery as above | `examples/multi_mcp_benchmark_report.json` |
| `benchmark_tokens.py` | Mock server with 50 tools: Stage 1 vs full-schema token proxy | stdout only |
| `playwright_smoke.py` / `basic_usage.py` | Manual smoke | n/a |

**Run (silent, multi-iteration example):**

```bash
npm install && npx playwright install chromium
```

```bash
python examples/strip_vs_full_benchmark.py --iterations 12 --output examples/strip_benchmark_aggregate_report.json
python examples/strip_vs_full_benchmark.py -v --iterations 1   # verbose single pass
```

### Metrics captured (JSON)

- **Timing:** `startup_ms`, `list_tools_ms`, `full_schema_json_build_ms` (build all-tool JSON), per-task `total_ms`, per-step `schema_fetch_ms`, `call_ms`, `iteration_wall_ms` (multi-run).
- **Token proxy:** `len(text)//4` — `stage1_list_tools_text_approx_tokens`, `all_tools_full_schemas_json_approx_tokens`, schemas for tools actually used in the run.
- **Multi-iteration:** `summary.*` with `min` / `max` / `mean` / `stdev` for startup and sum-of-task times; `total_suite_wall_ms`.
- **Edge tasks:** `expect_failure` / `outcome_passed` for unknown-tool and similar rows.

**Note:** Wikipedia and browser latency dominate wall time; **do not** treat one-off strip vs full deltas as proof of staging overhead without many runs or controlled mocks.

### Snapshot (example — replace after a local run)

| Metric | Strip mean (12 runs) | Full mean (12 runs) |
|--------|----------------------|---------------------|
| `startup_ms` | ~341 | ~335 |
| `sum_task_ms` (sum of scenarios) | varies widely | varies widely |

Paste the `summary` block from `strip_benchmark_aggregate_report.json` here when you want a dated record.

---

## Git ignore

Generated benchmark JSON under `examples/` matching `*report*.json` and `benchmark_run*.json` is ignored so ad-hoc runs do not clutter commits.
