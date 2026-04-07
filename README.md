# strip-mcp

Python middleware for the [Model Context Protocol](https://modelcontextprotocol.io): staged tool discovery (names and descriptions first, full JSON schemas on demand) to cut token overhead from large MCP tool registries.

## Why

Agents using many MCP servers receive full JSON schemas for every tool on `tools/list`. Most of that schema payload is never used — the model only needs to know what tools exist at planning time, and full schemas only when building arguments. strip-mcp serves tool information in three stages:

| Stage | What the agent gets | When |
|-------|---------------------|------|
| **1 — list** | Compact briefs: name, description, `requires_params` | Discovery / planning |
| **2 — schema** | Full `inputSchema` for specific tools, on demand | Before constructing arguments |
| **3 — execute** | `tools/call` result | Same as raw MCP |

**Measured result across 8 workflow profiles (32 tools):** staged delivery uses **~6× fewer tokens** than putting all schemas in the prompt upfront (range: 5.2×–7.5× depending on how many tools are actually invoked). See [docs/BENCHMARKS_AND_TESTS.md](docs/BENCHMARKS_AND_TESTS.md).

## Repository layout

| Path | Contents |
|------|----------|
| `src/strip_mcp/` | Library, transports (`connection/`), proxy server (`proxy/`), CLI (`cli.py`), macOS setup helpers (`setup/`) |
| `tests/` | Pytest suite |
| `examples/` | Benchmarks and runnable scripts |
| `docs/` | [Architecture](docs/ARCHITECTURE.md), [benchmarks/tests](docs/BENCHMARKS_AND_TESTS.md) |

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Python and Node

The **Python package** has no runtime dependency on Node. **`npm install`** at the repo root is **optional**: it pulls npm MCP packages used by `examples/` (Playwright, Wikipedia, memory) and enables `discover_node_mcp_servers(project_root)`.

```bash
npm install
npx playwright install chromium
```

## Usage (library)

```python
from strip_mcp import StripMCP

async with StripMCP() as mcp:
    mcp.add_server("playwright", command=["node", "node_modules/@playwright/mcp/cli.js"])
    await mcp.start()

    # Stage 1 — compact list
    tools = await mcp.list_tools()

    # Stage 2 — full schema for a specific tool
    [schema] = await mcp.get_schemas(["playwright__browser_navigate"])

    # Stage 3 — execute
    result = await mcp.call("playwright__browser_navigate", {"url": "https://example.com"})
```

## Proxy server

strip-mcp ships a proxy MCP server that wraps upstream MCP servers and exposes the 3-stage API as a standard MCP interface. Configure via JSON:

```bash
strip-mcp proxy --config path/to/config.json
```

The proxy exposes all upstream tools with stub schemas at `tools/list`, plus a `__strip__get_schema` tool that returns the real `inputSchema` on demand. Any agent that supports MCP tool use can use it without code changes.

## Setup CLI (macOS v1)

`strip-mcp setup` discovers installed Node MCP servers and writes them into Claude/Cursor configs.

```bash
strip-mcp setup                          # preview
strip-mcp setup --apps claude,cursor     # filter apps
strip-mcp setup --non-interactive --select playwright,wiki
strip-mcp setup --apply                  # write configs
strip-mcp setup --json                   # machine-readable output
```

Config paths can be overridden for automation:
- `STRIP_MCP_CLAUDE_CONFIG=/path/to/claude-config.json`
- `STRIP_MCP_CURSOR_CONFIG=/path/to/cursor-config.json`

## Node MCP auto-discovery

`discover_node_mcp_servers(project_root)` reads `package.json` and registers known npm MCP packages whose entry files exist under `node_modules`. Known default mappings are exposed as `DEFAULT_NODE_MCP_REGISTRY`.

## Further reading

- **Architecture and design:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- **Benchmarks and tests:** [`docs/BENCHMARKS_AND_TESTS.md`](docs/BENCHMARKS_AND_TESTS.md)
