# STRIP v2 — Buildable Spec

## Package: `strip-mcp`

A Python middleware that wraps MCP servers and delivers tool information in stages. Names and descriptions first, full schemas only on demand, direct execution for zero-param tools.

No ML. No embeddings. No vector database. Pure Python, async-first.

---

## Core Concepts

### The Three Stages

Every agent turn flows through up to three stages. Stages can be skipped when unnecessary.

**Stage 1 — Discovery**
Return every tool as `{name, description, server, requires_params}` only. The LLM sees a lightweight list. Token cost: ~15–30 per tool instead of ~800.

**Stage 2 — Schema Fetch**
LLM picks tool names from Stage 1. STRIP returns full `inputSchema` for only those tools. **Skippable**: if the tool is well-known (e.g. `browser_navigate`) the LLM may already know the params. The developer decides whether to call Stage 2.

**Stage 3 — Execution**
LLM calls the tool. STRIP routes to the correct MCP server subprocess and returns the result.

### Direct Execution (new in v2)

Tools with **no required parameters** (empty `inputSchema` or `required: []`) are marked `requires_params: false` in Stage 1. These can skip Stage 2 entirely — the agent can execute them directly from the discovery list.

Examples: `browser_screenshot`, `git_status`, `get_clipboard_contents`

```
Stage 1 output:
  playwright__browser_screenshot: Take a screenshot (no params, can execute directly)
  playwright__browser_navigate: Navigate to a URL (params required)
```

### Per-Server Staging Control

Not every MCP server needs staging. Small servers (under 10 tools) may not be worth the extra LLM call. Developers control this per-server:

```python
mcp.add_server("git", command=["uvx", "mcp-git"], staged=False)
# → git tools sent with full schemas always, no staging overhead

mcp.add_server("playwright", command=["npx", "@playwright/mcp"], staged=True)
# → default, uses 3-stage flow
```

`staged=True` is the default. `staged=False` passes through full schemas as-is.

---

## Architecture

```
Agent Code (developer's pipeline)
       ↑↓
 [ StripMCP ]              ← pip install strip-mcp
       |
       ├── ServerHandle("playwright", stdio, staged=True)
       │     ├── subprocess: npx @playwright/mcp
       │     ├── schema_cache: {tool_name: full_schema, ...}
       │     └── tool_registry: {tool_name: {name, desc, requires_params}, ...}
       │
       ├── ServerHandle("git", stdio, staged=False)
       │     ├── subprocess: uvx mcp-git
       │     └── passthrough: full schemas always returned
       │
       └── ServerHandle("custom", http, staged=True)
             └── HTTP client to http://localhost:3000
```

### Internal Components

**`StripMCP`** — top-level orchestrator
- Owns all `ServerHandle` instances
- Maintains global tool name → server mapping (with namespace prefix)
- Exposes the 3-stage public API
- Handles `refresh()` for schema reloading

**`ServerHandle`** — per-server manager
- Spawns and owns one MCP server (subprocess or HTTP client)
- Runs `initialize` + `tools/list` handshake on startup
- Caches full schemas in memory
- Produces stripped tool list (Stage 1 output)
- Routes `call_tool` to the underlying MCP connection

**`MCPConnection`** — transport abstraction
- `StdioConnection`: manages subprocess stdin/stdout, JSON-RPC framing
- `HTTPConnection`: manages HTTP/SSE client (Phase 2)
- Both implement: `initialize()`, `list_tools()`, `call_tool(name, args)`

---

## Tool Name Collisions

**Strategy: namespace prefix, always.**

All tool names are prefixed with `{server_id}__` (double underscore):
- `playwright__browser_navigate`
- `git__status`
- `custom__my_tool`

This is non-negotiable for Phase 1. Reasons:
- LLM sees namespace in the tool name itself — no ambiguity
- No developer bookkeeping of server IDs at call time
- Matches patterns in LangChain and other tool registries

The original MCP tool name is preserved internally for routing — STRIP translates `playwright__browser_navigate` → calls `browser_navigate` on the playwright server.

Optional: `namespace=False` on `add_server()` for single-server setups where prefixing is noise.

```python
mcp.add_server("playwright", command=[...])  # tools: playwright__browser_navigate, etc.
mcp.add_server("git", command=[...], namespace=False)  # tools: status, commit, etc. (raw names)
```

If `namespace=False` and a collision occurs, raise `ToolCollisionError` at startup.

---

## Public API

### Async (primary)

```python
from strip_mcp import StripMCP

async def main():
    mcp = StripMCP()

    # Register servers
    mcp.add_server("playwright", command=["npx", "@playwright/mcp"])
    mcp.add_server("git", command=["uvx", "mcp-git"], staged=False)
    mcp.add_server("custom", url="http://localhost:3000")

    # Start all servers, run handshakes, cache schemas
    await mcp.start()

    # ── Stage 1: Discovery ──
    tools = await mcp.list_tools()
    # Returns:
    # [
    #   {"name": "playwright__browser_navigate", "description": "Navigate to URL", "requires_params": True},
    #   {"name": "playwright__browser_screenshot", "description": "Take screenshot", "requires_params": False},
    #   {"name": "git__status", "description": "Show working tree status", "inputSchema": {...}},  # staged=False, full schema
    #   ...
    # ]

    # ── Stage 2: Schema Fetch (only for staged servers) ──
    schemas = await mcp.get_schemas(["playwright__browser_navigate"])
    # Returns:
    # [
    #   {"name": "playwright__browser_navigate", "inputSchema": {"type": "object", "properties": {"url": ...}, "required": ["url"]}}
    # ]

    # ── Stage 3: Execute ──
    result = await mcp.call("playwright__browser_navigate", {"url": "https://example.com"})

    # Direct execution (no params tool)
    screenshot = await mcp.call("playwright__browser_screenshot")

    # Refresh schemas (after server restart, etc.)
    await mcp.refresh()            # all servers
    await mcp.refresh("playwright") # one server

    # Shutdown
    await mcp.stop()
```

### Sync wrapper

```python
from strip_mcp import SyncStripMCP

mcp = SyncStripMCP()
mcp.add_server("playwright", command=["npx", "@playwright/mcp"])
mcp.start()

tools = mcp.list_tools()
schemas = mcp.get_schemas(["playwright__browser_navigate"])
result = mcp.call("playwright__browser_navigate", {"url": "https://example.com"})

mcp.stop()
```

`SyncStripMCP` is a thin wrapper that runs an internal event loop. Same API surface, blocking calls.

### Context manager support

```python
async with StripMCP() as mcp:
    mcp.add_server("playwright", command=["npx", "@playwright/mcp"])
    await mcp.start()
    # ... use mcp ...
# auto-stop on exit
```

### Convenience: text output for system prompts

```python
text = await mcp.list_tools_text()
# Returns formatted string:
# playwright__browser_navigate: Navigate to URL [params required]
# playwright__browser_screenshot: Take screenshot [no params]
# git__status: Show working tree status [full schema attached]
# ...

# Inject into system prompt
system_prompt = f"Available tools:\n{text}\n\nPick the tools you need, then request their schemas."
```

### Turn-level caching

`list_tools()` caches its output after the first call. Subsequent calls return the same object unless `refresh()` was called. No extra processing per turn.

```python
tools_1 = await mcp.list_tools()  # builds and caches
tools_2 = await mcp.list_tools()  # returns cached, zero cost
await mcp.refresh()
tools_3 = await mcp.list_tools()  # rebuilds after refresh
```

---

## Error Handling

### Defined error types

```python
from strip_mcp.errors import (
    StripError,              # base
    ServerStartError,        # subprocess failed to start or handshake failed
    ServerCrashedError,      # subprocess died mid-session
    ToolNotFoundError,       # LLM requested a tool name that doesn't exist (hallucination)
    ToolCollisionError,      # two servers registered same name (namespace=False collision)
    ToolExecutionError,      # tool call returned an error from the MCP server
    ToolTimeoutError,        # tool call exceeded timeout
    SchemaFetchError,        # get_schemas called for non-existent tool
)
```

### Behaviors

| Scenario | Behavior |
|---|---|
| MCP subprocess crashes | `ServerCrashedError` raised on next `call()`. Server marked unhealthy. `refresh()` attempts restart. |
| LLM requests non-existent tool name | `ToolNotFoundError` with suggestion of closest match (Levenshtein on tool names). |
| Tool execution times out | `ToolTimeoutError` after configurable timeout (default 30s). |
| Collision with `namespace=False` | `ToolCollisionError` raised at `start()` time, before any LLM interaction. |
| Server returns MCP error on `call_tool` | `ToolExecutionError` wrapping the original MCP error content. |

### Timeout configuration

```python
mcp = StripMCP(default_timeout=30)  # seconds, applies to all tool calls
mcp.add_server("playwright", command=[...], timeout=60)  # override per server
```

---

## Description Overrides

MCP servers have inconsistent description quality. STRIP uses exact MCP descriptions by default but allows overrides:

```python
mcp.add_server("playwright",
    command=["npx", "@playwright/mcp"],
    description_overrides={
        "browser_navigate": "Navigate to a URL",
        "browser_click": "Click a page element by CSS selector or text",
    }
)
```

Override applies to Stage 1 output only. Full schemas are never modified.

---

## File Structure

```
strip-mcp/
├── pyproject.toml
├── README.md
├── LICENSE                      # MIT
├── src/
│   └── strip_mcp/
│       ├── __init__.py          # exports StripMCP, SyncStripMCP
│       ├── core.py              # StripMCP class
│       ├── sync.py              # SyncStripMCP wrapper
│       ├── server.py            # ServerHandle class
│       ├── connection/
│       │   ├── __init__.py
│       │   ├── base.py          # MCPConnection ABC
│       │   ├── stdio.py         # StdioConnection
│       │   └── http.py          # HTTPConnection (Phase 2, stub in Phase 1)
│       ├── registry.py          # global tool name → server mapping, collision detection
│       ├── errors.py            # all error types
│       ├── types.py             # TypedDicts / dataclasses for ToolBrief, ToolSchema, etc.
│       └── adapters/
│           ├── __init__.py
│           └── anthropic.py     # Anthropic SDK adapter (Phase 2)
├── tests/
│   ├── conftest.py
│   ├── test_orchestrator.py
│   ├── test_transport.py
│   ├── test_setup_discovery.py
│   └── mock_mcp_server.py       # minimal MCP server for testing (stdio, responds to initialize + tools/list + call_tool)
└── examples/
    ├── basic_usage.py
    ├── multi_server.py
    ├── anthropic_agent.py        # Phase 2
    └── benchmark_tokens.py       # token counting before/after
```

---

## Data Types

```python
# types.py
from dataclasses import dataclass
from typing import Any

@dataclass
class ToolBrief:
    """Stage 1 output for a single tool."""
    name: str                    # namespaced: "playwright__browser_navigate"
    description: str             # from MCP or override
    server_id: str               # "playwright"
    requires_params: bool        # True if inputSchema has required fields or non-empty properties
    full_schema: dict | None     # only populated if server is staged=False

@dataclass
class ToolSchema:
    """Stage 2 output for a single tool."""
    name: str                    # namespaced
    input_schema: dict           # full inputSchema from MCP

@dataclass
class ToolResult:
    """Stage 3 output."""
    name: str
    content: list[dict[str, Any]]  # MCP content array (text, image, etc.)
    is_error: bool
```

---

## MCP JSON-RPC Protocol (for StdioConnection)

STRIP implements a minimal MCP client. The protocol is JSON-RPC 2.0 over stdio.

### Handshake sequence

```
CLIENT → SERVER:
{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
  "protocolVersion": "2024-11-05",
  "capabilities": {},
  "clientInfo": {"name": "strip-mcp", "version": "0.1.0"}
}}

SERVER → CLIENT:
{"jsonrpc": "2.0", "id": 1, "result": {
  "protocolVersion": "2024-11-05",
  "capabilities": {"tools": {}},
  "serverInfo": {"name": "...", "version": "..."}
}}

CLIENT → SERVER:
{"jsonrpc": "2.0", "method": "notifications/initialized"}
```

### tools/list

```
CLIENT → SERVER:
{"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}

SERVER → CLIENT:
{"jsonrpc": "2.0", "id": 2, "result": {
  "tools": [
    {"name": "browser_navigate", "description": "...", "inputSchema": {...}},
    ...
  ]
}}
```

### tools/call

```
CLIENT → SERVER:
{"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
  "name": "browser_navigate",
  "arguments": {"url": "https://example.com"}
}}

SERVER → CLIENT:
{"jsonrpc": "2.0", "id": 3, "result": {
  "content": [{"type": "text", "text": "Navigated to https://example.com"}],
  "isError": false
}}
```

### Framing

stdio MCP uses newline-delimited JSON. Each message is one line. Read stdout line by line, parse JSON, match by `id` for responses.

Server may also send notifications (no `id` field) — log and ignore for Phase 1.

---

## Phase 1 Scope (ship this)

### Included
- `StripMCP` + `SyncStripMCP` with full 3-stage API
- `StdioConnection` (subprocess management)
- Schema caching + `refresh()`
- Namespace collision handling (`server__tool`)
- `staged=True/False` per server
- `requires_params` detection + direct execution
- `description_overrides`
- `list_tools_text()` for system prompt injection
- All error types defined and raised
- Configurable timeouts
- Context manager support
- `mock_mcp_server.py` for tests
- One benchmark example (`benchmark_tokens.py`)
- README with honest "latency vs cost" section
- README states: resources and prompts are **out of scope** (tools only)

### Excluded (later phases)
- HTTP/SSE MCP transport (Phase 2 — stub exists)
- Anthropic / OpenAI SDK adapters (Phase 2)
- LangChain tool provider (Phase 3)
- HTTP server mode for multi-agent (Phase 3)
- Token savings reporting CLI (Phase 3)

---

## Phase 2 Scope

- `HTTPConnection` for HTTP/SSE MCP servers
- Anthropic SDK adapter: produces `tools=[]` for API calls, handles two-phase message flow
- OpenAI SDK adapter

---

## Phase 3 Scope

- LangChain / CrewAI tool provider (lazy-loading `BaseTool` objects)
- Standalone HTTP server mode for multi-agent setups
- Token counting / savings reporter
- Schema diff detection on `refresh()` (log what changed)

---

## Dependencies (Phase 1)

Runtime: none beyond stdlib (`asyncio`, `subprocess`, `json`, `dataclasses`).

Dev/test: `pytest`, `pytest-asyncio`.

Zero external dependencies is a feature. The package should install in under 2 seconds.

---

## README Sections (outline for Phase 1)

1. **What it does** — one paragraph + token math table
2. **Install** — `pip install strip-mcp`
3. **Quick start** — 15-line async example
4. **How staging works** — Stage 1/2/3 explanation with direct execution
5. **Per-server control** — `staged`, `namespace`, `description_overrides`, `timeout`
6. **Error handling** — table of error types and when they fire
7. **Sync usage** — `SyncStripMCP` example
8. **Limitations** — tools only (no resources/prompts), no host-integrated clients, latency tradeoff is real for low-tool-count setups
9. **Relation to SEP-1576** — client-side subset, no server changes, works until spec catches up

---

## Testing Strategy

### mock_mcp_server.py

A minimal Python script that acts as an MCP server over stdio. Configurable:
- Number of tools (default 20)
- Schema complexity (simple / nested / large)
- Latency simulation (optional sleep before response)
- Error simulation (fail on specific tool calls)

```bash
python mock_mcp_server.py --tools 50 --latency 0.1
```

### Test cases (Phase 1)

**Core flow:**
- Start server, list tools, get schemas, call tool, stop server
- Multiple servers, namespaced names, no collisions
- `staged=False` server returns full schemas in list_tools
- `namespace=False` with no collisions works
- `namespace=False` with collisions raises `ToolCollisionError` at start

**Direct execution:**
- Tools with empty inputSchema marked `requires_params=False`
- Tools with `required: []` but non-empty properties marked `requires_params=True`
- Direct `call()` with no args succeeds for no-param tools

**Error paths:**
- Server fails to start → `ServerStartError`
- Server crashes mid-call → `ServerCrashedError`
- Call non-existent tool → `ToolNotFoundError` with suggestion
- Call times out → `ToolTimeoutError`
- Server returns error → `ToolExecutionError`

**Caching:**
- `list_tools()` returns same object on second call
- `refresh()` rebuilds cache
- `refresh("server_id")` refreshes only that server

**Description overrides:**
- Override applied in Stage 1 output
- Full schema unchanged in Stage 2

---

## Open Decisions (for implementer)

1. **Protocol version**: Use `"2024-11-05"` as the MCP protocol version in `initialize`. Update if MCP spec has moved.
2. **Subprocess stdio buffering**: Use `asyncio.create_subprocess_exec` with `stdin=PIPE, stdout=PIPE, stderr=PIPE`. Read stderr in a background task for logging.
3. **JSON-RPC ID tracking**: Simple incrementing integer per connection. Dict mapping `id → Future` for response routing.
4. **Graceful shutdown**: On `stop()`, send no message (MCP has no shutdown handshake). Kill subprocess with SIGTERM, wait 3s, SIGKILL.
5. **`requires_params` heuristic**: A tool requires params if `inputSchema.properties` is non-empty. Even if `required` is empty — having properties means the LLM might need to know the schema. Only truly empty schemas (`{}` or `{"type": "object", "properties": {}}`) get `requires_params=False`.