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
REQUIRED_HARDENING_DIRECTIVES = (
    "NoNewPrivileges=true",
    "PrivateTmp=true",
    "ProtectSystem=strict",
    "ProtectHome=read-only",
    "RestrictSUIDSGID=true",
    "LockPersonality=true",
    "ProtectKernelTunables=true",
    "ProtectKernelModules=true",
    "ProtectControlGroups=true",
    "MemoryMax=",
    "TasksMax=",
    "KillMode=control-group",
    "UMask=0077",
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


def _hardening_override_content(
    safe_roots: tuple[str, ...], *, memory_max: str = "2G"
) -> str:
    roots = " ".join(safe_roots)
    return (
        "[Service]\n"
        "NoNewPrivileges=true\n"
        "PrivateTmp=true\n"
        "ProtectSystem=strict\n"
        "ProtectHome=read-only\n"
        f"ReadWritePaths={roots}\n"
        "RestrictSUIDSGID=true\n"
        "LockPersonality=true\n"
        "ProtectKernelTunables=true\n"
        "ProtectKernelModules=true\n"
        "ProtectControlGroups=true\n"
        f"MemoryMax={memory_max}\n"
        "TasksMax=256\n"
        "KillMode=control-group\n"
        "UMask=0077\n"
    )


def _dashboard_unit_content(user: str) -> str:
    return (
        "[Unit]\n"
        "Description=Hermes dashboard\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"User={user}\n"
        f"WorkingDirectory=/home/{user}/.hermes/hermes-agent\n"
        f"ExecStart=/home/{user}/.hermes/hermes-agent/venv/bin/python "
        "-m hermes_cli.main dashboard --no-open --skip-build "
        "--host 127.0.0.1 --port 9119\n"
        "Restart=on-failure\n"
        "RestartSec=5s\n"
        "NoNewPrivileges=true\n"
        "PrivateTmp=true\n"
        "ProtectSystem=strict\n"
        "ProtectHome=read-only\n"
        f"ReadWritePaths=/home/{user}/.hermes\n"
        "MemoryMax=768M\n"
        "TasksMax=128\n"
        "KillMode=control-group\n"
        "UMask=0077\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def validate_generated_plan(
    *, hardening_content: str, dashboard_content: str
) -> dict[str, Any]:
    missing_hardening = [
        directive
        for directive in REQUIRED_HARDENING_DIRECTIVES
        if directive not in hardening_content
    ]
    dashboard_requirements = (
        "ExecStart=",
        "--host 127.0.0.1",
        "Restart=on-failure",
        "NoNewPrivileges=true",
        "MemoryMax=",
        "KillMode=control-group",
    )
    missing_dashboard = [
        directive for directive in dashboard_requirements if directive not in dashboard_content
    ]
    return {
        "ok": not missing_hardening and not missing_dashboard,
        "missing_hardening": missing_hardening,
        "missing_dashboard": missing_dashboard,
        "note": "static local validation; run systemd-analyze security on the Linux target before apply",
    }


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
    hardening_content = _hardening_override_content(safe_roots)
    dashboard_content = _dashboard_unit_content(user)

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
        "hardening_override_content": hardening_content,
        "dashboard_unit_content": dashboard_content,
        "local_validation": validate_generated_plan(
            hardening_content=hardening_content,
            dashboard_content=dashboard_content,
        ),
        "apply_commands": apply_commands,
        "postcheck_commands": postcheck_commands,
        "rollback_commands": rollback_commands,
        "sha_release_plan": [
            "test -n \"$RELEASE_SHA\" && git -C /home/hermes/.hermes/hermes-agent fetch --prune origin",
            "git -C /home/hermes/.hermes/hermes-agent worktree add --detach /home/hermes/releases/$RELEASE_SHA $RELEASE_SHA",
            "test \"$(git -C /home/hermes/releases/$RELEASE_SHA rev-parse HEAD)\" = \"$RELEASE_SHA\"",
            "ln -sfn /home/hermes/releases/$RELEASE_SHA /home/hermes/releases/current.next",
            "mv -Tf /home/hermes/releases/current.next /home/hermes/releases/current",
        ],
        "offsite_backup_plan": [
            "test -n \"$RESTIC_REPOSITORY\" && test -n \"$RESTIC_PASSWORD_FILE\"",
            "restic snapshots --json",
            "restic backup /home/hermes/repo-cockpit /home/hermes/.hermes --exclude-caches --tag hermes-vps",
            "restic check --read-data-subset=5%",
        ],
        "restore_drill_plan": [
            "RESTORE_DIR=$(mktemp -d)",
            "restic restore latest --target \"$RESTORE_DIR\" --tag hermes-vps",
            "test -d \"$RESTORE_DIR/home/hermes/repo-cockpit\"",
            "rm -rf \"$RESTORE_DIR\"",
        ],
        "capacity_recommendation": {
            "minimum_ram_bytes": 2 * 1024 * 1024 * 1024,
            "recommended_ram_bytes": 4 * 1024 * 1024 * 1024,
            "minimum_available_ram_percent": 20,
            "maximum_disk_used_percent": 70,
            "note": "measurement only; resizing is a paid provider action",
        },
        "github_governance_audit": [
            "test -n \"$GITHUB_REPO\"",
            "gh api repos/$GITHUB_REPO/rulesets",
            "gh api repos/$GITHUB_REPO/contents/CODEOWNERS",
            "gh api repos/$GITHUB_REPO/environments/preview",
        ],
        "tailscale_readonly_audit": [
            "tailscale status --json",
            "tailscale debug prefs",
            "export ACL, grants, tags and device inventory from the admin console for human review",
        ],
        "approval_required": [
            "production",
            "DNS",
            "paid resize or storage",
            "reboot",
            "provider snapshot",
            "GitHub App or ruleset mutation",
            "Tailscale ACL or device revocation",
            "systemd install/reload/restart",
        ],
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
    lines.append("")
    lines.append("Local generated-unit validation:")
    lines.append(json.dumps(plan.get("local_validation") or {}, sort_keys=True))
    lines.append("")
    lines.append("SHA release plan (requires approval before service switch):")
    lines.extend(f"{idx}. {cmd}" for idx, cmd in enumerate(plan.get("sha_release_plan") or [], start=1))
    lines.append("")
    lines.append("Offsite backup plan (requires configured remote repository):")
    lines.extend(f"{idx}. {cmd}" for idx, cmd in enumerate(plan.get("offsite_backup_plan") or [], start=1))
    lines.append("")
    lines.append("Restore drill plan:")
    lines.extend(f"{idx}. {cmd}" for idx, cmd in enumerate(plan.get("restore_drill_plan") or [], start=1))
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
