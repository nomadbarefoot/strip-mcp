"""Host discovery and config merge/write adapters for setup CLI."""

from __future__ import annotations

import abc
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import AppId, DiscoveredApp, DiscoveredMCP

_MANAGED_KEY = "_toolgateManaged"


@dataclass
class MergeOutcome:
    config: dict[str, Any]
    added: list[str]
    updated: list[str]
    removed: list[str]
    unchanged: list[str]
    conflicts: list[str]

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.updated or self.removed)


class HostAdapter(abc.ABC):
    """Adapter contract for each host app."""

    app_id: AppId
    display_name: str

    @abc.abstractmethod
    def discover(self, home: Path) -> DiscoveredApp | None:
        """Return app discovery info if host is present on this system."""

    @abc.abstractmethod
    def load(self, config_path: Path) -> dict[str, Any]:
        """Load config JSON from disk; returns {} when file is absent."""

    @abc.abstractmethod
    def merge(self, config: dict[str, Any], selected: list[DiscoveredMCP]) -> MergeOutcome:
        """Merge selected MCPs into host config preserving user-owned entries."""

    @abc.abstractmethod
    def validate(self, config: dict[str, Any]) -> None:
        """Raise on invalid schema/shape."""

    @abc.abstractmethod
    def write(self, config_path: Path, config: dict[str, Any], *, timestamp: str) -> Path | None:
        """Atomically write config and optional backup. Returns backup path if created."""


def _is_path_writable(path: Path) -> bool:
    if path.exists():
        return os.access(path, os.W_OK)

    parent = path.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    return os.access(parent, os.W_OK)


def _server_entry_for_mcp(mcp: DiscoveredMCP) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "command": mcp.command,
        "args": list(mcp.args),
    }
    if mcp.cwd is not None:
        payload["cwd"] = mcp.cwd
    if mcp.env:
        payload["env"] = dict(mcp.env)
    return payload


def _normalized_entry(entry: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "command": entry.get("command"),
        "args": list(entry.get("args", [])) if isinstance(entry.get("args", []), list) else [],
    }
    if "cwd" in entry:
        payload["cwd"] = entry.get("cwd")
    if "env" in entry and isinstance(entry.get("env"), dict):
        payload["env"] = dict(entry["env"])
    return payload


def _merge_managed_servers(
    *,
    app_id: AppId,
    config: dict[str, Any],
    selected: list[DiscoveredMCP],
) -> MergeOutcome:
    mcp_servers = config.get("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        raise ValueError("Config key 'mcpServers' must be a JSON object")

    managed_meta = config.get(_MANAGED_KEY, {})
    managed_ids: set[str] = set()
    if isinstance(managed_meta, dict):
        raw_ids = managed_meta.get("serverIds")
        if isinstance(raw_ids, list):
            managed_ids = {x for x in raw_ids if isinstance(x, str)}

    updated_servers = dict(mcp_servers)
    selected_map = {m.id: m for m in selected}

    added: list[str] = []
    updated: list[str] = []
    removed: list[str] = []
    unchanged: list[str] = []
    conflicts: list[str] = []

    next_managed = set(managed_ids)

    # Reversible behavior: deselecting previously managed IDs removes only managed entries.
    for server_id in sorted(managed_ids - set(selected_map.keys())):
        if server_id in updated_servers:
            del updated_servers[server_id]
            removed.append(server_id)
        next_managed.discard(server_id)

    for server_id in sorted(selected_map.keys()):
        desired = _server_entry_for_mcp(selected_map[server_id])
        existing = updated_servers.get(server_id)

        if existing is None:
            updated_servers[server_id] = desired
            added.append(server_id)
            next_managed.add(server_id)
            continue

        if not isinstance(existing, dict):
            conflicts.append(
                f"Server '{server_id}' exists but is not an object in config; left unchanged."
            )
            unchanged.append(server_id)
            continue

        if server_id in managed_ids:
            if _normalized_entry(existing) != desired:
                updated_servers[server_id] = desired
                updated.append(server_id)
            else:
                unchanged.append(server_id)
            next_managed.add(server_id)
            continue

        # User-managed entry: preserve and report.
        if _normalized_entry(existing) != desired:
            conflicts.append(
                f"Server '{server_id}' is user-managed with different settings; not modified."
            )
        unchanged.append(server_id)

    new_config = dict(config)
    new_config["mcpServers"] = updated_servers

    if next_managed:
        new_config[_MANAGED_KEY] = {
            "version": 1,
            "source": "toolgate",
            "app": app_id,
            "serverIds": sorted(next_managed),
        }
    else:
        new_config.pop(_MANAGED_KEY, None)

    return MergeOutcome(
        config=new_config,
        added=added,
        updated=updated,
        removed=removed,
        unchanged=unchanged,
        conflicts=conflicts,
    )


class JsonMcpHostAdapter(HostAdapter):
    """Shared JSON config adapter for host apps using root mcpServers map."""

    def __init__(
        self,
        *,
        app_id: AppId,
        display_name: str,
        env_var: str,
        candidate_paths: list[str],
        default_path: str,
        bundle_paths: list[str],
    ) -> None:
        self.app_id = app_id
        self.display_name = display_name
        self._env_var = env_var
        self._candidate_paths = candidate_paths
        self._default_path = default_path
        self._bundle_paths = bundle_paths

    def discover(self, home: Path) -> DiscoveredApp | None:
        override = os.environ.get(self._env_var)
        if override:
            path = Path(override).expanduser()
            return DiscoveredApp(
                id=self.app_id,
                config_path=path,
                detected_via=f"env:{self._env_var}",
                is_writable=_is_path_writable(path),
            )

        candidates = [home / rel for rel in self._candidate_paths]
        for path in candidates:
            if path.is_file():
                return DiscoveredApp(
                    id=self.app_id,
                    config_path=path,
                    detected_via="existing_config",
                    is_writable=_is_path_writable(path),
                )

        bundles = [Path(p).expanduser() for p in self._bundle_paths]
        if any(path.exists() for path in bundles):
            fallback = home / self._default_path
            return DiscoveredApp(
                id=self.app_id,
                config_path=fallback,
                detected_via="app_bundle",
                is_writable=_is_path_writable(fallback),
            )

        return None

    def load(self, config_path: Path) -> dict[str, Any]:
        if not config_path.exists():
            return {}

        raw = config_path.read_text(encoding="utf-8")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError(f"Config {config_path} must be a JSON object")
        self.validate(value)
        return value

    def merge(self, config: dict[str, Any], selected: list[DiscoveredMCP]) -> MergeOutcome:
        self.validate(config)
        return _merge_managed_servers(app_id=self.app_id, config=config, selected=selected)

    def validate(self, config: dict[str, Any]) -> None:
        if not isinstance(config, dict):
            raise ValueError("Host config must be a JSON object")
        mcp_servers = config.get("mcpServers")
        if mcp_servers is not None and not isinstance(mcp_servers, dict):
            raise ValueError("Config key 'mcpServers' must be a JSON object")

    def write(self, config_path: Path, config: dict[str, Any], *, timestamp: str) -> Path | None:
        config_path.parent.mkdir(parents=True, exist_ok=True)

        backup_path: Path | None = None
        if config_path.exists():
            backup_path = config_path.with_name(f"{config_path.name}.bak.{timestamp}")
            shutil.copy2(config_path, backup_path)

        tmp = config_path.with_name(f"{config_path.name}.tmp")
        tmp.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, config_path)
        return backup_path


def utc_timestamp_compact() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def get_host_adapters() -> dict[AppId, HostAdapter]:
    return {
        "claude": JsonMcpHostAdapter(
            app_id="claude",
            display_name="Claude",
            env_var="TOOLGATE_CLAUDE_CONFIG",
            candidate_paths=[
                "Library/Application Support/Claude/claude_desktop_config.json",
                ".claude/claude_desktop_config.json",
                ".claude/settings.json",
                ".claude/settings.local.json",
            ],
            default_path="Library/Application Support/Claude/claude_desktop_config.json",
            bundle_paths=["/Applications/Claude.app", "~/Applications/Claude.app"],
        ),
        "cursor": JsonMcpHostAdapter(
            app_id="cursor",
            display_name="Cursor",
            env_var="TOOLGATE_CURSOR_CONFIG",
            candidate_paths=[
                "Library/Application Support/Cursor/User/mcp.json",
                ".cursor/mcp.json",
                "Library/Application Support/Cursor/User/settings.json",
            ],
            default_path="Library/Application Support/Cursor/User/mcp.json",
            bundle_paths=["/Applications/Cursor.app", "~/Applications/Cursor.app"],
        ),
    }


def discover_apps(home: Path, target_ids: list[AppId]) -> list[tuple[DiscoveredApp, HostAdapter]]:
    adapters = get_host_adapters()
    out: list[tuple[DiscoveredApp, HostAdapter]] = []
    for app_id in target_ids:
        adapter = adapters[app_id]
        app = adapter.discover(home)
        if app is not None:
            out.append((app, adapter))
    return out
