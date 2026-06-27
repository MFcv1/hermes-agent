#!/usr/bin/env python3
"""Read-only smoke matrix for Hermes VPS maintenance/update gates.

The script aggregates local VPS checks and prints the Telegram Desktop CUA
commands that must be run from the operator Mac. It does not send Telegram
messages, restart services, reboot, update, or restore files.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any


TELEGRAM_CUA_COMMANDS = [
    {
        "name": "normal-chat",
        "command": (
            "python3 /Users/matthis/.hermes/hermes-agent/scripts/telegram_desktop_cua_smoke.py "
            "--message 'smoke matrix normal chat: reponds juste OK smoke matrix'"
        ),
    },
    {
        "name": "version",
        "command": (
            "python3 /Users/matthis/.hermes/hermes-agent/scripts/telegram_desktop_cua_smoke.py "
            "--command /version"
        ),
    },
    {
        "name": "updatecheck",
        "command": (
            "python3 /Users/matthis/.hermes/hermes-agent/scripts/telegram_desktop_cua_smoke.py "
            "--command /updatecheck"
        ),
    },
    {
        "name": "conv-existing-repo",
        "command": (
            "python3 /Users/matthis/.hermes/hermes-agent/scripts/telegram_desktop_cua_smoke.py "
            "--command /conv"
        ),
        "manual_expectation": "Then click Nouveau chat -> Projet GitHub MFcv1 existant -> one native repo button.",
    },
]


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
    script_path = Path(__file__).resolve().with_name(f"{name}.py")
    if not script_path.exists():
        script_path = _repo_root() / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ok_status(status: str | None, accepted: set[str]) -> bool:
    return str(status or "").lower() in accepted


def collect_matrix(*, fresh: bool = False, timeout: float = 20) -> dict[str, Any]:
    sys.path.insert(0, str(_repo_root()))
    preflight_mod = _load_script_module("vps_ops_preflight")
    maintenance_mod = _load_script_module("vps_maintenance_plan")
    rollback_mod = _load_script_module("vps_rollback_drill")

    preflight = preflight_mod.collect_preflight(fresh=fresh, timeout=timeout)
    maintenance = maintenance_mod.collect_plan()
    rollback = rollback_mod.collect_drill()

    checks = [
        {
            "name": "ops-preflight",
            "status": preflight.get("status"),
            "ok": _ok_status(preflight.get("status"), {"ready", "warn"}),
            "detail": "WARN is acceptable on the dirty live VPS when issues are understood.",
        },
        {
            "name": "maintenance-plan",
            "status": maintenance.get("status"),
            "ok": _ok_status(maintenance.get("status"), {"ready_to_apply", "already_applied"}),
            "detail": "Plan only; do not run apply commands without approval.",
        },
        {
            "name": "rollback-drill",
            "status": rollback.get("status"),
            "ok": _ok_status(rollback.get("status"), {"ready"}),
            "detail": "Drill only; do not run rollback commands without a matching failure.",
        },
    ]
    status = "ready" if all(item["ok"] for item in checks) else "block"
    return {
        "schema": 1,
        "status": status,
        "checks": checks,
        "preflight": {
            "status": preflight.get("status"),
            "warnings": preflight.get("warnings") or [],
            "issues": preflight.get("issues") or [],
        },
        "maintenance": {
            "status": maintenance.get("status"),
            "override": maintenance.get("override"),
        },
        "rollback": {
            "status": rollback.get("status"),
            "snapshot_file": rollback.get("snapshot_file"),
            "target": rollback.get("target"),
            "service": rollback.get("service"),
        },
        "telegram_cua": {
            "status": "operator_required",
            "commands": TELEGRAM_CUA_COMMANDS,
            "evidence_dir": "/Users/matthis/.hermes/telegram-gui-smoke/",
        },
        "warnings": [
            "Telegram CUA smokes must be run from the operator Mac with Telegram Desktop visible.",
            "A screenshot_review_required result can be acceptable only after inspecting the saved screenshot.",
        ],
    }


def format_matrix(report: dict[str, Any]) -> str:
    lines = [f"Hermes VPS smoke matrix: {str(report.get('status')).upper()}"]
    for check in report.get("checks") or []:
        marker = "OK" if check.get("ok") else "BLOCK"
        lines.append(f"- {marker} {check.get('name')}: {str(check.get('status')).upper()}")
    preflight = report.get("preflight") or {}
    if preflight.get("warnings"):
        lines.append("")
        lines.append("Preflight warnings:")
        lines.extend(f"- {item}" for item in preflight.get("warnings") or [])
    if preflight.get("issues"):
        lines.append("")
        lines.append("Preflight issues:")
        lines.extend(f"- {item}" for item in preflight.get("issues") or [])

    telegram = report.get("telegram_cua") or {}
    lines.append("")
    lines.append("Telegram CUA commands to run from the operator Mac:")
    for item in telegram.get("commands") or []:
        lines.append(f"- {item.get('name')}: {item.get('command')}")
        if item.get("manual_expectation"):
            lines.append(f"  expectation: {item.get('manual_expectation')}")
    lines.append(f"Evidence dir: {telegram.get('evidence_dir')}")

    warnings = report.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {item}" for item in warnings)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true", help="Fetch origin/main through updatecheck.")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = collect_matrix(fresh=args.fresh, timeout=args.timeout)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_matrix(report))
    return 0 if report.get("status") == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
