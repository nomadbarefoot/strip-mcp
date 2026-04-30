"""Catalog collection and profile filtering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from toolgate.catalog import Catalog
from toolgate.collector import collect_inventory
from toolgate.profiles import load_profile
from toolgate.proxy.config import ProxyConfig, ServerEntry


async def test_collect_inventory_writes_tools(
    tmp_path: Path,
    mock_server_cmd: list[str],
) -> None:
    catalog = Catalog(tmp_path / "catalog.db")
    config = ProxyConfig(servers={"mock": ServerEntry(command=mock_server_cmd + ["--tools", "3"])})

    report = await collect_inventory(config, catalog)
    tools = catalog.list_tools()

    assert report.ok_count == 1
    assert [tool.tool_id for tool in tools] == [
        "mock__tool_0",
        "mock__tool_1",
        "mock__tool_2",
    ]


async def test_collect_inventory_records_failed_server(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "catalog.db")
    config = ProxyConfig(servers={"bad": ServerEntry(command=["definitely_missing_mcp_binary"])})

    report = await collect_inventory(config, catalog)

    assert report.failed_count == 1
    assert catalog.list_tools() == []


def test_profile_filters_catalog_tools(tmp_path: Path) -> None:
    profile_path = tmp_path / "browser.json"
    profile_path.write_text(
        json.dumps(
            {
                "id": "browser",
                "include_servers": ["mock"],
                "exclude_tools": ["mock__tool_0"],
                "description_overrides": {"mock__tool_1": "Override"},
            }
        ),
        encoding="utf-8",
    )
    profile = load_profile("browser", tmp_path)

    assert profile.allows("mock__tool_1", "mock")
    assert not profile.allows("mock__tool_0", "mock")
    assert profile.description_overrides["mock__tool_1"] == "Override"


def test_missing_profile_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Profile not found"):
        load_profile("missing", tmp_path)
