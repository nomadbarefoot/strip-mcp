"""Command line entrypoint for strip-mcp."""

from __future__ import annotations

import argparse
import json
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
    print("=== strip-mcp setup preview ===")

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


def _run_setup(args: argparse.Namespace) -> int:
    if platform.system() != "Darwin":
        print("strip-mcp setup currently supports macOS (Darwin) only.", file=sys.stderr)
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
            apply_results=apply_results,
        )
        print(json.dumps(payload, indent=2, sort_keys=True))

    if apply_results is None:
        return 1 if plan_errors else 0
    return 1 if any(result.status == "failed" for result in apply_results) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="strip-mcp")
    sub = parser.add_subparsers(dest="subcommand")

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

    if args.subcommand == "setup":
        return _run_setup(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
