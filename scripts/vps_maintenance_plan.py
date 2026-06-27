#!/usr/bin/env python3
"""Read-only maintenance plan generator for the Hermes VPS.

This script prints exact commands for an approved maintenance window. It never
writes files, reloads systemd, restarts services, reboots, or updates Hermes.
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_USER = "hermes"
DEFAULT_GATEWAY_UNIT = "hermes-gateway.service"
DEFAULT_REPO_UNIT = "hermes-repo-cockpit.service"
DEFAULT_SAFE_ROOTS = (
    "/home/hermes/.hermes",
    "/home/hermes/repo-cockpit",
)


def _run(argv: list[str], *, timeout: float = 5) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            argv,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "timed_out": False,
        }
    except Exception as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc), "timed_out": False}


def _home_for_user(user: str) -> Path:
    try:
        return Path(pwd.getpwnam(user).pw_dir)
    except Exception:
        return Path(f"/home/{user}")


def _uid_for_user(user: str) -> str:
    try:
        return str(pwd.getpwnam(user).pw_uid)
    except Exception:
        result = _run(["id", "-u", user])
        return str(result["stdout"]).strip() or "$(id -u hermes)"


def _gateway_override_path(user: str, unit: str) -> Path:
    return _home_for_user(user) / ".config" / "systemd" / "user" / f"{unit}.d" / "10-write-safe-roots.conf"


def _override_content(safe_roots: tuple[str, ...]) -> str:
    return (
        "[Service]\n"
        "Environment=HERMES_WRITE_SAFE_ROOTS=" + ":".join(safe_roots) + "\n"
    )


def _current_override(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if path.exists():
        try:
            out["content"] = path.read_text()
        except Exception as exc:
            out["error"] = str(exc)
    return out


def collect_plan(
    *,
    user: str = DEFAULT_USER,
    gateway_unit: str = DEFAULT_GATEWAY_UNIT,
    repo_unit: str = DEFAULT_REPO_UNIT,
    safe_roots: tuple[str, ...] = DEFAULT_SAFE_ROOTS,
) -> dict[str, Any]:
    override_path = _gateway_override_path(user, gateway_unit)
    expected_content = _override_content(safe_roots)
    current = _current_override(override_path)
    already_applied = current.get("content") == expected_content
    uid = _uid_for_user(user)
    xdg = f"XDG_RUNTIME_DIR=/run/user/{uid}"
    roots_value = ":".join(safe_roots)

    apply_commands = [
        "sudo -u hermes /home/hermes/.hermes/scripts/vps_ops_preflight.py",
        f"sudo -u {user} mkdir -p {override_path.parent}",
        (
            f"sudo -u {user} tee {override_path} >/dev/null <<'EOF'\n"
            f"{expected_content}"
            "EOF"
        ),
        f"sudo -u {user} {xdg} systemctl --user daemon-reload",
        f"sudo -u {user} {xdg} systemctl --user restart {gateway_unit}",
    ]
    postcheck_commands = [
        f"sudo -u {user} env HERMES_WRITE_SAFE_ROOTS={roots_value} /home/hermes/.hermes/scripts/vps_write_roots_audit.py",
        f"sudo -u {user} {xdg} systemctl --user is-active {gateway_unit} {repo_unit}",
        "cd /home/hermes/.hermes/hermes-agent && sudo -u hermes venv/bin/python -m hermes_cli.main updatecheck --cached",
        "python3 /Users/matthis/.hermes/hermes-agent/scripts/telegram_desktop_cua_smoke.py --message 'smoke maintenance: reponds juste OK maintenance'",
    ]
    rollback_commands = [
        f"sudo -u {user} rm -f {override_path}",
        f"sudo -u {user} {xdg} systemctl --user daemon-reload",
        f"sudo -u {user} {xdg} systemctl --user restart {gateway_unit}",
        f"sudo -u {user} {xdg} systemctl --user is-active {gateway_unit}",
    ]

    return {
        "schema": 1,
        "status": "already_applied" if already_applied else "ready_to_apply",
        "user": user,
        "gateway_unit": gateway_unit,
        "repo_unit": repo_unit,
        "safe_roots": list(safe_roots),
        "override": current,
        "expected_override_content": expected_content,
        "apply_commands": apply_commands,
        "postcheck_commands": postcheck_commands,
        "rollback_commands": rollback_commands,
        "warnings": [
            "read-only plan only; do not run apply commands without an approved maintenance window",
            "gateway restart is required before the running bot sees HERMES_WRITE_SAFE_ROOTS",
        ],
    }


def format_plan(plan: dict[str, Any]) -> str:
    lines = [
        f"Hermes VPS maintenance plan: {str(plan.get('status')).upper()}",
        f"Gateway unit: {plan.get('gateway_unit')}",
        f"Override: {(plan.get('override') or {}).get('path')}",
        "Safe roots: " + ", ".join(str(item) for item in plan.get("safe_roots") or []),
        "",
        "Expected override content:",
        str(plan.get("expected_override_content") or "").rstrip(),
        "",
        "Apply commands:",
    ]
    lines.extend(f"{idx}. {cmd}" for idx, cmd in enumerate(plan.get("apply_commands") or [], start=1))
    lines.append("")
    lines.append("Post-check commands:")
    lines.extend(f"{idx}. {cmd}" for idx, cmd in enumerate(plan.get("postcheck_commands") or [], start=1))
    lines.append("")
    lines.append("Rollback commands:")
    lines.extend(f"{idx}. {cmd}" for idx, cmd in enumerate(plan.get("rollback_commands") or [], start=1))
    warnings = plan.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {item}" for item in warnings)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    plan = collect_plan()
    if args.json:
        print(json.dumps(plan, indent=2, sort_keys=True))
    else:
        print(format_plan(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
