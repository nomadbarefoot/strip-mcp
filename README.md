# strip-mcp

Python middleware for the [Model Context Protocol](https://modelcontextprotocol.io): staged tool discovery (names and descriptions first, full JSON schemas on demand) to cut token overhead from large MCP tool registries.

## Repository layout

| Path | Contents |
|------|----------|
| `src/strip_mcp/` | Library, transports (`connection/`), CLI (`cli.py`), macOS setup helpers (`setup/`) |
| `tests/` | Pytest suite |
| `examples/` | Benchmarks and runnable scripts |
| `docs/` | Benchmarks/tests notes and design write-ups |

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Python and Node

The **Python package** has no runtime dependency on Node. **`npm install`** at the repo root is **optional**: it only pulls npm MCP packages used by `examples/` (Playwright, Wikipedia, memory, etc.) and for `discover_node_mcp_servers(project_root)`. See [CONTRIBUTING.md](CONTRIBUTING.md) for contributor expectations.

Optional Node MCPs for examples:

```bash
npm install
npx playwright install chromium
```

See `examples/` for usage. **Tests, benchmarks, and metrics:** [`docs/BENCHMARKS_AND_TESTS.md`](docs/BENCHMARKS_AND_TESTS.md). Design notes: [`docs/STRIP_V2_plan.md`](docs/STRIP_V2_plan.md).

**Node MCP auto-discovery:** `discover_node_mcp_servers(project_root)` reads `package.json` and registers known npm MCP packages whose entry files exist under `node_modules` (see `src/strip_mcp/node_discovery.py`). Known default mappings are exposed as `DEFAULT_NODE_MCP_REGISTRY` for extension.

## Setup CLI (macOS v1)

`strip-mcp setup` discovers directly installed MCP servers from:
- local `node_modules` (project root)
- global npm packages (`npm root -g`)

It then discovers Claude/Cursor config targets on macOS and previews config changes.

Default behavior is preview-only. Use `--apply` to write files.

```bash
strip-mcp setup
strip-mcp setup --apps claude,cursor
strip-mcp setup --non-interactive
strip-mcp setup --non-interactive --select playwright,wiki
strip-mcp setup --apply
strip-mcp setup --json
```

Notes:
- `--mode proxy` is reserved for future work; v1 supports `--mode direct` only.
- App config paths can be overridden for automation/tests:
  - `STRIP_MCP_CLAUDE_CONFIG=/path/to/claude-config.json`
  - `STRIP_MCP_CURSOR_CONFIG=/path/to/cursor-config.json`
