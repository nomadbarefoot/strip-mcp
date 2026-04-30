"""MCP discovery helpers for setup CLI."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from ..node_discovery import DEFAULT_NODE_MCP_REGISTRY, discover_node_mcp_servers
from .models import DiscoveredMCP


def _canonical_server_id(package_name: str) -> str:
    base = package_name.rsplit("/", 1)[-1].lower()
    if base.startswith("mcp-"):
        base = base[4:]
    if base.endswith("-mcp"):
        base = base[:-4]
    if base.startswith("server-"):
        base = base[7:]
    base = re.sub(r"[^a-z0-9]+", "_", base).strip("_")
    return base or "mcp"


def _iter_package_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []

    out: list[Path] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        if child.name.startswith("@"):  # scoped packages
            for scoped in sorted(child.iterdir(), key=lambda p: p.name):
                if scoped.is_dir():
                    out.append(scoped)
            continue
        out.append(child)
    return out


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    return value


def _resolve_entry_from_package_json(package_dir: Path, data: dict[str, Any]) -> Path | None:
    package_name = data.get("name")
    if isinstance(package_name, str) and package_name in DEFAULT_NODE_MCP_REGISTRY:
        rel = DEFAULT_NODE_MCP_REGISTRY[package_name][1]
        candidate = package_dir / rel
        if candidate.is_file():
            return candidate

    bin_field = data.get("bin")
    rel: str | None = None
    if isinstance(bin_field, str):
        rel = bin_field
    elif isinstance(bin_field, dict) and bin_field:
        if isinstance(package_name, str):
            key = package_name.rsplit("/", 1)[-1]
            val = bin_field.get(key)
            if isinstance(val, str):
                rel = val
        if rel is None:
            for _, value in sorted(bin_field.items()):
                if isinstance(value, str):
                    rel = value
                    break

    if rel is None:
        main_field = data.get("main")
        if isinstance(main_field, str):
            rel = main_field

    if rel is None:
        return None

    entry = package_dir / rel
    return entry if entry.is_file() else None


def _entry_to_command(entry: Path) -> tuple[str, list[str]]:
    if entry.suffix.lower() in {".js", ".mjs", ".cjs"}:
        return ("node", [str(entry)])
    return (str(entry), [])


def resolve_npm_global_root() -> Path | None:
    try:
        proc = subprocess.run(
            ["npm", "root", "-g"],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError:
        return None

    if proc.returncode != 0:
        return None

    root = Path(proc.stdout.strip()).expanduser()
    return root if root.is_dir() else None


def discover_global_node_mcp_servers(global_root: Path | None = None) -> list[DiscoveredMCP]:
    root = global_root or resolve_npm_global_root()
    if root is None:
        return []

    out: list[DiscoveredMCP] = []
    for package_dir in _iter_package_dirs(root):
        pkg_data = _load_json(package_dir / "package.json")
        if not pkg_data:
            continue

        package_name = pkg_data.get("name")
        if not isinstance(package_name, str):
            continue

        if "mcp" not in package_name.lower() and package_name not in DEFAULT_NODE_MCP_REGISTRY:
            continue

        entry = _resolve_entry_from_package_json(package_dir, pkg_data)
        if entry is None:
            continue

        if package_name in DEFAULT_NODE_MCP_REGISTRY:
            server_id = DEFAULT_NODE_MCP_REGISTRY[package_name][0]
        else:
            server_id = _canonical_server_id(package_name)

        command, args = _entry_to_command(entry)
        out.append(
            DiscoveredMCP(
                id=server_id,
                name=package_name,
                source="node_global",
                command=command,
                args=args,
            )
        )

    return out


def _dedupe_discovered(items: list[DiscoveredMCP]) -> list[DiscoveredMCP]:
    """Deduplicate by id/command while preferring node_local over node_global."""
    by_id: dict[str, DiscoveredMCP] = {}
    seen_cmd: set[tuple[str, tuple[str, ...]]] = set()

    def priority(src: str) -> int:
        return 0 if src == "node_local" else 1

    for item in sorted(items, key=lambda x: (x.id, priority(x.source), x.command_tuple())):
        cmd_key = (item.id, item.command_tuple())
        if cmd_key in seen_cmd:
            continue
        seen_cmd.add(cmd_key)

        existing = by_id.get(item.id)
        if existing is None:
            by_id[item.id] = item
            continue
        if existing.source == "node_global" and item.source == "node_local":
            by_id[item.id] = item

    return sorted(by_id.values(), key=lambda x: x.id)


def discover_installed_mcps(
    project_root: Path | str,
    *,
    global_root: Path | None = None,
) -> list[DiscoveredMCP]:
    """Discover MCP servers from local node_modules and global npm packages."""
    local = [
        DiscoveredMCP(
            id=d.server_id,
            name=d.package_name,
            source="node_local",
            command=d.command[0],
            args=d.command[1:],
        )
        for d in discover_node_mcp_servers(project_root)
    ]
    global_discovered = discover_global_node_mcp_servers(global_root)
    return _dedupe_discovered(local + global_discovered)
