"""CLI facade smoke tests."""

from __future__ import annotations

import json
from pathlib import Path

from toolgate.catalog import Catalog
from toolgate.collector import collect_inventory
from toolgate.cli import main
from toolgate.proxy.config import ProxyConfig, ServerEntry


async def test_cli_tools_list_reads_catalog(
    tmp_path: Path,
    capsys,
    mock_server_cmd: list[str],
) -> None:
    catalog_path = tmp_path / "catalog.db"
    catalog = Catalog(catalog_path)
    config = ProxyConfig(servers={"mock": ServerEntry(command=mock_server_cmd + ["--tools", "2"])})
    await collect_inventory(config, catalog)

    rc = main(["tools", "list", "--catalog", str(catalog_path), "--format", "json"])
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert [item["tool_id"] for item in out] == ["mock__tool_0", "mock__tool_1"]


async def test_cli_schema_enforces_profile(
    tmp_path: Path,
    capsys,
    mock_server_cmd: list[str],
) -> None:
    catalog_path = tmp_path / "catalog.db"
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "one.json").write_text(
        json.dumps({"id": "one", "include_tools": ["mock__tool_1"]}),
        encoding="utf-8",
    )
    config = ProxyConfig(servers={"mock": ServerEntry(command=mock_server_cmd + ["--tools", "2"])})
    await collect_inventory(config, Catalog(catalog_path))

    rc = main(
        [
            "schema",
            "mock__tool_0",
            "--catalog",
            str(catalog_path),
            "--profile",
            "one",
            "--profiles-dir",
            str(profiles_dir),
        ]
    )
    err = capsys.readouterr().err

    assert rc == 1
    assert "not available in profile" in err
