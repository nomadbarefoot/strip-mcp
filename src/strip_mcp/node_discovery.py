"""Discover locally installed Node MCP servers from package.json + node_modules.

Maps known npm package names to (server_id, path under node_modules) so StripMCP can
register servers without hand-editing paths when dependencies are present.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Known MCP packages: npm name -> (strip server_id, relative path to entry .js from package root)
DEFAULT_NODE_MCP_REGISTRY: dict[str, tuple[str, str]] = {
    "@playwright/mcp": ("playwright", "cli.js"),
    "wikipedia-mcp": ("wiki", "dist/index.js"),
    "@modelcontextprotocol/server-memory": ("memory", "dist/index.js"),
}


@dataclass(frozen=True)
class DiscoveredNodeServer:
    """One MCP server that exists on disk under node_modules."""

    package_name: str
    server_id: str
    command: list[str]


def _merge_registry(
    extra: dict[str, tuple[str, str]] | None,
) -> dict[str, tuple[str, str]]:
    merged = dict(DEFAULT_NODE_MCP_REGISTRY)
    if extra:
        merged.update(extra)
    return merged


def discover_node_mcp_servers(
    project_root: Path | str,
    *,
    registry: dict[str, tuple[str, str]] | None = None,
) -> list[DiscoveredNodeServer]:
    """Scan package.json dependencies for known MCP packages with installed entry files.

    Returns servers in deterministic order (sorted by server_id). Empty list if
    package.json is missing or no known packages resolve.
    """
    root = Path(project_root).resolve()
    pkg_path = root / "package.json"
    if not pkg_path.is_file():
        return []

    try:
        data: dict[str, Any] = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    deps: dict[str, str] = {}
    for key in ("dependencies", "devDependencies", "optionalDependencies"):
        block = data.get(key)
        if isinstance(block, dict):
            deps.update(block)

    reg = _merge_registry(registry)
    out: list[DiscoveredNodeServer] = []

    for npm_name in sorted(deps.keys()):
        if npm_name not in reg:
            continue
        server_id, rel = reg[npm_name]
        entry = root / "node_modules" / npm_name / rel
        if not entry.is_file():
            continue
        out.append(
            DiscoveredNodeServer(
                package_name=npm_name,
                server_id=server_id,
                command=["node", str(entry)],
            )
        )

    out.sort(key=lambda s: s.server_id)
    return out
