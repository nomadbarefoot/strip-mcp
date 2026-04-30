"""SQLite inventory catalog for discovered MCP tools."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CATALOG_PATH = Path.home() / ".toolgate" / "catalog.db"


@dataclass(frozen=True)
class CatalogServer:
    """One probed upstream server."""

    server_id: str
    command: list[str]
    cwd: str | None
    status: str
    error: str | None
    tool_count: int
    updated_at: str


@dataclass(frozen=True)
class CatalogTool:
    """One normalized tool in the local inventory."""

    tool_id: str
    server_id: str
    raw_name: str
    description: str
    input_schema: dict[str, Any]
    requires_params: bool
    annotations: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


class Catalog:
    """Small SQLite wrapper for MCP server/tool inventory."""

    def __init__(self, path: Path = DEFAULT_CATALOG_PATH) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS servers (
                    server_id TEXT PRIMARY KEY,
                    command_json TEXT NOT NULL,
                    cwd TEXT,
                    env_keys_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    tool_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tools (
                    tool_id TEXT PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    raw_name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    input_schema_json TEXT NOT NULL,
                    output_schema_json TEXT,
                    annotations_json TEXT,
                    requires_params INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(server_id) REFERENCES servers(server_id)
                );
                CREATE INDEX IF NOT EXISTS idx_tools_server_id ON tools(server_id);
                """
            )

    def upsert_server(
        self,
        *,
        server_id: str,
        command: list[str],
        cwd: str | None,
        env: dict[str, str] | None,
        status: str,
        error: str | None,
        tool_count: int,
        updated_at: str,
    ) -> None:
        env_keys = sorted((env or {}).keys())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO servers (
                    server_id, command_json, cwd, env_keys_json, status,
                    error, tool_count, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(server_id) DO UPDATE SET
                    command_json=excluded.command_json,
                    cwd=excluded.cwd,
                    env_keys_json=excluded.env_keys_json,
                    status=excluded.status,
                    error=excluded.error,
                    tool_count=excluded.tool_count,
                    updated_at=excluded.updated_at
                """,
                (
                    server_id,
                    json.dumps(command),
                    cwd,
                    json.dumps(env_keys),
                    status,
                    error,
                    tool_count,
                    updated_at,
                ),
            )

    def replace_server_tools(
        self,
        *,
        server_id: str,
        tools: list[CatalogTool],
        updated_at: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM tools WHERE server_id = ?", (server_id,))
            conn.executemany(
                """
                INSERT INTO tools (
                    tool_id, server_id, raw_name, description, input_schema_json,
                    output_schema_json, annotations_json, requires_params, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        tool.tool_id,
                        tool.server_id,
                        tool.raw_name,
                        tool.description,
                        json.dumps(tool.input_schema),
                        json.dumps(tool.output_schema) if tool.output_schema is not None else None,
                        json.dumps(tool.annotations) if tool.annotations is not None else None,
                        1 if tool.requires_params else 0,
                        updated_at,
                    )
                    for tool in tools
                ],
            )

    def list_tools(self) -> list[CatalogTool]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT tool_id, server_id, raw_name, description, input_schema_json,
                       output_schema_json, annotations_json, requires_params
                FROM tools
                ORDER BY tool_id
                """
            ).fetchall()
        return [self._tool_from_row(row) for row in rows]

    def search_tools(self, query: str) -> list[CatalogTool]:
        needle = f"%{query.lower()}%"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT tool_id, server_id, raw_name, description, input_schema_json,
                       output_schema_json, annotations_json, requires_params
                FROM tools
                WHERE lower(tool_id) LIKE ? OR lower(description) LIKE ?
                ORDER BY tool_id
                """,
                (needle, needle),
            ).fetchall()
        return [self._tool_from_row(row) for row in rows]

    def get_tool(self, tool_id: str) -> CatalogTool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT tool_id, server_id, raw_name, description, input_schema_json,
                       output_schema_json, annotations_json, requires_params
                FROM tools
                WHERE tool_id = ?
                """,
                (tool_id,),
            ).fetchone()
        if row is None:
            raise KeyError(tool_id)
        return self._tool_from_row(row)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _tool_from_row(self, row: sqlite3.Row) -> CatalogTool:
        output_schema_raw = row["output_schema_json"]
        annotations_raw = row["annotations_json"]
        return CatalogTool(
            tool_id=row["tool_id"],
            server_id=row["server_id"],
            raw_name=row["raw_name"],
            description=row["description"],
            input_schema=json.loads(row["input_schema_json"]),
            output_schema=json.loads(output_schema_raw) if output_schema_raw else None,
            annotations=json.loads(annotations_raw) if annotations_raw else None,
            requires_params=bool(row["requires_params"]),
        )
