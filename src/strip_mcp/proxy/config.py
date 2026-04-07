"""ProxyConfig — load/save proxy server configuration."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path.home() / ".strip-mcp" / "config.json"


@dataclass
class ServerEntry:
    command: list[str]
    # env is stored for future use; not yet passed to StdioConnection
    env: dict[str, str] | None = None


@dataclass
class ProxyConfig:
    servers: dict[str, ServerEntry] = field(default_factory=dict)
    # Raw original mcpServers from the host app — stored for clean uninstall
    original_mcp_servers: dict[str, Any] = field(default_factory=dict)
    version: int = 1

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH) -> "ProxyConfig":
        """Load config from a JSON file."""
        with open(path) as f:
            data = json.load(f)

        servers: dict[str, ServerEntry] = {}
        for sid, entry in data.get("servers", {}).items():
            servers[sid] = ServerEntry(
                command=entry["command"],
                env=entry.get("env"),
            )

        return cls(
            servers=servers,
            original_mcp_servers=data.get("original_mcp_servers", {}),
            version=data.get("version", 1),
        )

    def save(self, path: Path = DEFAULT_CONFIG_PATH) -> None:
        """Atomically write config to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {
            "version": self.version,
            "servers": {
                sid: {
                    "command": entry.command,
                    **({"env": entry.env} if entry.env else {}),
                }
                for sid, entry in self.servers.items()
            },
        }
        if self.original_mcp_servers:
            data["original_mcp_servers"] = self.original_mcp_servers

        tmp = path.with_suffix(".tmp")
        try:
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            os.replace(tmp, path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def is_empty(self) -> bool:
        return not self.servers
