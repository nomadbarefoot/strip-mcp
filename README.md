# strip-mcp

Python middleware for the [Model Context Protocol](https://modelcontextprotocol.io): staged tool discovery (names and descriptions first, full JSON schemas on demand) to cut token overhead from large MCP tool registries.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Optional Node MCPs for examples (Playwright, Wikipedia, memory):

```bash
npm install
npx playwright install chromium
```

See `examples/` for usage and benchmarks. Design notes: `STRIP_plan.md`, `STRIP_V2_plan.md`.
