"""Declarative tool profiles for curated MCP facades."""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import CatalogTool
from .types import ToolBrief

DEFAULT_PROFILES_DIR = Path.home() / ".toolgate" / "profiles"


@dataclass(frozen=True)
class ToolProfile:
    """A named curated subset of the collected tool inventory."""

    id: str
    description: str = ""
    include_servers: list[str] = field(default_factory=list)
    include_tools: list[str] = field(default_factory=list)
    exclude_tools: list[str] = field(default_factory=list)
    description_overrides: dict[str, str] = field(default_factory=dict)

    def allows(self, tool_id: str, server_id: str) -> bool:
        included = self._included(tool_id, server_id)
        excluded = _matches_any(tool_id, self.exclude_tools)
        return included and not excluded

    def filter_briefs(self, briefs: list[ToolBrief]) -> list[ToolBrief]:
        filtered: list[ToolBrief] = []
        for brief in briefs:
            if not self.allows(brief.name, brief.server_id):
                continue
            override = self.description_overrides.get(brief.name)
            if override is None:
                filtered.append(brief)
                continue
            filtered.append(
                ToolBrief(
                    name=brief.name,
                    description=override,
                    server_id=brief.server_id,
                    requires_params=brief.requires_params,
                    full_schema=brief.full_schema,
                )
            )
        return filtered

    def filter_catalog_tools(self, tools: list[CatalogTool]) -> list[CatalogTool]:
        return [tool for tool in tools if self.allows(tool.tool_id, tool.server_id)]

    def needs_server(self, server_id: str) -> bool:
        """Return True if this profile may expose tools from server_id."""
        if server_id in self.include_servers:
            return True
        if not self.include_servers and not self.include_tools:
            return True
        prefix = f"{server_id}__"
        return any(
            pattern == "*"
            or pattern.startswith("*")
            or pattern.startswith(prefix)
            or "__" not in pattern
            or fnmatch.fnmatchcase(prefix, pattern)
            for pattern in self.include_tools
        )

    def _included(self, tool_id: str, server_id: str) -> bool:
        if not self.include_servers and not self.include_tools:
            return True
        if server_id in self.include_servers:
            return True
        return _matches_any(tool_id, self.include_tools)


def load_profile(profile_id: str, profiles_dir: Path = DEFAULT_PROFILES_DIR) -> ToolProfile:
    path = profiles_dir / f"{profile_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Profile not found: {profile_id!r} at {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid profile JSON at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Profile must be a JSON object: {path}")
    return profile_from_dict(data, fallback_id=profile_id)


def list_profiles(profiles_dir: Path = DEFAULT_PROFILES_DIR) -> list[ToolProfile]:
    if not profiles_dir.is_dir():
        return []
    profiles: list[ToolProfile] = []
    for path in sorted(profiles_dir.glob("*.json")):
        profiles.append(load_profile(path.stem, profiles_dir))
    return profiles


def profile_from_dict(data: dict[str, object], *, fallback_id: str | None = None) -> ToolProfile:
    profile_id = _string_value(data, "id", fallback_id)
    if not profile_id:
        raise ValueError("Profile requires non-empty 'id'")
    return ToolProfile(
        id=profile_id,
        description=_string_value(data, "description", "") or "",
        include_servers=_string_list(data, "include_servers"),
        include_tools=_string_list(data, "include_tools"),
        exclude_tools=_string_list(data, "exclude_tools"),
        description_overrides=_string_map(data, "description_overrides"),
    )


def _matches_any(value: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(value, pattern) for pattern in patterns)


def _string_value(
    data: dict[str, object], key: str, default: str | None
) -> str | None:
    value = data.get(key, default)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Profile field {key!r} must be a string")
    return value


def _string_list(data: dict[str, object], key: str) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Profile field {key!r} must be a list of strings")
    return list(value)


def _string_map(data: dict[str, object], key: str) -> dict[str, str]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"Profile field {key!r} must be an object")
    out: dict[str, str] = {}
    for map_key, map_value in value.items():
        if not isinstance(map_key, str) or not isinstance(map_value, str):
            raise ValueError(f"Profile field {key!r} must map strings to strings")
        out[map_key] = map_value
    return out
