# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (dev)
pip install -e ".[dev]"

# Run all tests
pytest -q

# Run a single test file
pytest -q tests/test_orchestrator.py

# Run a single test
pytest -q tests/test_orchestrator.py::test_name

# CLI (after install)
toolgate setup --help
toolgate setup --apps claude,cursor --apply
```

Node.js is optional — only needed for `discover_node_mcp_servers` and the `examples/` scripts.

## Architecture

**toolgate** is a zero-dependency Python MCP middleware that delivers tool information in stages to reduce token overhead. The core idea: send lightweight tool briefs first (Stage 1), fetch full schemas only for tools the LLM wants to use (Stage 2), then execute (Stage 3).

### Three-stage flow

```
Stage 1  →  list_tools()        → list[ToolBrief]    (name, description, requires_params)
Stage 2  →  get_schemas(names)  → list[ToolSchema]   (full inputSchema, on demand)
Stage 3  →  call(name, args)    → ToolResult         (MCP content array)
```

`staged=True` (default) keeps Stage 1 minimal. `staged=False` embeds full schemas in Stage 1 (legacy full-list mode).

### Core components

| File | Role |
|------|------|
| `core.py` | `ToolGate` — async orchestrator, lifecycle, public API |
| `server.py` | `ServerHandle` — per-server connection, schema cache, brief generation |
| `registry.py` | `ToolRegistry` — namespaced name → server_id, collision detection, Levenshtein suggestions |
| `types.py` | `ToolBrief`, `ToolSchema`, `ToolResult` dataclasses |
| `errors.py` | Exception hierarchy rooted at `ToolGateError` |
| `sync.py` | `SyncToolGate` — blocking wrapper with dedicated event loop |
| `connection/stdio.py` | `StdioConnection` — subprocess JSON-RPC 2.0 over stdin/stdout |
| `connection/http.py` | `HTTPConnection` — Phase 2 stub, raises `NotImplementedError` |
| `node_discovery.py` | Discover Node.js MCP packages from `package.json`/`node_modules` |
| `cli.py` | `toolgate setup` — macOS Claude/Cursor config preview/apply |
| `setup/discovery.py` | Local + global npm MCP discovery, dedup (local preferred) |
| `setup/hosts.py` | `HostAdapter` for Claude/Cursor — merge, atomic write, backup |
| `setup/models.py` | `DiscoveredMCP`, `DiscoveredApp`, `PlannedChange`, `ApplyResult` |

### Lifecycle

1. Create `ToolGate()`, call `add_server(server_id, command=[...])` for each server (before `start()`)
2. `await start()` — spawns subprocesses, runs MCP handshake, builds `ToolRegistry`
3. Use the 3-stage API; `await stop()` to clean up
4. Context manager (`async with`) supported

### Namespacing

Tools are namespaced as `{server_id}__{raw_name}` by default (`namespace=True`). Set `namespace=False` only when collision risk is managed externally. `ToolRegistry` raises `ToolCollisionError` on duplicate registration.

### Setup CLI design

`toolgate setup` is macOS-only (v1). It:
- Discovers installed Node MCPs from local `node_modules` and global npm
- Locates Claude/Cursor config files (with env-var overrides `TOOLGATE_CLAUDE_CONFIG`, `TOOLGATE_CURSOR_CONFIG`)
- Builds a merge plan; defaults to preview (no write) unless `--apply` passed
- Writes atomically (`os.replace` via `.tmp` file) with `.bak.{ISO8601}` backups
- Tracks managed entries in `_toolgateManaged` metadata to avoid overwriting user-managed servers

### Tests

- `tests/mock_mcp_server.py` — minimal stdio MCP server, configurable tool count/latency/failures
- `tests/conftest.py` — fixtures for mock server commands
- `pytest.ini_options` sets `asyncio_mode = "auto"` — no manual `@pytest.mark.asyncio` needed
