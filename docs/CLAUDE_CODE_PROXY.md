# Claude Code Proxy (toolgate)

This document explains **what the Claude Code proxy delivers**, **how it delivers it (on-the-wire)**, and **how the full pipeline works end-to-end**.

It describes the implementation currently in:

- `src/toolgate/proxy/server.py`
- `src/toolgate/proxy/config.py`
- CLI wiring in `src/toolgate/cli.py`
- Tests in `tests/test_proxy_server.py` and `test_proxy_live.py`

---

## What the proxy is

The proxy is an **MCP server-side stdio proxy** that sits between:

- **MCP client**: Claude Code (or any MCP-capable client)
- **Upstream MCP servers**: Playwright MCP, filesystem MCP, etc.

The proxy itself speaks **JSON-RPC 2.0 over newline-delimited stdio** to the client, and uses `ToolGate` internally to manage multiple upstream servers and route tool calls.

The purpose is to implement **staged tool delivery** so the client doesn’t ingest large JSON Schemas for every tool up front.

---

## What it delivers (the staged contract)

The proxy implements a **2-stage tool schema delivery** pattern (plus execution):

### Stage 1: `tools/list` returns *brief tools*

On `tools/list`, the proxy returns:

- Every upstream tool as a **brief entry**:
  - `name`
  - `description` (may include a hint that Stage 2 is needed)
  - `inputSchema`: a **stub** schema with:
    - `properties: {}`
    - `additionalProperties: true`
- One additional “meta-tool” named **`__toolgate__get_schema`** with a real input schema.

This is intentionally minimal. The stub `inputSchema` prevents Claude Code from receiving huge tool schemas in Stage 1.

### Stage 2: `__toolgate__get_schema` returns the *real schema* on demand

When the client needs the real parameters for a tool, it calls:

- `tools/call` with `name="__toolgate__get_schema"`
- Arguments: `{ "tool_name": "<exact namespaced tool name>" }`

The proxy responds with an MCP tool result whose `content[0].text` is **pretty-printed JSON** for the upstream tool’s `inputSchema`.

Notes:

- If `tool_name` is unknown, the proxy returns **a valid MCP tool result** with `isError: true` and error text in content.
- If `tool_name` is missing, the proxy returns a **JSON-RPC error** (invalid params).

### Stage 3: `tools/call` executes upstream tools

For any other `tools/call` name:

- The proxy routes the call through `ToolGate.call(tool_name, arguments)` to the correct upstream server.
- It returns an MCP tool result:
  - `{ result: { content: [...], isError: <bool> } }`

Error mapping is deliberate:

- **Tool exists but failed during execution** (upstream `isError: true`):
  - returned as **MCP result** with `isError: true` (not a JSON-RPC protocol error)
- **Tool name not found at all**:
  - returned as **JSON-RPC error** (so clients treat it as a call failure)

---

## How it delivers it (transport + message shapes)

### Transport: newline-delimited JSON-RPC over stdio

The proxy reads **one JSON object per line** from stdin:

- It uses `asyncio.StreamReader.readline()` and `json.loads(line)`.
- Malformed JSON is ignored (warn logged).

Responses are written as a **single line**:

- `sys.stdout.write(json.dumps(msg) + "\n")`
- Flushed immediately
- Protected with an async write lock to prevent interleaving when handling concurrent requests.

### Implemented JSON-RPC methods

The proxy handles these JSON-RPC methods:

- `initialize` → returns capabilities and kicks off upstream startup in the background
- `notifications/initialized` → ignored (no response)
- `tools/list` → Stage 1 brief list + meta-tool
- `tools/call` → Stage 2 meta-tool OR Stage 3 execution

Any other method:

- If request has an `id`: returns JSON-RPC error `-32601 Method not found`
- If no `id`: treated as notification and ignored

---

## End-to-end pipeline (Claude Code → proxy → upstreams)

### 0) Install wiring: how Claude Code gets pointed at the proxy

The CLI includes an “install into Claude Code” helper:

- `toolgate install [--proxy-config PATH] [--claude-config PATH] [--dry-run]`

What it does:

1. Reads Claude Code settings JSON (default `~/.claude/settings.json`, overridable via `TOOLGATE_CLAUDE_CODE_CONFIG`).
2. Reads existing `mcpServers` entries.
3. Writes a `ProxyConfig` JSON (default `~/.toolgate/config.json`) that stores:
   - `servers`: upstream server IDs and their `command` arrays
   - `original_mcp_servers`: the original Claude Code `mcpServers` dict (for clean uninstall)
4. Rewrites Claude Code’s `mcpServers` to a **single entry**:

```json
{
  "mcpServers": {
    "toolgate": {
      "command": "<absolute path to toolgate>",
      "args": ["proxy", "--config", "<path to proxy config>"]
    }
  }
}
```

On uninstall (`toolgate uninstall`), it restores `mcpServers` from `original_mcp_servers`.

### 1) Startup: `initialize` returns immediately, upstreams start in background

When Claude Code starts the MCP server, it sends:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}
```

The proxy:

- immediately responds with `protocolVersion`, `capabilities.tools`, and `serverInfo`
- starts an async background task to connect to upstream servers (`ToolGate.start()`)

This is important because some upstreams may be slow to spawn; the proxy must not block initialization.

### 2) Stage 1: Claude Code requests tools list (brief)

Claude Code sends:

```json
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
```

The proxy tries to wait for upstream startup, but only up to a timeout (15s). If upstreams are slow, it may return a **partial tool list** rather than blocking indefinitely.

The response shape:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "tools": [
      {
        "name": "playwright__browser_navigate",
        "description": "… (call __toolgate__get_schema to get parameters before use)",
        "inputSchema": { "type":"object", "properties":{}, "additionalProperties":true }
      },
      // ... many tools ...
      {
        "name": "__toolgate__get_schema",
        "description": "Returns the full parameter schema for any upstream tool. …",
        "inputSchema": {
          "type":"object",
          "properties": { "tool_name": { "type":"string", "description":"Exact namespaced tool name, e.g. 'playwright__browser_navigate'" } },
          "required": ["tool_name"]
        }
      }
    ]
  }
}
```

### 3) Stage 2: Claude Code fetches schema for a specific tool (on demand)

When the model wants to call a tool with arguments, it first fetches that tool’s schema:

Request:

```json
{
  "jsonrpc":"2.0",
  "id":3,
  "method":"tools/call",
  "params":{
    "name":"__toolgate__get_schema",
    "arguments":{"tool_name":"playwright__browser_navigate"}
  }
}
```

Response (the schema is returned as text content):

```json
{
  "jsonrpc":"2.0",
  "id":3,
  "result":{
    "content":[{"type":"text","text":"{\\n  \\"type\\": \\"object\\",\\n  \\"properties\\": { ... }\\n}\\n"}],
    "isError": false
  }
}
```

### 4) Stage 3: Claude Code calls the upstream tool

Request:

```json
{
  "jsonrpc":"2.0",
  "id":4,
  "method":"tools/call",
  "params":{
    "name":"playwright__browser_navigate",
    "arguments":{"url":"https://example.com"}
  }
}
```

Response:

```json
{
  "jsonrpc":"2.0",
  "id":4,
  "result":{
    "content":[{"type":"text","text":"..."}],
    "isError": false
  }
}
```

If the upstream tool runs but returns an MCP `isError` outcome, the proxy preserves it as:

```json
{ "jsonrpc":"2.0", "id":4, "result": { "content":[...], "isError": true } }
```

If the tool name is unknown, the proxy returns a JSON-RPC error:

```json
{ "jsonrpc":"2.0", "id":4, "error": { "code": -32602, "message": "..." } }
```

---

## How upstream routing works inside the proxy

Internally the proxy uses `ToolGate`:

- For each upstream in `ProxyConfig.servers`, it calls:
  - `ToolGate.add_server(server_id, command=[...], staged=True, namespace=True)`

Key implications:

- **Namespacing is always on**: upstream tools are presented as `{server_id}__{tool}`.
- **Stage 1 is always brief** at the upstream `ToolGate` level too (by passing `staged=True`).
- The proxy itself then *forces* the client-facing schema to be stubbed (Stage 1), regardless of upstream behavior.

During execution:

- `ToolGate` resolves the namespaced tool to a specific upstream server via `ToolRegistry`.
- The selected upstream `ServerHandle` performs the real JSON-RPC `tools/call` against that server’s subprocess.

---

## Concurrency and shutdown behavior

- Each incoming JSON-RPC request line is handled in its own asyncio task.
- Output is serialized using a single async write lock so replies are not interleaved.
- On SIGINT/SIGTERM or BrokenPipeError, the proxy stops reading and exits.
- On stdin EOF (client closes), the proxy drains in-flight tasks and shuts down.

---

## How to see it live (local reproduction)

There’s a live script that exercises this flow against Playwright MCP:

- `test_proxy_live.py`
- Uses `test-proxy-config.json` for upstream configuration.

It prints:

- The `initialize` response
- A summary of `tools/list` showing stub schemas
- A `__toolgate__get_schema` call showing a real schema
- A token/size comparison between:
  - proxy brief `tools/list`
  - direct upstream `tools/list`

---

## Current limitations / sharp edges

- **Stage 2 schema is returned as text**, not as a structured JSON object. This is intentional for MCP compatibility, but clients must parse JSON from `content[0].text` if they want to programmatically use it.
- The proxy config supports per-server `env` and `cwd`, and both are passed through to upstream subprocess creation.
- Upstream startup failures are logged; `tools/list` may return partial lists if startup is slow or failing.
