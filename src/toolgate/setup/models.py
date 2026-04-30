"""Data models for setup CLI discovery and planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

MCPSource = Literal["node_local", "node_global"]
AppId = Literal["claude", "cursor"]


@dataclass(frozen=True)
class DiscoveredMCP:
    """A locally discoverable MCP server command."""

    id: str
    name: str
    source: MCPSource
    command: str
    args: list[str] = field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] | None = None

    def command_tuple(self) -> tuple[str, ...]:
        return (self.command, *self.args)


@dataclass(frozen=True)
class DiscoveredApp:
    """A target host app and chosen config path."""

    id: AppId
    config_path: Path
    detected_via: str
    is_writable: bool


@dataclass
class PlannedChange:
    """Preview/apply summary for one target app."""

    app_id: AppId
    config_path: Path
    selected_server_ids: list[str]
    added_servers: list[str]
    updated_servers: list[str]
    removed_servers: list[str]
    unchanged_servers: list[str]
    conflict_notes: list[str]
    backup_path: Path | None = None


@dataclass
class ApplyResult:
    """Per-app write status during --apply."""

    app_id: AppId
    config_path: Path
    status: Literal["applied", "unchanged", "failed"]
    backup_path: Path | None = None
    error: str | None = None
