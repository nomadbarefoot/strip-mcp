"""Command line entrypoint for toolgate."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .setup.discovery import discover_installed_mcps
from .setup.hosts import HostAdapter, discover_apps, utc_timestamp_compact
from .setup.models import AppId, ApplyResult, DiscoveredApp, DiscoveredMCP, PlannedChange


@dataclass
class _AppPlan:
    app: DiscoveredApp
    adapter: HostAdapter
    selected: list[DiscoveredMCP]
    change: PlannedChange
    merged_config: dict[str, Any]


def _parse_csv(raw: str | None) -> list[str]:
    if raw is None:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _proxy_config_path(raw: str | None) -> Path:
    from .proxy.config import DEFAULT_CONFIG_PATH

    return Path(raw).expanduser().resolve() if raw else DEFAULT_CONFIG_PATH


def _catalog_path(raw: str | None) -> Path:
    from .catalog import DEFAULT_CATALOG_PATH

    return Path(raw).expanduser().resolve() if raw else DEFAULT_CATALOG_PATH


def _profiles_dir(raw: str | None) -> Path:
    from .profiles import DEFAULT_PROFILES_DIR

    return Path(raw).expanduser().resolve() if raw else DEFAULT_PROFILES_DIR


def _load_profile_arg(profile: str | None, profiles_dir: str | None):
    if profile is None:
        return None
    from .profiles import load_profile

    return load_profile(profile, _profiles_dir(profiles_dir))


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _parse_app_ids(raw: str | None) -> list[AppId]:
    default: list[AppId] = ["claude", "cursor"]
    items = _parse_csv(raw)
    if not items:
        return default

    allowed = {"claude", "cursor"}
    invalid = [it for it in items if it not in allowed]
    if invalid:
        raise ValueError(f"Unknown app id(s): {', '.join(invalid)}")
    return [it for it in items if it in allowed]  # type: ignore[return-value]


def _prompt_deselect(title: str, ids: list[str]) -> set[str]:
    if not ids:
        return set()

    print(f"\n{title}")
    for i, item_id in enumerate(ids, start=1):
        print(f"  {i}. {item_id}")
    print("All are preselected.")

    while True:
        raw = input("Enter comma-separated numbers to deselect (blank to keep all): ").strip()
        if not raw:
            return set(ids)

        parts = [p.strip() for p in raw.split(",") if p.strip()]
        try:
            indexes = {int(p) for p in parts}
        except ValueError:
            print("Invalid input. Please enter numbers like: 2,4")
            continue

        if any(i < 1 or i > len(ids) for i in indexes):
            print("Out-of-range selection.")
            continue

        deselected = {ids[i - 1] for i in indexes}
        return set(ids) - deselected


def _render_preview(discovered_apps: list[DiscoveredApp], mcps: list[DiscoveredMCP], plans: list[_AppPlan]) -> None:
    print("=== toolgate setup preview ===")

    print("\nDiscovered apps:")
    if not discovered_apps:
        print("  (none)")
    else:
        for app in discovered_apps:
            writable = "yes" if app.is_writable else "no"
            print(f"  - {app.id}: {app.config_path} (detected via {app.detected_via}, writable={writable})")

    print("\nDiscovered MCP servers:")
    if not mcps:
        print("  (none)")
    else:
        for m in mcps:
            cmd = " ".join([m.command, *m.args])
            print(f"  - {m.id} [{m.source}] ({m.name}) -> {cmd}")

    print("\nPlanned changes:")
    if not plans:
        print("  (none)")
        return

    for plan in plans:
        ch = plan.change
        print(f"\n[{ch.app_id}] {ch.config_path}")
        print(f"  selected: {', '.join(ch.selected_server_ids) if ch.selected_server_ids else '(none)'}")
        print(f"  add/update/remove/unchanged: {len(ch.added_servers)}/{len(ch.updated_servers)}/{len(ch.removed_servers)}/{len(ch.unchanged_servers)}")
        if ch.added_servers:
            print(f"  + {', '.join(ch.added_servers)}")
        if ch.updated_servers:
            print(f"  ~ {', '.join(ch.updated_servers)}")
        if ch.removed_servers:
            print(f"  - {', '.join(ch.removed_servers)}")
        if ch.conflict_notes:
            print("  conflicts:")
            for note in ch.conflict_notes:
                print(f"    * {note}")


def _build_json_payload(
    *,
    mode: str,
    apply: bool,
    discovered_apps: list[DiscoveredApp],
    mcps: list[DiscoveredMCP],
    plans: list[_AppPlan],
    plan_errors: list[ApplyResult] | None = None,
    apply_results: list[ApplyResult] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mode": mode,
        "apply": apply,
        "platform": platform.system(),
        "apps": [
            {
                "id": a.id,
                "config_path": str(a.config_path),
                "detected_via": a.detected_via,
                "is_writable": a.is_writable,
            }
            for a in discovered_apps
        ],
        "mcps": [
            {
                "id": m.id,
                "name": m.name,
                "source": m.source,
                "command": m.command,
                "args": m.args,
                "cwd": m.cwd,
                "env": m.env,
            }
            for m in mcps
        ],
        "changes": [
            {
                "app_id": p.change.app_id,
                "config_path": str(p.change.config_path),
                "selected_server_ids": p.change.selected_server_ids,
                "added_servers": p.change.added_servers,
                "updated_servers": p.change.updated_servers,
                "removed_servers": p.change.removed_servers,
                "unchanged_servers": p.change.unchanged_servers,
                "conflict_notes": p.change.conflict_notes,
                "backup_path": str(p.change.backup_path) if p.change.backup_path else None,
            }
            for p in plans
        ],
    }
    if apply_results is not None:
        payload["apply_results"] = [
            {
                "app_id": r.app_id,
                "config_path": str(r.config_path),
                "status": r.status,
                "backup_path": str(r.backup_path) if r.backup_path else None,
                "error": r.error,
            }
            for r in apply_results
        ]
    if plan_errors is not None:
        payload["planning_errors"] = [
            {
                "app_id": r.app_id,
                "config_path": str(r.config_path),
                "status": r.status,
                "error": r.error,
            }
            for r in plan_errors
        ]
    return payload


def _select_mcps_for_apps(
    *,
    mcps: list[DiscoveredMCP],
    app_ids: list[AppId],
    non_interactive: bool,
    select_ids: list[str],
) -> dict[AppId, set[str]]:
    all_ids = [m.id for m in mcps]
    unknown = sorted(set(select_ids) - set(all_ids))
    if unknown:
        raise ValueError(f"Unknown MCP server id(s): {', '.join(unknown)}")

    selection: dict[AppId, set[str]] = {}
    if non_interactive:
        chosen = set(select_ids) if select_ids else set(all_ids)
        for app_id in app_ids:
            selection[app_id] = set(chosen)
        return selection

    chosen_apps = _prompt_deselect("Step 2: Select apps", app_ids)
    if not chosen_apps:
        return {}

    for app_id in app_ids:
        if app_id not in chosen_apps:
            continue
        selected_for_app = _prompt_deselect(
            f"Step 3: Select MCPs for {app_id}",
            all_ids,
        )
        selection[app_id] = selected_for_app

    return selection


def _build_plans(
    *,
    discovered: list[tuple[DiscoveredApp, HostAdapter]],
    selection: dict[AppId, set[str]],
    mcps: list[DiscoveredMCP],
) -> tuple[list[_AppPlan], list[ApplyResult]]:
    mcps_by_id = {m.id: m for m in mcps}
    plans: list[_AppPlan] = []
    errors: list[ApplyResult] = []

    for app, adapter in discovered:
        if app.id not in selection:
            continue

        selected_ids = sorted(selection[app.id])
        selected_mcps = [mcps_by_id[sid] for sid in selected_ids if sid in mcps_by_id]

        try:
            config = adapter.load(app.config_path)
            outcome = adapter.merge(config, selected_mcps)
        except Exception as exc:  # noqa: BLE001
            errors.append(
                ApplyResult(
                    app_id=app.id,
                    config_path=app.config_path,
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue

        change = PlannedChange(
            app_id=app.id,
            config_path=app.config_path,
            selected_server_ids=selected_ids,
            added_servers=outcome.added,
            updated_servers=outcome.updated,
            removed_servers=outcome.removed,
            unchanged_servers=outcome.unchanged,
            conflict_notes=outcome.conflicts,
        )
        plans.append(
            _AppPlan(
                app=app,
                adapter=adapter,
                selected=selected_mcps,
                change=change,
                merged_config=outcome.config,
            )
        )

    return plans, errors


_CLAUDE_CODE_SETTINGS = Path.home() / ".claude" / "settings.json"
_CLAUDE_CODE_ENV_VAR = "TOOLGATE_CLAUDE_CODE_CONFIG"


def _claude_code_settings_path(override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    env = os.environ.get(_CLAUDE_CODE_ENV_VAR)
    if env:
        return Path(env).expanduser().resolve()
    return _CLAUDE_CODE_SETTINGS


def _is_toolgate_proxy_entry(server_id: str, entry: dict[str, Any]) -> bool:
    if server_id != "toolgate":
        return False
    command = str(entry.get("command", ""))
    args = entry.get("args", [])
    if not isinstance(args, list):
        return False
    return "toolgate" in command and "proxy" in args


def _run_install(args: argparse.Namespace) -> int:
    import shutil as _shutil

    from .proxy.config import DEFAULT_CONFIG_PATH, ProxyConfig, ServerEntry
    from .setup.hosts import utc_timestamp_compact

    settings_path = _claude_code_settings_path(getattr(args, "claude_config", None))
    proxy_config_path = Path(args.proxy_config).expanduser().resolve() if args.proxy_config else DEFAULT_CONFIG_PATH
    dry_run: bool = args.dry_run

    # Load existing Claude Code settings
    if settings_path.exists():
        try:
            host_config: dict = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"Failed to read {settings_path}: {exc}", file=sys.stderr)
            return 1
    else:
        host_config = {}

    existing_mcps: dict = host_config.get("mcpServers", {})

    # Resolve toolgate binary (absolute path, required since Claude Code may not share PATH)
    toolgate_bin = _shutil.which("toolgate") or str(Path(sys.executable).parent / "toolgate")

    # Build ProxyConfig from existing non-toolgate-proxy mcpServers (if any)
    filtered_mcps: dict[str, Any] = {
        sid: entry
        for sid, entry in existing_mcps.items()
        if isinstance(entry, dict) and not _is_toolgate_proxy_entry(sid, entry)
    }

    if filtered_mcps:
        servers: dict[str, ServerEntry] = {}
        for sid, entry in filtered_mcps.items():
            if not isinstance(entry, dict):
                continue
            cmd_str = entry.get("command", "")
            args_list = entry.get("args", [])
            command = [cmd_str, *args_list] if cmd_str else []
            if command:
                servers[sid] = ServerEntry(
                    command=command,
                    cwd=entry.get("cwd") if isinstance(entry.get("cwd"), str) else None,
                    env=entry.get("env") if isinstance(entry.get("env"), dict) else None,
                )
        proxy_config = ProxyConfig(servers=servers, original_mcp_servers=dict(filtered_mcps))
    elif proxy_config_path.exists():
        # No upstream mcpServers in settings, but a proxy config already exists — use it
        try:
            proxy_config = ProxyConfig.load(proxy_config_path)
        except Exception as exc:
            print(f"Failed to load existing proxy config {proxy_config_path}: {exc}", file=sys.stderr)
            return 1
        # Preserve original_mcp_servers if already set
    else:
        print("No upstream mcpServers in Claude Code settings and no proxy config found.", file=sys.stderr)
        print(f"Add servers to {proxy_config_path} first, then re-run install.", file=sys.stderr)
        return 1

    # Build new settings.json with single toolgate entry
    new_mcp_servers: dict = {
        "toolgate": {
            "command": toolgate_bin,
            "args": ["proxy", "--config", str(proxy_config_path)],
        }
    }
    new_config = {**host_config, "mcpServers": new_mcp_servers}

    # Preview
    print(f"Claude Code settings: {settings_path}")
    print(f"Proxy config:         {proxy_config_path}")
    print(f"toolgate binary:     {toolgate_bin}")
    print(f"\nUpstream servers to proxy ({len(proxy_config.servers)}):")
    for sid, entry in proxy_config.servers.items():
        print(f"  {sid}: {' '.join(entry.command)}")
    if not proxy_config.servers:
        print("  (none — populate proxy config before use)")
    print(f"\nmcpServers will become:")
    print(f"  toolgate → {toolgate_bin} proxy --config {proxy_config_path}")

    if existing_mcps:
        print(f"\nOriginal mcpServers ({len(existing_mcps)} entries) will be backed up to proxy config.")

    if dry_run:
        print("\n[dry-run] No files written.")
        return 0

    timestamp = utc_timestamp_compact()

    # 1. Save ProxyConfig
    proxy_config.save(proxy_config_path)
    print(f"\nSaved proxy config → {proxy_config_path}")

    # 2. Backup and rewrite settings.json
    if settings_path.exists():
        backup = settings_path.with_name(f"{settings_path.name}.bak.{timestamp}")
        _shutil.copy2(settings_path, backup)
        print(f"Backup             → {backup}")

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = settings_path.with_name(f"{settings_path.name}.tmp")
    tmp.write_text(json.dumps(new_config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, settings_path)
    print(f"Updated settings   → {settings_path}")
    print("\nDone. Restart Claude Code to apply.")
    return 0


def _run_uninstall(args: argparse.Namespace) -> int:
    import shutil as _shutil

    from .proxy.config import DEFAULT_CONFIG_PATH, ProxyConfig
    from .setup.hosts import utc_timestamp_compact

    settings_path = _claude_code_settings_path(getattr(args, "claude_config", None))
    proxy_config_path = Path(args.proxy_config).expanduser().resolve() if args.proxy_config else DEFAULT_CONFIG_PATH
    dry_run: bool = args.dry_run

    if not proxy_config_path.exists():
        print(f"No proxy config found at {proxy_config_path}. Nothing to uninstall.", file=sys.stderr)
        return 1

    try:
        proxy_config = ProxyConfig.load(proxy_config_path)
    except Exception as exc:
        print(f"Failed to load proxy config: {exc}", file=sys.stderr)
        return 1

    if not settings_path.exists():
        print(f"Claude Code settings not found at {settings_path}.", file=sys.stderr)
        return 1

    try:
        host_config: dict = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Failed to read {settings_path}: {exc}", file=sys.stderr)
        return 1

    original = proxy_config.original_mcp_servers
    new_config = {**host_config, "mcpServers": original}
    if not original:
        new_config.pop("mcpServers", None)

    print(f"Claude Code settings: {settings_path}")
    print(f"Restoring mcpServers ({len(original)} entries):")
    for sid in original:
        print(f"  {sid}")
    if not original:
        print("  (none — mcpServers key will be removed)")

    if dry_run:
        print("\n[dry-run] No files written.")
        return 0

    timestamp = utc_timestamp_compact()
    backup = settings_path.with_name(f"{settings_path.name}.bak.{timestamp}")
    _shutil.copy2(settings_path, backup)
    print(f"\nBackup → {backup}")

    tmp = settings_path.with_name(f"{settings_path.name}.tmp")
    tmp.write_text(json.dumps(new_config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, settings_path)
    print(f"Restored → {settings_path}")
    print("\nDone. Restart Claude Code to apply.")
    return 0


def _run_proxy(args: argparse.Namespace) -> int:
    from .proxy.config import ProxyConfig
    from .proxy.server import ProxyServer

    config_path = _proxy_config_path(args.config)

    if not config_path.exists():
        print(f"Config not found: {config_path}", file=sys.stderr)
        print("Create one manually or run 'toolgate install --dry-run' to preview setup.", file=sys.stderr)
        return 1

    try:
        config = ProxyConfig.load(config_path)
    except Exception as exc:
        print(f"Failed to load config {config_path}: {exc}", file=sys.stderr)
        return 1

    if config.is_empty():
        print("No servers configured in proxy config.", file=sys.stderr)
        return 1

    try:
        profile = _load_profile_arg(getattr(args, "profile", None), getattr(args, "profiles_dir", None))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    asyncio.run(ProxyServer(config, profile=profile).run())
    return 0


def _run_collect(args: argparse.Namespace) -> int:
    from .catalog import Catalog
    from .collector import collect_inventory
    from .proxy.config import ProxyConfig

    config_path = _proxy_config_path(args.config)
    catalog_path = _catalog_path(args.catalog)
    if not config_path.exists():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 1
    try:
        config = ProxyConfig.load(config_path)
        report = asyncio.run(collect_inventory(config, Catalog(catalog_path)))
    except Exception as exc:  # noqa: BLE001
        print(f"Collection failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    payload = {
        "catalog": str(catalog_path),
        "ok_count": report.ok_count,
        "failed_count": report.failed_count,
        "results": [
            {
                "server_id": result.server_id,
                "status": result.status,
                "tool_count": result.tool_count,
                "error": result.error,
            }
            for result in report.results
        ],
    }
    if args.as_json:
        _print_json(payload)
    else:
        print(f"Catalog: {catalog_path}")
        for result in report.results:
            suffix = f" ({result.error})" if result.error else ""
            print(f"{result.server_id}: {result.status}, {result.tool_count} tools{suffix}")
    return 1 if report.failed_count else 0


def _run_daemon(args: argparse.Namespace) -> int:
    from .daemon import DaemonAddress, ToolDaemon
    from .proxy.config import ProxyConfig

    config_path = _proxy_config_path(args.config)
    if not config_path.exists():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 1
    try:
        config = ProxyConfig.load(config_path)
        profile = _load_profile_arg(args.profile, args.profiles_dir)
        asyncio.run(ToolDaemon(config, profile=profile).run(DaemonAddress(args.host, args.port)))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Daemon failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


def _run_profiles(args: argparse.Namespace) -> int:
    from .profiles import list_profiles

    try:
        profiles = list_profiles(_profiles_dir(args.profiles_dir))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    payload = [
        {"id": profile.id, "description": profile.description}
        for profile in profiles
    ]
    if args.as_json:
        _print_json(payload)
    else:
        for item in payload:
            desc = f" - {item['description']}" if item["description"] else ""
            print(f"{item['id']}{desc}")
    return 0


def _format_tools(tools: list[dict[str, Any]], fmt: str) -> None:
    if fmt == "json":
        _print_json(tools)
        return
    for tool in tools:
        tag = "[params]" if tool.get("requires_params") else "[no params]"
        print(f"{tool['tool_id']}: {tool['description']} {tag}")


def _tools_from_catalog(args: argparse.Namespace) -> list[dict[str, Any]]:
    from .catalog import Catalog

    catalog = Catalog(_catalog_path(args.catalog))
    tools = catalog.search_tools(args.query) if getattr(args, "query", None) else catalog.list_tools()
    profile = _load_profile_arg(args.profile, args.profiles_dir)
    if profile:
        tools = profile.filter_catalog_tools(tools)
    return [
        {
            "tool_id": tool.tool_id,
            "server_id": tool.server_id,
            "description": profile.description_overrides.get(tool.tool_id, tool.description) if profile else tool.description,
            "requires_params": tool.requires_params,
        }
        for tool in tools
    ]


def _run_tools(args: argparse.Namespace) -> int:
    try:
        tools = _tools_from_catalog(args)
    except (OSError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _format_tools(tools, args.format)
    return 0


def _run_schema(args: argparse.Namespace) -> int:
    from .catalog import Catalog

    try:
        profile = _load_profile_arg(args.profile, args.profiles_dir)
        tool = Catalog(_catalog_path(args.catalog)).get_tool(args.tool_id)
        if profile and not profile.allows(tool.tool_id, tool.server_id):
            print(f"Tool not available in profile: {args.tool_id}", file=sys.stderr)
            return 1
        _print_json(tool.input_schema)
    except (OSError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _run_call(args: argparse.Namespace) -> int:
    from .daemon import DaemonAddress, request_daemon

    try:
        arguments = json.loads(args.json_args) if args.json_args else {}
    except json.JSONDecodeError as exc:
        print(f"Invalid --json: {exc}", file=sys.stderr)
        return 2
    if not isinstance(arguments, dict):
        print("--json must decode to an object", file=sys.stderr)
        return 2
    try:
        response = asyncio.run(
            request_daemon(
                {"action": "call", "tool_id": args.tool_id, "arguments": arguments},
                DaemonAddress(args.host, args.port),
            )
        )
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Daemon request failed: {exc}", file=sys.stderr)
        return 1
    if not response.get("ok"):
        print(response.get("error", "Unknown daemon error"), file=sys.stderr)
        return 1
    _print_json(response["result"])
    return 0


def _run_daemon_status(args: argparse.Namespace) -> int:
    from .daemon import DaemonAddress, request_daemon

    try:
        response = asyncio.run(request_daemon({"action": "status"}, DaemonAddress(args.host, args.port)))
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Daemon request failed: {exc}", file=sys.stderr)
        return 1
    _print_json(response)
    return 0


def _run_setup(args: argparse.Namespace) -> int:
    if platform.system() != "Darwin":
        print("toolgate setup currently supports macOS (Darwin) only.", file=sys.stderr)
        return 2

    if args.mode != "direct":
        print("Only --mode direct is implemented in v1.", file=sys.stderr)
        return 2

    try:
        target_apps = _parse_app_ids(args.apps)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    project_root = Path(args.project_root).expanduser().resolve()
    home = Path(args.home).expanduser().resolve() if args.home else Path.home()
    global_root = Path(args.global_root).expanduser().resolve() if args.global_root else None

    discovered_mcps = discover_installed_mcps(project_root, global_root=global_root)
    discovered = discover_apps(home, target_apps)
    discovered_apps = [app for app, _ in discovered]

    if not discovered_apps:
        print("No supported host apps discovered (Claude/Cursor).", file=sys.stderr)
        return 1

    non_interactive = bool(args.non_interactive or not sys.stdin.isatty())
    select_ids = _parse_csv(args.select)

    try:
        selection = _select_mcps_for_apps(
            mcps=discovered_mcps,
            app_ids=[app.id for app in discovered_apps],
            non_interactive=non_interactive,
            select_ids=select_ids,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    plans, plan_errors = _build_plans(
        discovered=discovered,
        selection=selection,
        mcps=discovered_mcps,
    )
    text_output = not args.as_json
    if text_output:
        _render_preview(discovered_apps, discovered_mcps, plans)

    if plan_errors and text_output:
        print("\nPlanning errors:")
        for error in plan_errors:
            print(f"  - {error.app_id}: {error.error}")

    apply_results: list[ApplyResult] | None = None
    if args.apply:
        timestamp = utc_timestamp_compact()
        apply_results = list(plan_errors)
        for plan in plans:
            has_changes = bool(
                plan.change.added_servers
                or plan.change.updated_servers
                or plan.change.removed_servers
            )
            if not has_changes:
                apply_results.append(
                    ApplyResult(
                        app_id=plan.app.id,
                        config_path=plan.app.config_path,
                        status="unchanged",
                    )
                )
                continue

            try:
                backup = plan.adapter.write(
                    plan.app.config_path,
                    plan.merged_config,
                    timestamp=timestamp,
                )
                plan.change.backup_path = backup
                apply_results.append(
                    ApplyResult(
                        app_id=plan.app.id,
                        config_path=plan.app.config_path,
                        status="applied",
                        backup_path=backup,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                apply_results.append(
                    ApplyResult(
                        app_id=plan.app.id,
                        config_path=plan.app.config_path,
                        status="failed",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )

        if text_output:
            print("\nApply summary:")
            for result in apply_results:
                line = f"  - {result.app_id}: {result.status}"
                if result.backup_path:
                    line += f" (backup: {result.backup_path})"
                if result.error:
                    line += f" ({result.error})"
                print(line)

    elif text_output:
        print("\nPreview only. Re-run with --apply to write changes.")

    if args.as_json:
        payload = _build_json_payload(
            mode=args.mode,
            apply=bool(args.apply),
            discovered_apps=discovered_apps,
            mcps=discovered_mcps,
            plans=plans,
            plan_errors=plan_errors,
            apply_results=apply_results,
        )
        print(json.dumps(payload, indent=2, sort_keys=True))

    if apply_results is None:
        return 1 if plan_errors else 0
    return 1 if any(result.status == "failed" for result in apply_results) else 0


def _add_install_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--claude-config", default=None, help=f"Override path to Claude Code settings.json (env: {_CLAUDE_CODE_ENV_VAR})")
    p.add_argument("--proxy-config", default=None, help="Path to toolgate proxy config (default: ~/.toolgate/config.json)")
    p.add_argument("--dry-run", action="store_true", help="Preview changes without writing")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="toolgate")
    sub = parser.add_subparsers(dest="subcommand")

    proxy = sub.add_parser(
        "proxy",
        help="Run the toolgate proxy server (MCP server-side, reads JSON-RPC from stdin)",
    )
    proxy.add_argument(
        "--config",
        default=None,
        help="Path to proxy config JSON (default: ~/.toolgate/config.json)",
    )
    proxy.add_argument("--profile", default=None, help="Profile id to expose")
    proxy.add_argument("--profiles-dir", default=None, help="Directory containing profile JSON files")

    collect = sub.add_parser(
        "collect",
        help="Probe configured MCP servers and write the SQLite inventory catalog",
    )
    collect.add_argument("--config", default=None, help="Path to proxy config JSON")
    collect.add_argument("--catalog", default=None, help="Path to inventory catalog DB")
    collect.add_argument("--json", dest="as_json", action="store_true", help="Emit JSON report")

    daemon = sub.add_parser(
        "daemon",
        help="Run the local toolgate JSON-line daemon for CLI tool calls",
    )
    daemon_sub = daemon.add_subparsers(dest="daemon_command")
    daemon_run = daemon_sub.add_parser("run", help="Start the daemon")
    daemon_run.add_argument("--config", default=None, help="Path to proxy config JSON")
    daemon_run.add_argument("--profile", default=None, help="Profile id to enforce")
    daemon_run.add_argument("--profiles-dir", default=None, help="Directory containing profile JSON files")
    daemon_run.add_argument("--host", default="127.0.0.1", help="Bind host")
    daemon_run.add_argument("--port", default=8765, type=int, help="Bind port")
    daemon_status = daemon_sub.add_parser("status", help="Query daemon status")
    daemon_status.add_argument("--host", default="127.0.0.1", help="Daemon host")
    daemon_status.add_argument("--port", default=8765, type=int, help="Daemon port")

    tools = sub.add_parser(
        "tools",
        help="List or search collected tools from the SQLite catalog",
    )
    tools_sub = tools.add_subparsers(dest="tools_command")
    tools_list = tools_sub.add_parser("list", help="List collected tools")
    tools_list.add_argument("--catalog", default=None, help="Path to inventory catalog DB")
    tools_list.add_argument("--profile", default=None, help="Profile id to filter")
    tools_list.add_argument("--profiles-dir", default=None, help="Directory containing profile JSON files")
    tools_list.add_argument("--format", choices=["text", "json"], default="json")
    tools_search = tools_sub.add_parser("search", help="Search collected tools")
    tools_search.add_argument("query")
    tools_search.add_argument("--catalog", default=None, help="Path to inventory catalog DB")
    tools_search.add_argument("--profile", default=None, help="Profile id to filter")
    tools_search.add_argument("--profiles-dir", default=None, help="Directory containing profile JSON files")
    tools_search.add_argument("--format", choices=["text", "json"], default="json")

    schema = sub.add_parser("schema", help="Print a collected tool input schema")
    schema.add_argument("tool_id")
    schema.add_argument("--catalog", default=None, help="Path to inventory catalog DB")
    schema.add_argument("--profile", default=None, help="Profile id to enforce")
    schema.add_argument("--profiles-dir", default=None, help="Directory containing profile JSON files")

    call = sub.add_parser("call", help="Call a tool through a running toolgate daemon")
    call.add_argument("tool_id")
    call.add_argument("--json", dest="json_args", default=None, help="JSON object arguments")
    call.add_argument("--host", default="127.0.0.1", help="Daemon host")
    call.add_argument("--port", default=8765, type=int, help="Daemon port")

    profiles = sub.add_parser("profiles", help="List profile files")
    profiles.add_argument("--profiles-dir", default=None, help="Directory containing profile JSON files")
    profiles.add_argument("--json", dest="as_json", action="store_true", help="Emit JSON")

    _add_install_args(sub.add_parser(
        "install",
        help="Install toolgate proxy into Claude Code settings.json",
    ))
    _add_install_args(sub.add_parser(
        "uninstall",
        help="Remove toolgate proxy and restore original mcpServers",
    ))

    setup = sub.add_parser(
        "setup",
        help="Discover MCP servers and configure Claude/Cursor host configs",
    )
    setup.add_argument("--apps", default=None, help="Comma-separated apps: claude,cursor")
    setup.add_argument("--apply", action="store_true", help="Apply planned config writes")
    setup.add_argument("--json", dest="as_json", action="store_true", help="Emit machine-readable JSON preview")
    setup.add_argument("--non-interactive", action="store_true", help="Disable prompts; select all by default")
    setup.add_argument("--select", default=None, help="Comma-separated MCP server IDs to select")
    setup.add_argument(
        "--mode",
        default="direct",
        choices=["direct", "proxy"],
        help="Integration mode (proxy reserved for future; direct only in v1)",
    )
    setup.add_argument(
        "--project-root",
        default=".",
        help="Project root used for local node_modules discovery",
    )
    setup.add_argument(
        "--home",
        default=None,
        help=argparse.SUPPRESS,
    )
    setup.add_argument(
        "--global-root",
        default=None,
        help=argparse.SUPPRESS,
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "proxy":
        return _run_proxy(args)

    if args.subcommand == "collect":
        return _run_collect(args)

    if args.subcommand == "daemon":
        if args.daemon_command == "run":
            return _run_daemon(args)
        if args.daemon_command == "status":
            return _run_daemon_status(args)
        parser.print_help()
        return 1

    if args.subcommand == "tools":
        if args.tools_command in {"list", "search"}:
            return _run_tools(args)
        parser.print_help()
        return 1

    if args.subcommand == "schema":
        return _run_schema(args)

    if args.subcommand == "call":
        return _run_call(args)

    if args.subcommand == "profiles":
        return _run_profiles(args)

    if args.subcommand == "install":
        return _run_install(args)

    if args.subcommand == "uninstall":
        return _run_uninstall(args)

    if args.subcommand == "setup":
        return _run_setup(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
