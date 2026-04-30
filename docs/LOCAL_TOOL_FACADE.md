# Local Tool Facade

`toolgate` can now act as a local MCP collector/router with lower-context facades
for local agents and custom implementations.

## Inventory

Collect configured MCP tools into SQLite:

```bash
toolgate collect --config ~/.toolgate/config.json
```

Default catalog path: `~/.toolgate/catalog.db`.

The catalog stores discovered server/tool metadata and schemas. It does not store
secrets or active session state.

## Profiles

Profiles are JSON files under `~/.toolgate/profiles/` by default:

```json
{
  "id": "browser",
  "description": "Browser tools only",
  "include_servers": ["playwright"],
  "include_tools": [],
  "exclude_tools": [],
  "description_overrides": {}
}
```

Tool selectors use namespaced tool ids such as `playwright__browser_navigate`.
`include_tools` and `exclude_tools` support `*` globs. Excludes win.

## CLI Facade

Catalog-only commands:

```bash
toolgate profiles
toolgate tools list --profile browser --format text
toolgate tools search navigate --profile browser
toolgate schema playwright__browser_navigate --profile browser
```

Tool calls go through a warm local daemon:

```bash
toolgate daemon run --config ~/.toolgate/config.json --profile browser
toolgate call playwright__browser_navigate --json '{"url":"https://example.com"}'
```

The daemon binds to `127.0.0.1:8765` by default and speaks compact JSON lines to
the CLI.

## Profiled MCP Proxy

The existing MCP proxy can expose a curated profile:

```bash
toolgate proxy --config ~/.toolgate/config.json --profile browser
```

The proxy still returns staged MCP tools: brief/stub schemas first,
`__toolgate__get_schema` for full schemas, then normal `tools/call` routing. Tools
outside the active profile are rejected.

## Minimal REST Direction

The planned custom HTTP facade is intentionally not MCP-over-HTTP. It should be
a minimal local REST API over the same catalog/runtime:

- `GET /v1/health`
- `GET /v1/profiles`
- `GET /v1/tools?profile=NAME`
- `GET /v1/tools/{tool_id}/schema`
- `POST /v1/tools/{tool_id}/call`

MCP Streamable HTTP remains a separate compatibility emitter because it has its
own protocol requirements.
