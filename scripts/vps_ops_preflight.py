#!/usr/bin/env python3
"""Read-only Hermes VPS operations preflight.

Aggregates update readiness, reboot readiness, and rollback inventory before a
manual reboot or Hermes update window. It never updates, reboots, or restores.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    configured = os.environ.get("HERMES_UPDATECHECK_PROJECT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "hermes_cli").is_dir() and (parent / "scripts").is_dir():
            return parent
    return (Path.home() / ".hermes" / "hermes-agent").resolve()


def _load_script_module(name: str):
    import importlib.util

    script_path = Path(__file__).resolve().with_name(f"{name}.py")
    if not script_path.exists():
        script_path = _repo_root() / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _updatecheck(fresh: bool, timeout: float) -> dict[str, Any]:
    repo = _repo_root()
    sys.path.insert(0, str(repo))
    from hermes_cli.updatecheck import collect_updatecheck  # pylint: disable=import-outside-toplevel
    from hermes_constants import get_hermes_home  # pylint: disable=import-outside-toplevel

    return collect_updatecheck(
        project_root=repo,
        hermes_home=get_hermes_home(),
        fresh=fresh,
        fetch_timeout=timeout,
    )


def _updatecheck_blocking_issue(report: dict[str, Any]) -> bool:
    issues = [str(item) for item in report.get("issues") or []]
    allowed = {"working tree has local changes/untracked files"}
    return any(issue not in allowed for issue in issues)


def collect_preflight(*, fresh: bool = False, timeout: float = 20, rollback_limit: int = 8) -> dict[str, Any]:
    reboot_mod = _load_script_module("vps_reboot_readiness")
    rollback_mod = _load_script_module("vps_rollback_inventory")
    roots_mod = _load_script_module("vps_write_roots_audit")

    update = _updatecheck(fresh=fresh, timeout=timeout)
    reboot = reboot_mod.collect_readiness()
    rollback = rollback_mod.collect_inventory(limit=rollback_limit)
    roots = roots_mod.collect_write_roots()

    issues: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []

    if _updatecheck_blocking_issue(update):
        issues.append("updatecheck has blocking issues beyond the expected dirty worktree")
    elif update.get("status") == "red":
        warnings.append("updatecheck is RED because the live worktree is dirty")
    elif update.get("status") == "yellow":
        warnings.append("updatecheck is YELLOW")
    else:
        ok.append("updatecheck has no blocking issues")

    if reboot.get("status") == "block":
        issues.append("reboot readiness is BLOCK")
    elif reboot.get("status") == "warn":
        warnings.append("reboot readiness is WARN")
    else:
        ok.append("reboot readiness is READY")

    useful_snapshots = [
        item for item in rollback.get("snapshots") or []
        if int(item.get("restorable_count") or 0) > 0
    ]
    if useful_snapshots:
        ok.append(f"rollback inventory has {len(useful_snapshots)} useful recent bundle(s)")
    else:
        issues.append("rollback inventory found no useful recent bundles")

    if roots.get("status") == "block":
        issues.append("write roots audit is BLOCK")
    elif roots.get("status") == "warn":
        warnings.append("write roots audit is WARN")
    else:
        ok.append("write roots audit is READY")

    status = "block" if issues else ("warn" if warnings else "ready")
    return {
        "schema": 1,
        "status": status,
        "issues": issues,
        "warnings": warnings,
        "ok": ok,
        "updatecheck": {
            "status": update.get("status"),
            "version": update.get("version"),
            "update_available": update.get("update_available"),
            "issues": update.get("issues") or [],
            "warnings": update.get("warnings") or [],
        },
        "reboot": {
            "status": reboot.get("status"),
            "reboot_required": reboot.get("reboot_required"),
            "ports": reboot.get("ports"),
            "disk": reboot.get("disk"),
        },
        "rollback": {
            "snapshot_count": rollback.get("snapshot_count"),
            "useful_recent": useful_snapshots[:rollback_limit],
        },
        "write_roots": {
            "status": roots.get("status"),
            "recommended_write_roots": roots.get("recommended_write_roots"),
            "policy": roots.get("write_root_policy") or {},
            "warnings": roots.get("warnings") or [],
            "workspace_count": (
                (roots.get("roots") or {})
                .get("repo_workspaces", {})
                .get("workspace_count")
            ),
        },
    }


def format_preflight(report: dict[str, Any]) -> str:
    status = str(report.get("status", "unknown")).upper()
    lines = [f"Hermes VPS ops preflight: {status}"]
    update = report.get("updatecheck") or {}
    lines.append(
        "Updatecheck: "
        f"{str(update.get('status', '?')).upper()}, "
        f"update_available={update.get('update_available')}"
    )
    reboot = report.get("reboot") or {}
    ports = reboot.get("ports") or {}
    disk = reboot.get("disk") or {}
    lines.append(
        "Reboot: "
        f"{str(reboot.get('status', '?')).upper()}, "
        f"ports={ports}, disk_free={disk.get('free_gb', '?')}GB"
    )
    rollback = report.get("rollback") or {}
    lines.append(
        "Rollback: "
        f"{rollback.get('snapshot_count', 0)} snapshots, "
        f"{len(rollback.get('useful_recent') or [])} useful recent"
    )
    roots = report.get("write_roots") or {}
    lines.append(
        "Write roots: "
        f"{str(roots.get('status', '?')).upper()}, "
        f"workspaces={roots.get('workspace_count', '?')}, "
        f"policy={'set' if (roots.get('policy') or {}).get('configured_roots') else 'unset'}"
    )
    for title, key in (("Issues", "issues"), ("Warnings", "warnings"), ("OK", "ok")):
        values = report.get(key) or []
        if not values:
            continue
        lines.append("")
        lines.append(f"{title}:")
        lines.extend(f"- {item}" for item in values)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true", help="Fetch origin/main for updatecheck.")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--rollback-limit", type=int, default=8)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = collect_preflight(
        fresh=args.fresh,
        timeout=args.timeout,
        rollback_limit=max(1, args.rollback_limit),
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_preflight(report))
    return 0 if report["status"] in {"ready", "warn"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
