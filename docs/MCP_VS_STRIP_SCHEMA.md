# Native MCP tool shape vs strip-mcp

This document places **standard MCP tool listings** next to **strip-mcp’s** representations so you can see **what is omitted or replaced** in staged mode and **where token savings** come from. For architecture context, see [ARCHITECTURE.md](./ARCHITECTURE.md).

---

## 1. Two ways to use strip-mcp

| Surface | What the model / caller sees | Full schemas in “discovery” step? |
|--------|------------------------------|----------------------------------|
| **Python library** (`StripMCP`) | `ToolBrief` (Stage 1), `ToolSchema` (Stage 2), `ToolResult` (Stage 3) | Only if `staged=False` (each brief can carry `full_schema`) |
| **MCP proxy** (`strip-mcp proxy`) | JSON-RPC `tools/list` with **stub** `inputSchema` per tool + meta-tool `__strip__get_schema` | No — real schemas arrive via `tools/call` on `__strip__get_schema` or are read from in-process cache in the library path |

Upstream servers still speak **native MCP**; strip-mcp **caches** full `inputSchema` after its own `tools/list` and **chooses what to expose** per stage.

---

## 2. One tool: native MCP vs strip-mcp (side by side)

### 2.1 Native MCP (typical `tools/list` entry)

Per the MCP tools model, each tool includes at least **name**, optional **description**, and **`inputSchema`** (JSON Schema for arguments). Large token cost usually lives in **`inputSchema`** (nested `properties`, `enum`, `oneOf`, long descriptions inside the schema, etc.).

```json
{
  "name": "browser_navigate",
  "description": "Navigate to URL",
  "inputSchema": {
    "type": "object",
    "properties": {
      "url": { "type": "string", "format": "uri" }
    },
    "required": ["url"]
  }
}
```

### 2.2 strip-mcp — Python API (Stage 1: `ToolBrief`)

Full schema is **not** a field in Stage 1 when `staged=True` (default). The library keeps a **boolean** derived from the upstream schema instead of serializing the whole object.

| Field | Native MCP | strip-mcp `ToolBrief` |
|-------|------------|------------------------|
| Identity | `name` | `name` — often **namespaced**: `server_id__raw_name` when `namespace=True` |
| Human text | `description` | `description` — same or **override** via `description_overrides` |
| Parameters | Full `inputSchema` object | **`requires_params`** — `True` iff `inputSchema.properties` is non-empty (see `server.py`) |
| Full JSON Schema | In every list entry | **`full_schema`** — `None` when staged; copy of `inputSchema` when `staged=False` |
| Routing | N/A (single server) | **`server_id`** — which upstream server owns the tool |

```text
ToolBrief(
  name="playwright__browser_navigate",
  description="Navigate to URL",
  server_id="playwright",
  requires_params=True,
  full_schema=None,   # staged=True: omitted from Stage 1
)
```

### 2.3 strip-mcp — Python API (Stage 2: `ToolSchema`)

When you need arguments, you request **only named tools**. Each item is the **full** upstream `inputSchema`, unchanged in meaning.

```text
ToolSchema(
  name="playwright__browser_navigate",
  input_schema={ "type": "object", "properties": { ... }, ... },
)
```

### 2.4 strip-mcp — MCP proxy wire (`tools/list` entry for the same logical tool)

The proxy must expose valid MCP tool entries, so each tool still has an **`inputSchema`**, but it is a **small stub** (accept-any object) — not the real upstream schema. Descriptions may **hint** the model to call `__strip__get_schema` first.

```json
{
  "name": "playwright__browser_navigate",
  "description": "Navigate to URL (call __strip__get_schema to get parameters before use)",
  "inputSchema": {
    "type": "object",
    "properties": {},
    "additionalProperties": true
  }
}
```

**Added once per session (fixed overhead):** meta-tool `__strip__get_schema` with its own small `inputSchema` (`tool_name` string). See [ARCHITECTURE.md §3.3](./ARCHITECTURE.md#33-raw-json-rpc-examples-native-mcp-vs-strip-mcp) for full JSON-RPC examples.

---

## 3. Full discovery payload: conceptual comparison

### Native MCP `tools/list` (staged off — “full registry”)

- **One block per tool**: `name` + `description` + **full** `inputSchema`.
- Token cost scales roughly with **(number of tools) × (average schema size)**.

### strip-mcp Stage 1 (`staged=True`)

**Library path**

- **Per tool**: short line-friendly text from `list_tools_text()` — name, description, and a tag: `[params required]`, `[no params]`, or `[full schema attached]` if `staged=False`.
- **No** embedding of full JSON Schema in Stage 1 when staged.

**Proxy path**

- **Per tool**: name + description + **stub** schema (constant small size).
- **Plus** one meta-tool definition for Stage 2 schema retrieval.

### strip-mcp Stage 2 (on demand)

- **Only** schemas for tools the agent asked for — not the entire registry.

---

## 4. How and where we save tokens

| Mechanism | What shrinks | Caveat |
|-----------|----------------|--------|
| **Omit full `inputSchema` from Stage 1** | Largest win: no per-tool JSON Schema blobs in the first prompt/tool-definition pass | Model must do a second step (`get_schemas` / `__strip__get_schema`) before calling tools with rich arguments |
| **Stub `inputSchema` in proxy** | Keeps MCP wire valid with a **constant tiny** schema instead of variable large ones | Same as above |
| **`requires_params` vs full schema** | One bit of intent (`True`/`False`) vs hundreds of tokens of nested JSON | Coarse: does not list parameter names until Stage 2 |
| **Subset schema fetch** | Stage 2 pays only for **k** tools, not **N** | If the model requests schemas for almost all tools, savings narrow |
| **No duplicate cross-server schemas in Stage 1** | Namespacing adds `server_id__` prefix (small overhead) but avoids ambiguity; savings still dominated by schema omission | — |

**Where tokens are “spent” vs “saved”**

- **Saved**: Anything that would have repeated **full JSON Schema** text in the initial tool list or system prompt (Stage 1).
- **Spent elsewhere**: Short descriptions, namespaced names, stub schemas, the `__strip__get_schema` tool (proxy), and any Stage 2 calls that pull full schemas for selected tools.
- **Not an extra network round-trip for schema in the common library case**: the handle already cached upstream `inputSchema` in memory at startup (`ServerHandle._load_tools`); Stage 2 is **exposing** cached data to the model, not re-fetching from the subprocess unless you `refresh()`.

**Measured ratios** (same methodology as [ARCHITECTURE.md §9](./ARCHITECTURE.md#9-benchmark-results)): staged workflow vs naive full-registry prompt showed roughly **6× fewer** tokens in the benchmark scenario; see [BENCHMARKS_AND_TESTS.md](./BENCHMARKS_AND_TESTS.md) for scripts and caveats (token estimate uses `len(utf8_text) // 4`, a rough proxy).

---

## 5. Quick reference: stage mapping

| Stage | Native MCP (conceptual) | strip-mcp library | strip-mcp proxy |
|-------|-------------------------|-------------------|-----------------|
| 1 — List | `tools/list` with full schemas | `list_tools()` → `ToolBrief[]`; `list_tools_text()` | `tools/list` with stub schemas + `__strip__get_schema` |
| 2 — Schema detail | *(already in list)* | `get_schemas(names)` → `ToolSchema[]` | `tools/call` `__strip__get_schema` |
| 3 — Execute | `tools/call` | `call(name, args)` | `tools/call` namespaced name → upstream |

---

## 6. Related reading

- [ARCHITECTURE.md](./ARCHITECTURE.md) — staged pipeline, registry, lifecycle
- [BENCHMARKS_AND_TESTS.md](./BENCHMARKS_AND_TESTS.md) — token methodology and benchmarks

---

## 7. Appendix: full side-by-side capture (`@playwright/mcp`)

**What this is:** A **live** `tools/list` from **`node node_modules/@playwright/mcp/cli.js`** (raw MCP), next to the **strip-mcp proxy–equivalent** payload for the same upstream with `server_id=playwright` (`staged=True`, `namespace=True`). Captured in this repo on **2026-04-07**.

**Sizes (compact JSON, `len(s) // 4` token proxy):**

| Payload | Tools | Characters (compact) | ~Tokens |
|---------|-------|----------------------|---------|
| Raw Playwright `tools/list` `result.tools` | 21 | 15,505 | ~3,876 |
| strip-mcp `result.tools` (21 brief + `__strip__get_schema`) | 22 | 5,291 | ~1,323 |

Raw MCP includes **`annotations`** on every tool and full **`inputSchema`** per tool; strip-mcp replaces each **`inputSchema`** with the same stub and appends the meta-tool.

### 7.1 strip-mcp — full `tools/list` JSON-RPC `result` (Playwright only)

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "tools": [
      {
        "name": "playwright__browser_close",
        "description": "Close the page",
        "inputSchema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        }
      },
      {
        "name": "playwright__browser_resize",
        "description": "Resize the browser window (call __strip__get_schema to get parameters before use)",
        "inputSchema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        }
      },
      {
        "name": "playwright__browser_console_messages",
        "description": "Returns all console messages (call __strip__get_schema to get parameters before use)",
        "inputSchema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        }
      },
      {
        "name": "playwright__browser_handle_dialog",
        "description": "Handle a dialog (call __strip__get_schema to get parameters before use)",
        "inputSchema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        }
      },
      {
        "name": "playwright__browser_evaluate",
        "description": "Evaluate JavaScript expression on page or element (call __strip__get_schema to get parameters before use)",
        "inputSchema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        }
      },
      {
        "name": "playwright__browser_file_upload",
        "description": "Upload one or multiple files (call __strip__get_schema to get parameters before use)",
        "inputSchema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        }
      },
      {
        "name": "playwright__browser_fill_form",
        "description": "Fill multiple form fields (call __strip__get_schema to get parameters before use)",
        "inputSchema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        }
      },
      {
        "name": "playwright__browser_press_key",
        "description": "Press a key on the keyboard (call __strip__get_schema to get parameters before use)",
        "inputSchema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        }
      },
      {
        "name": "playwright__browser_type",
        "description": "Type text into editable element (call __strip__get_schema to get parameters before use)",
        "inputSchema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        }
      },
      {
        "name": "playwright__browser_navigate",
        "description": "Navigate to a URL (call __strip__get_schema to get parameters before use)",
        "inputSchema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        }
      },
      {
        "name": "playwright__browser_navigate_back",
        "description": "Go back to the previous page in the history",
        "inputSchema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        }
      },
      {
        "name": "playwright__browser_network_requests",
        "description": "Returns all network requests since loading the page (call __strip__get_schema to get parameters before use)",
        "inputSchema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        }
      },
      {
        "name": "playwright__browser_run_code",
        "description": "Run Playwright code snippet (call __strip__get_schema to get parameters before use)",
        "inputSchema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        }
      },
      {
        "name": "playwright__browser_take_screenshot",
        "description": "Take a screenshot of the current page. You can't perform actions based on the screenshot, use browser_snapshot for actions. (call __strip__get_schema to get parameters before use)",
        "inputSchema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        }
      },
      {
        "name": "playwright__browser_snapshot",
        "description": "Capture accessibility snapshot of the current page, this is better than screenshot (call __strip__get_schema to get parameters before use)",
        "inputSchema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        }
      },
      {
        "name": "playwright__browser_click",
        "description": "Perform click on a web page (call __strip__get_schema to get parameters before use)",
        "inputSchema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        }
      },
      {
        "name": "playwright__browser_drag",
        "description": "Perform drag and drop between two elements (call __strip__get_schema to get parameters before use)",
        "inputSchema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        }
      },
      {
        "name": "playwright__browser_hover",
        "description": "Hover over element on page (call __strip__get_schema to get parameters before use)",
        "inputSchema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        }
      },
      {
        "name": "playwright__browser_select_option",
        "description": "Select an option in a dropdown (call __strip__get_schema to get parameters before use)",
        "inputSchema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        }
      },
      {
        "name": "playwright__browser_tabs",
        "description": "List, create, close, or select a browser tab. (call __strip__get_schema to get parameters before use)",
        "inputSchema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        }
      },
      {
        "name": "playwright__browser_wait_for",
        "description": "Wait for text to appear or disappear or a specified time to pass (call __strip__get_schema to get parameters before use)",
        "inputSchema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        }
      },
      {
        "name": "__strip__get_schema",
        "description": "Returns the full parameter schema for any upstream tool. Call this before using a tool whose parameters you don't know.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "tool_name": {
              "type": "string",
              "description": "Exact namespaced tool name, e.g. 'playwright__browser_navigate'"
            }
          },
          "required": [
            "tool_name"
          ]
        }
      }
    ]
  }
}

```

### 7.2 Raw MCP — full `tools/list` `result.tools` (Playwright only)

```json
[
  {
    "name": "browser_close",
    "description": "Close the page",
    "inputSchema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "properties": {},
      "additionalProperties": false
    },
    "annotations": {
      "title": "Close browser",
      "readOnlyHint": false,
      "destructiveHint": true,
      "openWorldHint": true
    }
  },
  {
    "name": "browser_resize",
    "description": "Resize the browser window",
    "inputSchema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "properties": {
        "width": {
          "type": "number",
          "description": "Width of the browser window"
        },
        "height": {
          "type": "number",
          "description": "Height of the browser window"
        }
      },
      "required": [
        "width",
        "height"
      ],
      "additionalProperties": false
    },
    "annotations": {
      "title": "Resize browser window",
      "readOnlyHint": false,
      "destructiveHint": true,
      "openWorldHint": true
    }
  },
  {
    "name": "browser_console_messages",
    "description": "Returns all console messages",
    "inputSchema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "properties": {
        "level": {
          "default": "info",
          "description": "Level of the console messages to return. Each level includes the messages of more severe levels. Defaults to \"info\".",
          "type": "string",
          "enum": [
            "error",
            "warning",
            "info",
            "debug"
          ]
        },
        "all": {
          "description": "Return all console messages since the beginning of the session, not just since the last navigation. Defaults to false.",
          "type": "boolean"
        },
        "filename": {
          "description": "Filename to save the console messages to. If not provided, messages are returned as text.",
          "type": "string"
        }
      },
      "required": [
        "level"
      ],
      "additionalProperties": false
    },
    "annotations": {
      "title": "Get console messages",
      "readOnlyHint": true,
      "destructiveHint": false,
      "openWorldHint": true
    }
  },
  {
    "name": "browser_handle_dialog",
    "description": "Handle a dialog",
    "inputSchema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "properties": {
        "accept": {
          "type": "boolean",
          "description": "Whether to accept the dialog."
        },
        "promptText": {
          "description": "The text of the prompt in case of a prompt dialog.",
          "type": "string"
        }
      },
      "required": [
        "accept"
      ],
      "additionalProperties": false
    },
    "annotations": {
      "title": "Handle a dialog",
      "readOnlyHint": false,
      "destructiveHint": true,
      "openWorldHint": true
    }
  },
  {
    "name": "browser_evaluate",
    "description": "Evaluate JavaScript expression on page or element",
    "inputSchema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "properties": {
        "function": {
          "type": "string",
          "description": "() => { /* code */ } or (element) => { /* code */ } when element is provided"
        },
        "element": {
          "description": "Human-readable element description used to obtain permission to interact with the element",
          "type": "string"
        },
        "ref": {
          "description": "Exact target element reference from the page snapshot",
          "type": "string"
        },
        "filename": {
          "description": "Filename to save the result to. If not provided, result is returned as text.",
          "type": "string"
        }
      },
      "required": [
        "function"
      ],
      "additionalProperties": false
    },
    "annotations": {
      "title": "Evaluate JavaScript",
      "readOnlyHint": false,
      "destructiveHint": true,
      "openWorldHint": true
    }
  },
  {
    "name": "browser_file_upload",
    "description": "Upload one or multiple files",
    "inputSchema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "properties": {
        "paths": {
          "description": "The absolute paths to the files to upload. Can be single file or multiple files. If omitted, file chooser is cancelled.",
          "type": "array",
          "items": {
            "type": "string"
          }
        }
      },
      "additionalProperties": false
    },
    "annotations": {
      "title": "Upload files",
      "readOnlyHint": false,
      "destructiveHint": true,
      "openWorldHint": true
    }
  },
  {
    "name": "browser_fill_form",
    "description": "Fill multiple form fields",
    "inputSchema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "properties": {
        "fields": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "name": {
                "type": "string",
                "description": "Human-readable field name"
              },
              "type": {
                "type": "string",
                "enum": [
                  "textbox",
                  "checkbox",
                  "radio",
                  "combobox",
                  "slider"
                ],
                "description": "Type of the field"
              },
              "ref": {
                "type": "string",
                "description": "Exact target field reference from the page snapshot"
              },
              "selector": {
                "description": "CSS or role selector for the field element, when \"ref\" is not available. Either \"selector\" or \"ref\" is required.",
                "type": "string"
              },
              "value": {
                "type": "string",
                "description": "Value to fill in the field. If the field is a checkbox, the value should be `true` or `false`. If the field is a combobox, the value should be the text of the option."
              }
            },
            "required": [
              "name",
              "type",
              "ref",
              "value"
            ],
            "additionalProperties": false
          },
          "description": "Fields to fill in"
        }
      },
      "required": [
        "fields"
      ],
      "additionalProperties": false
    },
    "annotations": {
      "title": "Fill form",
      "readOnlyHint": false,
      "destructiveHint": true,
      "openWorldHint": true
    }
  },
  {
    "name": "browser_press_key",
    "description": "Press a key on the keyboard",
    "inputSchema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "properties": {
        "key": {
          "type": "string",
          "description": "Name of the key to press or a character to generate, such as `ArrowLeft` or `a`"
        }
      },
      "required": [
        "key"
      ],
      "additionalProperties": false
    },
    "annotations": {
      "title": "Press a key",
      "readOnlyHint": false,
      "destructiveHint": true,
      "openWorldHint": true
    }
  },
  {
    "name": "browser_type",
    "description": "Type text into editable element",
    "inputSchema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "properties": {
        "element": {
          "description": "Human-readable element description used to obtain permission to interact with the element",
          "type": "string"
        },
        "ref": {
          "type": "string",
          "description": "Exact target element reference from the page snapshot"
        },
        "text": {
          "type": "string",
          "description": "Text to type into the element"
        },
        "submit": {
          "description": "Whether to submit entered text (press Enter after)",
          "type": "boolean"
        },
        "slowly": {
          "description": "Whether to type one character at a time. Useful for triggering key handlers in the page. By default entire text is filled in at once.",
          "type": "boolean"
        }
      },
      "required": [
        "ref",
        "text"
      ],
      "additionalProperties": false
    },
    "annotations": {
      "title": "Type text",
      "readOnlyHint": false,
      "destructiveHint": true,
      "openWorldHint": true
    }
  },
  {
    "name": "browser_navigate",
    "description": "Navigate to a URL",
    "inputSchema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "properties": {
        "url": {
          "type": "string",
          "description": "The URL to navigate to"
        }
      },
      "required": [
        "url"
      ],
      "additionalProperties": false
    },
    "annotations": {
      "title": "Navigate to a URL",
      "readOnlyHint": false,
      "destructiveHint": true,
      "openWorldHint": true
    }
  },
  {
    "name": "browser_navigate_back",
    "description": "Go back to the previous page in the history",
    "inputSchema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "properties": {},
      "additionalProperties": false
    },
    "annotations": {
      "title": "Go back",
      "readOnlyHint": false,
      "destructiveHint": true,
      "openWorldHint": true
    }
  },
  {
    "name": "browser_network_requests",
    "description": "Returns all network requests since loading the page",
    "inputSchema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "properties": {
        "static": {
          "default": false,
          "description": "Whether to include successful static resources like images, fonts, scripts, etc. Defaults to false.",
          "type": "boolean"
        },
        "requestBody": {
          "default": false,
          "description": "Whether to include request body. Defaults to false.",
          "type": "boolean"
        },
        "requestHeaders": {
          "default": false,
          "description": "Whether to include request headers. Defaults to false.",
          "type": "boolean"
        },
        "filter": {
          "description": "Only return requests whose URL matches this regexp (e.g. \"/api/.*user\").",
          "type": "string"
        },
        "filename": {
          "description": "Filename to save the network requests to. If not provided, requests are returned as text.",
          "type": "string"
        }
      },
      "required": [
        "static",
        "requestBody",
        "requestHeaders"
      ],
      "additionalProperties": false
    },
    "annotations": {
      "title": "List network requests",
      "readOnlyHint": true,
      "destructiveHint": false,
      "openWorldHint": true
    }
  },
  {
    "name": "browser_run_code",
    "description": "Run Playwright code snippet",
    "inputSchema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "properties": {
        "code": {
          "description": "A JavaScript function containing Playwright code to execute. It will be invoked with a single argument, page, which you can use for any page interaction. For example: `async (page) => { await page.getByRole('button', { name: 'Submit' }).click(); return await page.title(); }`",
          "type": "string"
        },
        "filename": {
          "description": "Load code from the specified file. If both code and filename are provided, code will be ignored.",
          "type": "string"
        }
      },
      "additionalProperties": false
    },
    "annotations": {
      "title": "Run Playwright code",
      "readOnlyHint": false,
      "destructiveHint": true,
      "openWorldHint": true
    }
  },
  {
    "name": "browser_take_screenshot",
    "description": "Take a screenshot of the current page. You can't perform actions based on the screenshot, use browser_snapshot for actions.",
    "inputSchema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "properties": {
        "type": {
          "default": "png",
          "description": "Image format for the screenshot. Default is png.",
          "type": "string",
          "enum": [
            "png",
            "jpeg"
          ]
        },
        "filename": {
          "description": "File name to save the screenshot to. Defaults to `page-{timestamp}.{png|jpeg}` if not specified. Prefer relative file names to stay within the output directory.",
          "type": "string"
        },
        "element": {
          "description": "Human-readable element description used to obtain permission to screenshot the element. If not provided, the screenshot will be taken of viewport. If element is provided, ref must be provided too.",
          "type": "string"
        },
        "ref": {
          "description": "Exact target element reference from the page snapshot. If not provided, the screenshot will be taken of viewport. If ref is provided, element must be provided too.",
          "type": "string"
        },
        "fullPage": {
          "description": "When true, takes a screenshot of the full scrollable page, instead of the currently visible viewport. Cannot be used with element screenshots.",
          "type": "boolean"
        }
      },
      "required": [
        "type"
      ],
      "additionalProperties": false
    },
    "annotations": {
      "title": "Take a screenshot",
      "readOnlyHint": true,
      "destructiveHint": false,
      "openWorldHint": true
    }
  },
  {
    "name": "browser_snapshot",
    "description": "Capture accessibility snapshot of the current page, this is better than screenshot",
    "inputSchema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "properties": {
        "filename": {
          "description": "Save snapshot to markdown file instead of returning it in the response.",
          "type": "string"
        },
        "depth": {
          "description": "Limit the depth of the snapshot tree",
          "type": "number"
        }
      },
      "additionalProperties": false
    },
    "annotations": {
      "title": "Page snapshot",
      "readOnlyHint": true,
      "destructiveHint": false,
      "openWorldHint": true
    }
  },
  {
    "name": "browser_click",
    "description": "Perform click on a web page",
    "inputSchema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "properties": {
        "element": {
          "description": "Human-readable element description used to obtain permission to interact with the element",
          "type": "string"
        },
        "ref": {
          "type": "string",
          "description": "Exact target element reference from the page snapshot"
        },
        "doubleClick": {
          "description": "Whether to perform a double click instead of a single click",
          "type": "boolean"
        },
        "button": {
          "description": "Button to click, defaults to left",
          "type": "string",
          "enum": [
            "left",
            "right",
            "middle"
          ]
        },
        "modifiers": {
          "description": "Modifier keys to press",
          "type": "array",
          "items": {
            "type": "string",
            "enum": [
              "Alt",
              "Control",
              "ControlOrMeta",
              "Meta",
              "Shift"
            ]
          }
        }
      },
      "required": [
        "ref"
      ],
      "additionalProperties": false
    },
    "annotations": {
      "title": "Click",
      "readOnlyHint": false,
      "destructiveHint": true,
      "openWorldHint": true
    }
  },
  {
    "name": "browser_drag",
    "description": "Perform drag and drop between two elements",
    "inputSchema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "properties": {
        "startElement": {
          "type": "string",
          "description": "Human-readable source element description used to obtain the permission to interact with the element"
        },
        "startRef": {
          "type": "string",
          "description": "Exact source element reference from the page snapshot"
        },
        "endElement": {
          "type": "string",
          "description": "Human-readable target element description used to obtain the permission to interact with the element"
        },
        "endRef": {
          "type": "string",
          "description": "Exact target element reference from the page snapshot"
        }
      },
      "required": [
        "startElement",
        "startRef",
        "endElement",
        "endRef"
      ],
      "additionalProperties": false
    },
    "annotations": {
      "title": "Drag mouse",
      "readOnlyHint": false,
      "destructiveHint": true,
      "openWorldHint": true
    }
  },
  {
    "name": "browser_hover",
    "description": "Hover over element on page",
    "inputSchema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "properties": {
        "element": {
          "description": "Human-readable element description used to obtain permission to interact with the element",
          "type": "string"
        },
        "ref": {
          "type": "string",
          "description": "Exact target element reference from the page snapshot"
        }
      },
      "required": [
        "ref"
      ],
      "additionalProperties": false
    },
    "annotations": {
      "title": "Hover mouse",
      "readOnlyHint": false,
      "destructiveHint": true,
      "openWorldHint": true
    }
  },
  {
    "name": "browser_select_option",
    "description": "Select an option in a dropdown",
    "inputSchema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "properties": {
        "element": {
          "description": "Human-readable element description used to obtain permission to interact with the element",
          "type": "string"
        },
        "ref": {
          "type": "string",
          "description": "Exact target element reference from the page snapshot"
        },
        "values": {
          "type": "array",
          "items": {
            "type": "string"
          },
          "description": "Array of values to select in the dropdown. This can be a single value or multiple values."
        }
      },
      "required": [
        "ref",
        "values"
      ],
      "additionalProperties": false
    },
    "annotations": {
      "title": "Select option",
      "readOnlyHint": false,
      "destructiveHint": true,
      "openWorldHint": true
    }
  },
  {
    "name": "browser_tabs",
    "description": "List, create, close, or select a browser tab.",
    "inputSchema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "properties": {
        "action": {
          "type": "string",
          "enum": [
            "list",
            "new",
            "close",
            "select"
          ],
          "description": "Operation to perform"
        },
        "index": {
          "description": "Tab index, used for close/select. If omitted for close, current tab is closed.",
          "type": "number"
        }
      },
      "required": [
        "action"
      ],
      "additionalProperties": false
    },
    "annotations": {
      "title": "Manage tabs",
      "readOnlyHint": false,
      "destructiveHint": true,
      "openWorldHint": true
    }
  },
  {
    "name": "browser_wait_for",
    "description": "Wait for text to appear or disappear or a specified time to pass",
    "inputSchema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "properties": {
        "time": {
          "description": "The time to wait in seconds",
          "type": "number"
        },
        "text": {
          "description": "The text to wait for",
          "type": "string"
        },
        "textGone": {
          "description": "The text to wait for to disappear",
          "type": "string"
        }
      },
      "additionalProperties": false
    },
    "annotations": {
      "title": "Wait for",
      "readOnlyHint": true,
      "destructiveHint": false,
      "openWorldHint": true
    }
  }
]
```
