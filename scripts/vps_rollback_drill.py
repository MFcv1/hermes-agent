#!/usr/bin/env python3
"""Read-only rollback drill for Hermes VPS ops snapshots.

The drill chooses a recent, known restorable file and prints the exact manual
rollback commands. It never stops services, copies files, or mutates state.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path("/home/hermes/ops-snapshots")

KNOWN_TARGETS = {
    "file_safety.py.before": {
        "target": "/home/hermes/.hermes/hermes-agent/agent/file_safety.py",
        "service": "hermes-gateway.service",
        "reason": "Hermes file-safety code",
    },
    "updatecheck.py.before": {
        "target": "/home/hermes/.hermes/hermes-agent/hermes_cli/updatecheck.py",
        "service": "hermes-gateway.service",
        "reason": "Hermes updatecheck command implementation",
    },
    "vps_ops_preflight.py.before": {
        "target": "/home/hermes/.hermes/scripts/vps_ops_preflight.py",
        "service": None,
        "reason": "operator preflight helper script",
    },
    "vps_write_roots_audit.py.before": {
        "target": "/home/hermes/.hermes/scripts/vps_write_roots_audit.py",
        "service": None,
        "reason": "operator write-root audit helper script",
    },
    "vps_maintenance_plan.py.before": {
        "target": "/home/hermes/.hermes/scripts/vps_maintenance_plan.py",
        "service": None,
        "reason": "operator maintenance plan helper script",
    },
    "blueprint_catalog.py.before": {
        "target": "/home/hermes/.hermes/hermes-agent/cron/blueprint_catalog.py",
        "service": "hermes-gateway.service",
        "reason": "automation blueprint catalog",
    },
    "gateway_run.py.before": {
        "target": "/home/hermes/.hermes/hermes-agent/gateway/run.py",
        "service": "hermes-gateway.service",
        "reason": "gateway runtime",
    },
    "gateway_slash_commands.py.before": {
        "target": "/home/hermes/.hermes/hermes-agent/gateway/slash_commands.py",
        "service": "hermes-gateway.service",
        "reason": "gateway slash command dispatch",
    },
}


def _snapshot_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)


def _candidate_files(root: Path, *, limit: int) -> list[Path]:
    candidates: list[Path] = []
    for snapshot in _snapshot_dirs(root)[:limit]:
        for path in sorted(snapshot.rglob("*.before")):
            if path.name in KNOWN_TARGETS:
                candidates.append(path)
    return candidates


def _select_candidate(root: Path, requested: str | None, *, limit: int) -> Path | None:
    candidates = _candidate_files(root, limit=limit)
    if requested:
        requested_path = Path(requested)
        if requested_path.is_absolute() and requested_path.exists():
            return requested_path if requested_path.name in KNOWN_TARGETS else None
        for candidate in candidates:
            if requested in str(candidate):
                return candidate
        return None
    return candidates[0] if candidates else None


def collect_drill(
    *,
    root: Path = DEFAULT_ROOT,
    snapshot_or_file: str | None = None,
    limit: int = 12,
) -> dict[str, Any]:
    candidate = _select_candidate(root, snapshot_or_file, limit=limit)
    if candidate is None:
        return {
            "schema": 1,
            "status": "no_known_candidate",
            "root": str(root),
            "requested": snapshot_or_file,
            "issues": ["no known .before file could be mapped to a live target"],
            "warnings": [],
            "commands": [],
        }

    meta = KNOWN_TARGETS[candidate.name]
    target = str(meta["target"])
    service = meta.get("service")
    verify_commands = [
        f"test -f {candidate}",
        f"test -e {target}",
        f"diff -u {candidate} {target} | sed -n '1,120p'",
    ]
    commands: list[str] = []
    if service:
        commands.append(f"sudo -u hermes XDG_RUNTIME_DIR=/run/user/1000 systemctl --user stop {service}")
    commands.extend(
        [
            f"cp {target} {target}.rollback-candidate.$(date -u +%Y%m%dT%H%M%SZ)",
            f"cp {candidate} {target}",
            f"chown hermes:hermes {target}",
        ]
    )
    if str(target).endswith(".py"):
        commands.append(f"cd /home/hermes/.hermes/hermes-agent && sudo -u hermes venv/bin/python -m py_compile {target}")
    if service:
        commands.append(f"sudo -u hermes XDG_RUNTIME_DIR=/run/user/1000 systemctl --user start {service}")
        commands.append(f"sudo -u hermes XDG_RUNTIME_DIR=/run/user/1000 systemctl --user is-active {service}")

    return {
        "schema": 1,
        "status": "ready",
        "root": str(root),
        "snapshot_file": str(candidate),
        "target": target,
        "service": service,
        "reason": meta["reason"],
        "verify_commands": verify_commands,
        "commands": commands,
        "warnings": [
            "read-only drill only; do not run rollback commands unless a matching failure is confirmed",
            "restore one mapped file at a time; Never bulk-restore a snapshot directory",
        ],
        "issues": [],
    }


def format_drill(report: dict[str, Any]) -> str:
    lines = [f"Hermes VPS rollback drill: {str(report.get('status')).upper()}"]
    if report.get("status") != "ready":
        for item in report.get("issues") or []:
            lines.append(f"- {item}")
        return "\n".join(lines)
    lines.extend(
        [
            f"Snapshot file: {report.get('snapshot_file')}",
            f"Target: {report.get('target')}",
            f"Service: {report.get('service') or 'none'}",
            f"Reason: {report.get('reason')}",
            "",
            "Verify before rollback:",
        ]
    )
    lines.extend(f"{idx}. {cmd}" for idx, cmd in enumerate(report.get("verify_commands") or [], start=1))
    lines.append("")
    lines.append("Rollback commands:")
    lines.extend(f"{idx}. {cmd}" for idx, cmd in enumerate(report.get("commands") or [], start=1))
    warnings = report.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {item}" for item in warnings)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--file", help="Snapshot file or substring to drill.")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = collect_drill(root=args.root, snapshot_or_file=args.file, limit=max(1, args.limit))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_drill(report))
    return 0 if report.get("status") == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
