"""Small read-only VPS/Hermes status overview helpers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


def _run(argv: list[str], *, timeout: float = 5) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "output": ((proc.stdout or "") + (proc.stderr or "")).strip(),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": 124, "output": "timeout"}
    except Exception as exc:
        return {"ok": False, "returncode": 1, "output": str(exc)}


def _disk(path: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(path)
    return {
        "path": str(path),
        "free_gb": round(usage.free / (1024**3), 2),
        "used_percent": round((usage.used / usage.total) * 100, 1) if usage.total else 0,
    }


def _cron_heartbeat(hermes_home: Path) -> dict[str, Any]:
    candidates = [
        hermes_home / "cron" / "ticker_last_success",
        hermes_home / "cron" / "ticker_heartbeat",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            age = max(0, int(time.time() - path.stat().st_mtime))
        except OSError:
            continue
        return {"path": str(path), "age_seconds": age, "ok": age < 180}
    return {"path": str(candidates[0]), "age_seconds": None, "ok": False}


def _jobs_summary(hermes_home: Path) -> dict[str, Any]:
    path = hermes_home / "cron" / "jobs.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"total": 0, "enabled": 0, "path": str(path), "ok": not path.exists()}
    jobs = data if isinstance(data, list) else []
    enabled = [job for job in jobs if isinstance(job, dict) and job.get("enabled", True)]
    return {"total": len(jobs), "enabled": len(enabled), "path": str(path), "ok": True}


def _service_state(name: str) -> dict[str, Any]:
    result = _run(
        [
            "bash",
            "-lc",
            f"XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user is-active {name}",
        ],
        timeout=4,
    )
    output = str(result.get("output") or "").strip().splitlines()
    state = output[0] if output else ""
    return {
        "name": name,
        "state": state or "unknown",
        "ok": result.get("ok") is True and state == "active",
    }


def _git_value(repo: Path, *args: str) -> str:
    result = _run(["git", "-C", str(repo), *args], timeout=4)
    return str(result.get("output") or "").strip() if result.get("ok") else ""


def _remote_label(remote: str) -> str:
    value = remote.strip().removesuffix(".git")
    for prefix in ("https://github.com/", "http://github.com/", "git@github.com:"):
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def _git_repo_info(path: Path) -> dict[str, Any]:
    branch = _git_value(path, "branch", "--show-current") or "detached"
    remote = _git_value(path, "remote", "get-url", "origin")
    dirty = bool(_git_value(path, "status", "--porcelain"))
    return {
        "name": path.name,
        "path": str(path),
        "branch": branch,
        "remote": remote,
        "remote_label": _remote_label(remote) if remote else "local only",
        "dirty": dirty,
        "state": "changes" if dirty else "ready",
    }


def _find_unorganized_repos(
    user_home: Path,
    *,
    projects_root: Path,
    system_repo: Path,
) -> list[dict[str, Any]]:
    """Find visible Git checkouts outside the system and projects roots.

    The scan is deliberately shallow and prunes caches/dependencies so `/vps`
    stays fast even on a long-lived server home directory.
    """
    ignored_top_level = {
        ".cache", ".config", ".local", ".npm", ".pki", ".ssh",
        ".supabase", ".hermes", "backups", projects_root.name,
    }
    ignored_nested = {
        "node_modules", ".venv", "venv", "__pycache__", ".cache",
    }
    found: list[dict[str, Any]] = []
    try:
        children = sorted(user_home.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return found
    for child in children:
        if not child.is_dir() or child.name in ignored_top_level or child.name.startswith("."):
            continue
        for root, dirs, files in os.walk(child):
            current = Path(root)
            try:
                depth = len(current.relative_to(child).parts)
            except ValueError:
                continue
            if depth > 3:
                dirs[:] = []
                continue
            if ".git" in dirs or ".git" in files:
                if current != system_repo and not current.is_relative_to(projects_root):
                    found.append(_git_repo_info(current))
                dirs[:] = []
                continue
            dirs[:] = [name for name in dirs if name not in ignored_nested]
    return found


def _project_inventory(hermes_home: Path) -> dict[str, Any]:
    user_home = hermes_home.parent
    projects_root = user_home / "mes-projets"
    system_repo = hermes_home / "hermes-agent"
    projects: list[dict[str, Any]] = []
    if projects_root.is_dir():
        try:
            candidates = sorted(projects_root.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            candidates = []
        for candidate in candidates:
            if candidate.is_dir() and (candidate / ".git").exists():
                projects.append(_git_repo_info(candidate))
    return {
        "root": str(projects_root),
        "system_repo": str(system_repo),
        "projects": projects,
        "unorganized": _find_unorganized_repos(
            user_home,
            projects_root=projects_root,
            system_repo=system_repo,
        ),
    }


def collect_vps_overview(*, hermes_home: Path | None = None) -> dict[str, Any]:
    from hermes_constants import get_hermes_home

    home = (hermes_home or get_hermes_home()).resolve()
    root_disk = _disk(Path("/"))
    home_disk = _disk(home)
    cron = _cron_heartbeat(home)
    jobs = _jobs_summary(home)
    services = [
        _service_state("hermes-gateway"),
        _service_state("hermes-dashboard"),
    ]
    uptime = _run(["bash", "-lc", "uptime | sed 's/^ *//'"], timeout=4)

    issues: list[str] = []
    warnings: list[str] = []
    if root_disk["free_gb"] < 5:
        issues.append("root disk has less than 5GB free")
    elif root_disk["free_gb"] < 10:
        warnings.append("root disk headroom is low")
    if not cron["ok"]:
        warnings.append("cron heartbeat is missing or stale")
    for service in services:
        if service["state"] not in {"active", "unknown"}:
            warnings.append(f"{service['name']} is {service['state']}")

    if issues:
        status = "red"
    elif warnings:
        status = "yellow"
    else:
        status = "green"

    return {
        "status": status,
        "hermes_home": str(home),
        "disk": {"root": root_disk, "home": home_disk},
        "cron": cron,
        "jobs": jobs,
        "services": services,
        "uptime": uptime.get("output") or "",
        "issues": issues,
        "warnings": warnings,
        "inventory": _project_inventory(home),
    }


def _age_human(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def format_vps_overview(report: dict[str, Any]) -> str:
    status = str(report.get("status") or "unknown").upper()
    icon = {"GREEN": "OK", "YELLOW": "WARN", "RED": "BLOCK"}.get(status, "INFO")
    disk = report.get("disk") or {}
    root_disk = disk.get("root") or {}
    home_disk = disk.get("home") or {}
    cron = report.get("cron") or {}
    jobs = report.get("jobs") or {}
    lines = [
        f"{icon} VPS status: {status}",
        f"Root disk: {root_disk.get('free_gb', '?')}GB free, {root_disk.get('used_percent', '?')}% used",
        f"Hermes home disk: {home_disk.get('free_gb', '?')}GB free, {home_disk.get('used_percent', '?')}% used",
        f"Cron: heartbeat age {_age_human(cron.get('age_seconds'))}, jobs {jobs.get('enabled', 0)}/{jobs.get('total', 0)} enabled",
    ]
    service_bits = [
        f"{item.get('name', '?').replace('hermes-', '')}={item.get('state', '?')}"
        for item in report.get("services") or []
    ]
    if service_bits:
        lines.append("Services: " + ", ".join(service_bits))
    if report.get("uptime"):
        lines.append("Load: " + str(report.get("uptime"))[:120])
    for title, key in (("Issues", "issues"), ("Warnings", "warnings")):
        values = report.get(key) or []
        if values:
            lines.append("")
            lines.append(f"{title}:")
            lines.extend(f"- {value}" for value in values[:5])
    return "\n".join(lines)


def format_vps_overview_html(report: dict[str, Any]) -> str:
    import html

    text = format_vps_overview(report)
    lines = text.splitlines()
    if not lines:
        return "<b>VPS status</b>"
    return "<b>" + html.escape(lines[0]) + "</b>\n" + "\n".join(
        html.escape(line) for line in lines[1:]
    )


def format_vps_projects_view(report: dict[str, Any]) -> str:
    """Render the simple, read-only Telegram view of the VPS desktop."""
    root_disk = ((report.get("disk") or {}).get("root") or {})
    inventory = report.get("inventory") or {}
    projects = inventory.get("projects") or []
    unorganized = inventory.get("unorganized") or []
    service_states = {
        str(item.get("name") or "").removeprefix("hermes-"): str(item.get("state") or "unknown")
        for item in report.get("services") or []
    }

    lines = [
        "🖥 VPS Hermes",
        (
            f"Stockage : {root_disk.get('used_percent', '?')} % utilisé · "
            f"{root_disk.get('free_gb', '?')} Go disponibles"
        ),
        "",
        "⚙️ Système",
        (
            "└ Hermes Agent"
            f" · Gateway {service_states.get('gateway', 'unknown')}"
            f" · Dashboard {service_states.get('dashboard', 'unknown')}"
        ),
        f"  {inventory.get('system_repo', '?')}",
        "",
        f"📁 Mes projets — {len(projects)}",
    ]
    if not projects:
        lines.append("└ Aucun projet cloné")
    for index, project in enumerate(projects):
        tree = "└" if index == len(projects) - 1 else "├"
        state = "changements locaux" if project.get("dirty") else "prêt"
        lines.extend([
            f"{tree} {project.get('name', '?')} · {state}",
            f"  GitHub : {project.get('remote_label', 'local only')}",
            f"  Branche : {project.get('branch', '?')}",
        ])

    if unorganized:
        lines.extend(["", "⚠️ Projets Git trouvés hors de mes-projets"])
        lines.extend(f"- {item.get('path', '?')}" for item in unorganized)
    return "\n".join(lines)
