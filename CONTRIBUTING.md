# Contributing

## Tooling

- **Required for core development:** Python 3.11+, `pip install -e ".[dev]"`, and `pytest` for tests.
- **Optional:** Node.js and `npm install` in the repo root only if you run `examples/` that depend on npm MCP packages (`package.json` / `node_modules`) or want to exercise `discover_node_mcp_servers` against real installs.

CI and local Python workflows should not assume Node is present unless the task explicitly targets Node MCP examples.
