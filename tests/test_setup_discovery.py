"""Node MCP discovery, setup CLI, host adapters, and global/local discovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from toolgate import cli
from toolgate.node_discovery import discover_node_mcp_servers
from toolgate.setup.discovery import discover_global_node_mcp_servers, discover_installed_mcps
from toolgate.setup.hosts import get_host_adapters
from toolgate.setup.models import DiscoveredMCP


# ── toolgate.node_discovery (Python-side registry) ───────────────────────────


def test_discover_empty_without_package_json(tmp_path: Path) -> None:
    assert discover_node_mcp_servers(tmp_path) == []


def test_discover_resolves_known_packages(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "devDependencies": {
                    "@playwright/mcp": "^0.0.1",
                    "wikipedia-mcp": "^1.0.0",
                    "@modelcontextprotocol/server-memory": "^0.6.0",
                }
            }
        ),
        encoding="utf-8",
    )
    for pkg, rel in (
        ("@playwright/mcp", "cli.js"),
        ("wikipedia-mcp", "dist/index.js"),
        ("@modelcontextprotocol/server-memory", "dist/index.js"),
    ):
        p = tmp_path / "node_modules" / pkg / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("// stub\n", encoding="utf-8")

    found = discover_node_mcp_servers(tmp_path)
    ids = {s.server_id for s in found}
    assert ids == {"memory", "playwright", "wiki"}
    assert all(s.command[0] == "node" for s in found)
    assert all(Path(s.command[1]).is_file() for s in found)


def test_unknown_package_ignored(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"some-random-pkg": "1.0.0"}}), encoding="utf-8"
    )
    assert discover_node_mcp_servers(tmp_path) == []


def test_custom_registry(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"my-custom-mcp": "1.0.0"}}), encoding="utf-8"
    )
    entry = tmp_path / "node_modules" / "my-custom-mcp" / "index.js"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("", encoding="utf-8")

    reg = {"my-custom-mcp": ("custom", "index.js")}
    found = discover_node_mcp_servers(tmp_path, registry=reg)
    assert len(found) == 1
    assert found[0].server_id == "custom"


# ── toolgate.setup.discovery ─────────────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_discover_global_from_package_bin(tmp_path: Path) -> None:
    global_root = tmp_path / "global"

    _write(
        global_root / "mcp-echo" / "package.json",
        json.dumps({"name": "mcp-echo", "bin": "dist/cli.js"}),
    )
    _write(global_root / "mcp-echo" / "dist" / "cli.js", "// js\n")

    found = discover_global_node_mcp_servers(global_root)
    assert len(found) == 1
    assert found[0].id == "echo"
    assert found[0].source == "node_global"
    assert found[0].command == "node"
    assert found[0].args and found[0].args[0].endswith("dist/cli.js")


def test_discover_installed_prefers_local_over_global(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    _write(
        project / "package.json",
        json.dumps({"dependencies": {"@playwright/mcp": "^0.0.1"}}),
    )
    _write(project / "node_modules" / "@playwright" / "mcp" / "cli.js", "// local\n")

    global_root = tmp_path / "global"
    _write(
        global_root / "@playwright" / "mcp" / "package.json",
        json.dumps({"name": "@playwright/mcp", "bin": "cli.js"}),
    )
    _write(global_root / "@playwright" / "mcp" / "cli.js", "// global\n")

    _write(
        global_root / "wikipedia-mcp" / "package.json",
        json.dumps({"name": "wikipedia-mcp", "main": "dist/index.js"}),
    )
    _write(global_root / "wikipedia-mcp" / "dist" / "index.js", "// wiki\n")

    found = discover_installed_mcps(project, global_root=global_root)
    ids = {m.id for m in found}
    assert "playwright" in ids
    assert "wiki" in ids

    playwright = next(m for m in found if m.id == "playwright")
    assert playwright.source == "node_local"
    assert "node_modules" in " ".join(playwright.args)


# ── toolgate.setup.hosts ─────────────────────────────────────────────────────


def _mcp(server_id: str, *, command: str = "node", args: list[str] | None = None) -> DiscoveredMCP:
    return DiscoveredMCP(
        id=server_id,
        name=server_id,
        source="node_local",
        command=command,
        args=args or [f"/{server_id}.js"],
    )


def test_cursor_discovery_prefers_existing_config(tmp_path: Path) -> None:
    home = tmp_path
    cfg = home / "Library" / "Application Support" / "Cursor" / "User" / "mcp.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("{}", encoding="utf-8")

    adapter = get_host_adapters()["cursor"]
    app = adapter.discover(home)

    assert app is not None
    assert app.id == "cursor"
    assert app.config_path == cfg
    assert app.detected_via == "existing_config"


def test_merge_updates_managed_and_preserves_user_entries() -> None:
    adapter = get_host_adapters()["claude"]

    original = {
        "mcpServers": {
            "user_server": {"command": "node", "args": ["/user.js"]},
            "playwright": {"command": "node", "args": ["/old-playwright.js"]},
        },
        "_toolgateManaged": {
            "version": 1,
            "source": "toolgate",
            "app": "claude",
            "serverIds": ["playwright"],
        },
    }

    out = adapter.merge(original, [_mcp("playwright", args=["/new-playwright.js"]), _mcp("wiki")])

    assert out.updated == ["playwright"]
    assert out.added == ["wiki"]
    assert out.removed == []
    assert out.config["mcpServers"]["user_server"]["args"] == ["/user.js"]
    assert out.config["mcpServers"]["playwright"]["args"] == ["/new-playwright.js"]
    assert out.config["_toolgateManaged"]["serverIds"] == ["playwright", "wiki"]


def test_merge_removes_deselected_managed() -> None:
    adapter = get_host_adapters()["claude"]

    original = {
        "mcpServers": {
            "playwright": {"command": "node", "args": ["/p.js"]},
            "wiki": {"command": "node", "args": ["/w.js"]},
        },
        "_toolgateManaged": {
            "version": 1,
            "source": "toolgate",
            "app": "claude",
            "serverIds": ["playwright", "wiki"],
        },
    }

    out = adapter.merge(original, [_mcp("playwright", args=["/p.js"])])

    assert out.removed == ["wiki"]
    assert "wiki" not in out.config["mcpServers"]
    assert out.config["_toolgateManaged"]["serverIds"] == ["playwright"]


def test_merge_reports_conflict_for_user_managed_entry() -> None:
    adapter = get_host_adapters()["cursor"]

    original = {
        "mcpServers": {
            "playwright": {"command": "node", "args": ["/user-owned.js"]},
        }
    }

    out = adapter.merge(original, [_mcp("playwright", args=["/toolgate-owned.js"])])

    assert out.added == []
    assert out.updated == []
    assert out.unchanged == ["playwright"]
    assert out.conflicts
    assert "user-managed" in out.conflicts[0]


def test_write_creates_backup_and_atomic_file(tmp_path: Path) -> None:
    adapter = get_host_adapters()["claude"]
    config_path = tmp_path / "claude.json"
    config_path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

    backup = adapter.write(
        config_path,
        {"mcpServers": {"wiki": {"command": "node", "args": ["/wiki.js"]}}},
        timestamp="20260406T120000Z",
    )

    assert backup is not None
    assert backup.exists()
    loaded = json.loads(config_path.read_text(encoding="utf-8"))
    assert "wiki" in loaded["mcpServers"]


# ── toolgate CLI (setup) ─────────────────────────────────────────────────────


def _make_local_playwright_project(root: Path) -> None:
    _write(
        root / "package.json",
        json.dumps({"dependencies": {"@playwright/mcp": "^0.0.1"}}),
    )
    _write(root / "node_modules" / "@playwright" / "mcp" / "cli.js", "// playwright\n")


def _force_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.platform, "system", lambda: "Darwin")


def test_setup_preview_non_interactive_does_not_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_darwin(monkeypatch)

    project = tmp_path / "project"
    home = tmp_path / "home"
    _make_local_playwright_project(project)

    claude_cfg = home / "claude.json"
    cursor_cfg = home / "cursor.json"
    monkeypatch.setenv("TOOLGATE_CLAUDE_CONFIG", str(claude_cfg))
    monkeypatch.setenv("TOOLGATE_CURSOR_CONFIG", str(cursor_cfg))

    rc = cli.main(
        [
            "setup",
            "--apps",
            "claude,cursor",
            "--non-interactive",
            "--project-root",
            str(project),
            "--home",
            str(home),
            "--global-root",
            str(tmp_path / "global-empty"),
        ]
    )

    assert rc == 0
    assert not claude_cfg.exists()
    assert not cursor_cfg.exists()


def test_setup_apply_creates_backup_and_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _force_darwin(monkeypatch)

    project = tmp_path / "project"
    home = tmp_path / "home"
    _make_local_playwright_project(project)

    claude_cfg = home / "claude.json"
    cursor_cfg = home / "cursor.json"
    _write(claude_cfg, json.dumps({"mcpServers": {}}))
    _write(cursor_cfg, json.dumps({"mcpServers": {}}))

    monkeypatch.setenv("TOOLGATE_CLAUDE_CONFIG", str(claude_cfg))
    monkeypatch.setenv("TOOLGATE_CURSOR_CONFIG", str(cursor_cfg))

    argv = [
        "setup",
        "--apps",
        "claude,cursor",
        "--non-interactive",
        "--apply",
        "--project-root",
        str(project),
        "--home",
        str(home),
        "--global-root",
        str(tmp_path / "global-empty"),
    ]

    rc1 = cli.main(argv)
    assert rc1 == 0

    claude_backups_1 = sorted(claude_cfg.parent.glob("claude.json.bak.*"))
    cursor_backups_1 = sorted(cursor_cfg.parent.glob("cursor.json.bak.*"))
    assert len(claude_backups_1) == 1
    assert len(cursor_backups_1) == 1

    rc2 = cli.main(argv)
    assert rc2 == 0

    out = capsys.readouterr().out
    assert "unchanged" in out

    claude_backups_2 = sorted(claude_cfg.parent.glob("claude.json.bak.*"))
    cursor_backups_2 = sorted(cursor_cfg.parent.glob("cursor.json.bak.*"))
    assert len(claude_backups_2) == 1
    assert len(cursor_backups_2) == 1


def test_setup_apply_partial_failure_continues_other_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_darwin(monkeypatch)

    project = tmp_path / "project"
    home = tmp_path / "home"
    _make_local_playwright_project(project)

    claude_cfg = home / "claude.json"
    bad_cursor_target = home / "cursor-dir"
    bad_cursor_target.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("TOOLGATE_CLAUDE_CONFIG", str(claude_cfg))
    monkeypatch.setenv("TOOLGATE_CURSOR_CONFIG", str(bad_cursor_target))

    rc = cli.main(
        [
            "setup",
            "--apps",
            "claude,cursor",
            "--non-interactive",
            "--apply",
            "--project-root",
            str(project),
            "--home",
            str(home),
            "--global-root",
            str(tmp_path / "global-empty"),
        ]
    )

    assert rc == 1
    assert claude_cfg.exists()


def test_setup_non_interactive_unknown_select_is_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_darwin(monkeypatch)

    project = tmp_path / "project"
    home = tmp_path / "home"
    _make_local_playwright_project(project)

    monkeypatch.setenv("TOOLGATE_CLAUDE_CONFIG", str(home / "claude.json"))
    monkeypatch.setenv("TOOLGATE_CURSOR_CONFIG", str(home / "cursor.json"))

    rc = cli.main(
        [
            "setup",
            "--apps",
            "claude,cursor",
            "--non-interactive",
            "--select",
            "does_not_exist",
            "--project-root",
            str(project),
            "--home",
            str(home),
            "--global-root",
            str(tmp_path / "global-empty"),
        ]
    )

    assert rc == 2


def test_setup_json_includes_planning_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _force_darwin(monkeypatch)

    project = tmp_path / "project"
    home = tmp_path / "home"
    _make_local_playwright_project(project)

    bad_target = home / "claude-dir"
    bad_target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TOOLGATE_CLAUDE_CONFIG", str(bad_target))

    rc = cli.main(
        [
            "setup",
            "--apps",
            "claude",
            "--non-interactive",
            "--json",
            "--project-root",
            str(project),
            "--home",
            str(home),
            "--global-root",
            str(tmp_path / "global-empty"),
        ]
    )
    assert rc == 1

    payload = json.loads(capsys.readouterr().out)
    assert "planning_errors" in payload
    assert payload["planning_errors"]


def test_install_ignores_existing_toolgate_proxy_entry(
    tmp_path: Path,
) -> None:
    settings_path = tmp_path / "settings.json"
    proxy_path = tmp_path / "proxy.json"
    settings_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "toolgate": {
                        "command": "/usr/local/bin/toolgate",
                        "args": ["proxy", "--config", str(proxy_path)],
                    },
                    "wiki": {
                        "command": "node",
                        "args": ["/tmp/wiki.js"],
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    rc = cli.main(
        [
            "install",
            "--claude-config",
            str(settings_path),
            "--proxy-config",
            str(proxy_path),
        ]
    )
    assert rc == 0

    proxy_data = json.loads(proxy_path.read_text(encoding="utf-8"))
    assert set(proxy_data["servers"].keys()) == {"wiki"}
