# STRIP — Staged Tool Retrieval & Invocation Protocol

## What It Is

A universal Python middleware that wraps any MCP server and eliminates the token overhead of full schema disclosure. Instead of sending complete tool definitions to every LLM call, STRIP delivers tool information in stages — names and descriptions first, full schemas only on demand.

No ML. No embeddings. No vector database. Pure Python.

---

## The Problem It Solves

MCP servers expose full tool schemas upfront, every time, regardless of how many tools the agent will actually use. A typical setup:

```
Playwright MCP:  20 tools × ~800 tokens = 16,000 tokens (every query)
Git MCP:         15 tools × ~600 tokens =  9,000 tokens (every query)
Custom MCPs:     30 tools × ~700 tokens = 21,000 tokens (every query)
─────────────────────────────────────────────────────
Total wasted:   46,000 tokens — before the agent does anything
```

Agents typically use 2–5 tools per turn. The rest is noise the LLM pays for.

---

## How It Works

Three stages per agent turn:

**Stage 1 — Discovery**
STRIP strips each MCP tool to its name and description only (using the exact description the MCP server provides — no translation). Agent receives a lightweight list.

```
browser_navigate: Navigate the browser to a URL
browser_click: Click an element on the page
browser_screenshot: Take a screenshot of the current page
git_status: Show the working tree status
git_commit: Create a new commit with staged changes
... (all tools, names + descriptions only)
```

Token cost: ~15–30 tokens per tool instead of ~800.

**Stage 2 — Schema Fetch**
LLM reads Stage 1, decides which tools it needs, sends back names. STRIP returns full inputSchema for only those tools.

```python
# LLM picked these two
full_schemas = mcp.get_schemas(["browser_navigate", "git_commit"])
# → full inputSchema + required fields for 2 tools only
```

**Stage 3 — Execution**
LLM calls the tool with correct parameters. STRIP routes to the right MCP server subprocess and returns the result.

---

## Token Math (Real Example)

Scenario: Agent needs to run a browser test and commit the result.
Tools used: `browser_navigate`, `browser_click`, `git_add`, `git_commit` (4 tools)

| Approach | Tokens |
|---|---|
| Raw MCP (65 tools, full schemas) | ~46,000 |
| STRIP Stage 1 (65 tools, name+desc) | ~1,300 |
| STRIP Stage 2 (4 tools, full schemas) | ~400 |
| **STRIP total** | **~1,700** |
| **Savings** | **~96%** |

Savings scale with tool count. Under 20 tools: still 70–80%. Over 50 tools: 90%+.

---

## Architecture

```
LLM (Anthropic / OpenAI / local / any)
        ↑↓
  Agent Code (developer's pipeline)
        ↑↓
  [ STRIP middleware ]   ←  pip install strip-mcp
        ↑↓  (stdio subprocess)
  MCP Server processes
  (playwright, git, custom, etc.)
```

STRIP:
- Spawns and manages MCP server subprocesses on startup
- Does the MCP `initialize` + `tools/list` handshake internally
- Caches the full schema map in memory (never sent to LLM until requested)
- Exposes the 3-stage API to agent code
- Routes `execute` calls to the correct MCP server by tool name

No network server required for single-agent use. Optional HTTP mode for multi-agent / multi-process setups.

---

## Developer API

```python
from strip_mcp import StripMCP

# One-time setup
mcp = StripMCP()
mcp.add_server("playwright", command=["npx", "@playwright/mcp"])
mcp.add_server("git",        command=["uvx", "mcp-git"])
mcp.add_server("custom",     url="http://localhost:3000")  # HTTP MCP

# Stage 1 — give this to the LLM as the tool list
tools_brief = mcp.list_tools()
# → [{"name": "browser_navigate", "description": "Navigate browser to URL"}, ...]

# Stage 2 — after LLM picks tools, fetch full schemas
schemas = mcp.get_schemas(["browser_navigate", "git_commit"])
# → [{"name": "browser_navigate", "inputSchema": {...}}, ...]

# Stage 3 — execute
result = mcp.call("browser_navigate", {"url": "https://example.com"})
```

Framework adapters (thin wrappers over this core):
- **Anthropic SDK**: builds `tools=[]` array for the API call
- **OpenAI SDK**: same, compatible format
- **LangChain**: custom tool provider
- **Raw**: dict list, developer formats as needed

---

## What STRIP Does NOT Do

- Does not modify or translate MCP descriptions (uses exact text from MCP server)
- Does not do semantic retrieval or embeddings
- Does not require any ML infrastructure
- Does not require changes to existing MCP servers
- Does not work with host-integrated MCP (Claude Desktop, Cursor) — those hosts control tool loading directly. STRIP targets programmatic agents.

---

## Release Plan

**Phase 1 — Core (ship first)**
- `StripMCP` class: subprocess management, schema cache, 3-stage API
- stdio MCP support (covers 95% of existing servers)
- Raw dict output (LLM-agnostic)

**Phase 2 — Adapters**
- Anthropic SDK adapter
- OpenAI SDK adapter
- LangChain tool provider

**Phase 3 — HTTP mode**
- Standalone HTTP server for multi-agent use
- Session management (per-agent tool selection state)

**Phase 4 — Polish**
- HTTP MCP server support
- Schema cache invalidation (detect server restarts/upgrades)
- Benchmarks + token savings reporting built in

---

## Positioning

> *"MCP gives you everything. STRIP gives the LLM only what it needs."*

Zero infrastructure. Drop-in wrapper. Universal MCP compatibility. Works until the MCP spec fixes staged discovery natively (SEP-1576 — tracking, not yet merged).

---

## Open Questions (deferred)

1. Package name: `strip-mcp`? `mcp-strip`? `strip`?
2. Should Stage 1 support optional keyword filtering (pass `filter="browser"` to narrow even the name list)?
3. Async-first or sync-first API? (Python asyncio vs blocking)
4. How to handle tool name collisions across multiple MCP servers?

---

## Validation & external context (2026-04)

**MCP roadmap (official, last updated 2026-03-05):** Top priorities are transport scalability (Streamable HTTP, sessions), Agent Communication (Tasks / SEP-1686 lifecycle), governance, and enterprise readiness (audit, auth, gateways, config portability). Staged tool listing / token bloat is **not** in those four priority areas — see [modelcontextprotocol.io/development/roadmap](https://modelcontextprotocol.io/development/roadmap).

**SEP-1576** (*Mitigating Token Bloat in MCP…*): Open proposal on the standards track; covers schema deduplication (`$ref`), optional verbosity, and (among other ideas) embedding-based tool retrieval. STRIP is a **client-side, no-ML subset** (name + description → full schema) that does not require server changes. Track: [github.com/modelcontextprotocol/modelcontextprotocol/issues/1576](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1576).

**Build/ship recommendation:** Proceed with **Phase 1–2** for **programmatic** agents that attach many MCP servers (custom runners, SDK pipelines). Pain is real and aligned with SEP-1576’s problem statement; the public roadmap suggests a **multi-quarter** window before core MCP might subsume this pattern. STRIP does **not** address host-integrated clients (Cursor, Claude Desktop) — keep that limitation visible in docs.

**Positioning tweak:** Prefer “until staged discovery is **native in spec + ubiquitous in SDKs**” over vague “until MCP figures out evolution.” Evolution is already directed (WGs, SEPs); obsolescence risk is **spec/SDK catch-up**, not lack of direction.

**Risks to monitor**

- A merged SEP (e.g. 1576 or a successor) may standardize **different** shapes (server-driven verbosity, dedup, optional embedding flows) — migration path should stay a thin adapter layer.
- Roadmap **enterprise gateway / proxy** work could eventually normalize intermediaries that reshape tool payloads — compete on Python ergonomics and zero-infra positioning.
- Major hosts could ship opinionated lazy-tool patterns that reduce need for a third-party wrapper.

**Suggested near-term execution:** Ship minimal **Phase 1** (stdio, schema cache, 3-stage API, **collision strategy**, one real benchmark) plus **one** adapter you actually use; defer HTTP mode and extra adapters until the core API is proven.

---

## Improvements (incorporated from review)

1. **Tool name collisions — Phase 1, not optional late work.** Pick and document before v0.1: e.g. namespaced names (`server__tool`), or `call(server_id, tool, args)`, or an explicit registry map returned alongside Stage 1.
2. **Async-first API.** Prefer asyncio as primary with sync wrappers (or the inverse), to avoid a breaking second release for serious agent runners.
3. **MCP surface beyond tools.** v1 can be tools-only, but state loudly in README: **resources** and **prompts** are out of scope unless added to a later phase — avoids false expectations of “full MCP client.”
4. **Live tool list changes.** Define behavior when servers refresh tools: cache TTL, manual `tools/list` refresh, and/or notification handling if implemented.
5. **SEP-1576 / community.** Optional: comment on or link from SEP-1576 with a short “client-side staged listing in Python” note to reduce duplicate effort and gather feedback.
6. **Package name.** Favor `strip-mcp` over bare `strip` (clarity + PyPI collision avoidance); verify availability before final branding.
7. **Stage 1 keyword filtering (Open Question #2).** Default **off** (YAGNI). If added later, prefer **deterministic** filters (prefix, server allowlist) over fuzzy search.
8. **Trust / security.** STRIP centralizes subprocess spawn and routing; document a minimal **threat model** (what runs as which user, no description/schema mutation, subprocess boundaries) for security- and enterprise-minded adopters.
