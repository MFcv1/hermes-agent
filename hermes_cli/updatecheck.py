"""Read-only Hermes update readiness checks.

This is an operator surface, not a model tool.  It intentionally reports why an
update should or should not proceed without mutating source, config, skills, or
services.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = 8,
) -> CommandResult:
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return CommandResult(proc.returncode, proc.stdout, proc.stderr, False)
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            124,
            exc.stdout if isinstance(exc.stdout, str) else "",
            exc.stderr if isinstance(exc.stderr, str) else "",
            True,
        )
    except Exception as exc:
        return CommandResult(1, "", str(exc), False)


def _git(repo: Path, *args: str, timeout: float = 8) -> CommandResult:
    return _run(["git", *args], cwd=repo, timeout=timeout)


def _one_line(result: CommandResult) -> str | None:
    if result.returncode != 0:
        return None
    return result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""


def _status_counts(status_text: str) -> dict[str, int]:
    counts = {"modified": 0, "untracked": 0, "deleted": 0, "renamed": 0, "other": 0}
    for line in status_text.splitlines():
        if not line:
            continue
        code = line[:2]
        if code == "??":
            counts["untracked"] += 1
        elif "D" in code:
            counts["deleted"] += 1
        elif "R" in code:
            counts["renamed"] += 1
        elif code.strip():
            counts["modified"] += 1
        else:
            counts["other"] += 1
    return counts


def _read_update_cache(hermes_home: Path) -> dict[str, Any]:
    path = hermes_home / ".update_check"
    out: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return out
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        out["error"] = str(exc)
        return out
    out.update(data if isinstance(data, dict) else {"raw": data})
    ts = out.get("ts")
    if isinstance(ts, (int, float)):
        out["age_seconds"] = max(0, int(time.time() - ts))
    return out


def _default_state_path(hermes_home: Path) -> Path:
    return hermes_home / "reports" / "update-check" / "last.json"


def _load_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def _notification_signature(report: dict[str, Any]) -> dict[str, Any]:
    release = report.get("latest_release")
    release_tag = release.get("tag") if isinstance(release, dict) else None
    return {
        "status": report.get("status"),
        "update_available": report.get("update_available"),
        "latest_release": release_tag,
        "issues": sorted(str(item) for item in (report.get("issues") or [])),
        "warnings": sorted(str(item) for item in (report.get("warnings") or [])),
    }


def _state_from_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": 1,
        "ts": int(time.time()),
        "version": report.get("version"),
        "head": report.get("head"),
        "origin_main": report.get("origin_main"),
        "latest_release": (
            report.get("latest_release", {}).get("tag")
            if isinstance(report.get("latest_release"), dict)
            else None
        ),
        "signature": _notification_signature(report),
    }


def evaluate_notification(
    report: dict[str, Any],
    previous_state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Decide whether a scheduled updatecheck should emit a message."""
    signature = _notification_signature(report)
    previous_signature = (
        previous_state.get("signature")
        if isinstance(previous_state, dict)
        and isinstance(previous_state.get("signature"), dict)
        else None
    )
    status = str(report.get("status") or "unknown").lower()
    update_available = report.get("update_available") is True
    changed = previous_signature is not None and signature != previous_signature

    if previous_signature is None:
        reason = "first_run"
        should_notify = True
    elif changed:
        reason = "status_changed"
        should_notify = True
    elif status in {"red", "yellow"}:
        reason = status
        should_notify = True
    elif update_available:
        reason = "update_available"
        should_notify = True
    else:
        reason = "unchanged_green"
        should_notify = False

    return {
        "should_notify": should_notify,
        "reason": reason,
        "changed": changed,
        "previous_status": (
            previous_signature.get("status")
            if isinstance(previous_signature, dict)
            else None
        ),
        "status": status,
    }


def _disk_summary(path: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(path)
    return {
        "path": str(path),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "free_gb": round(usage.free / (1024**3), 2),
        "used_percent": round((usage.used / usage.total) * 100, 1) if usage.total else 0,
    }


def _dir_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for current, dirs, files in os.walk(path):
        parts = Path(current).parts
        if ".git" in parts or "node_modules" in parts or "__pycache__" in parts:
            dirs[:] = []
            continue
        for name in files:
            try:
                total += (Path(current) / name).stat().st_size
            except OSError:
                continue
    return total


_RELEASE_TAG_RE = re.compile(r"^v(\d{4})\.(\d{1,2})\.(\d{1,2})(?:$|[-+])")


def _release_sort_key(tag: str) -> tuple[int, int, int, str] | None:
    match = _RELEASE_TAG_RE.match(tag.strip())
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    return year, month, day, tag


def _remote_release_url(repo: Path, tag: str) -> str | None:
    remote = _one_line(_git(repo, "remote", "get-url", "origin", timeout=3))
    if not remote:
        return None
    base = remote.strip()
    if base.startswith("git@github.com:"):
        base = "https://github.com/" + base.removeprefix("git@github.com:")
    elif base.startswith("ssh://git@github.com/"):
        base = "https://github.com/" + base.removeprefix("ssh://git@github.com/")
    if base.endswith(".git"):
        base = base[:-4]
    if not base.startswith("https://github.com/"):
        return None
    return f"{base}/releases/tag/{tag}"


def _collect_latest_release(repo: Path, *, timeout: float) -> dict[str, Any]:
    result = _git(repo, "ls-remote", "--tags", "--refs", "origin", "v*", timeout=timeout)
    out: dict[str, Any] = {
        "source": "git ls-remote --tags --refs origin v*",
        "available": result.returncode == 0,
        "timed_out": result.timed_out,
    }
    if result.returncode != 0:
        out["error"] = result.stderr.strip()[:300]
        return out

    tags: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        ref = parts[1]
        if not ref.startswith("refs/tags/"):
            continue
        tag = ref.removeprefix("refs/tags/")
        if _release_sort_key(tag) is not None:
            tags.append(tag)

    if not tags:
        out["available"] = False
        out["error"] = "no stable vYYYY.M.D tags found"
        return out

    tag = max(tags, key=lambda item: _release_sort_key(item) or (0, 0, 0, ""))
    out.update(
        {
            "available": True,
            "tag": tag,
            "url": _remote_release_url(repo, tag),
            "stable_tag_count": len(tags),
        }
    )
    return out


def _skill_dirs(path: Path) -> list[Path]:
    if not path.is_dir():
        return []
    return sorted(p for p in path.iterdir() if p.is_dir() and (p / "SKILL.md").exists())


def _collect_catalogs(repo: Path, home: Path) -> dict[str, Any]:
    user_skills = _skill_dirs(home / "skills")
    bundled_skills = _skill_dirs(repo / "skills")
    optional_skills = _skill_dirs(repo / "optional-skills")
    bundled_plugins = sorted(p for p in (repo / "plugins").iterdir()) if (repo / "plugins").is_dir() else []
    user_plugins = sorted(p for p in (home / "plugins").iterdir()) if (home / "plugins").is_dir() else []

    enabled_plugins: list[str] = []
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        raw = cfg.get("plugins", {}).get("enabled", []) if isinstance(cfg, dict) else []
        if isinstance(raw, list):
            enabled_plugins = [str(item) for item in raw]
    except Exception:
        enabled_plugins = []

    return {
        "user_skills": len(user_skills),
        "user_skills_size_bytes": _dir_size_bytes(home / "skills"),
        "bundled_skills": len(bundled_skills),
        "optional_skills": len(optional_skills),
        "bundled_plugins": len([p for p in bundled_plugins if p.is_dir()]),
        "user_plugins": len([p for p in user_plugins if p.is_dir()]),
        "enabled_plugins": enabled_plugins,
    }


def _collect_services() -> dict[str, Any]:
    out: dict[str, Any] = {
        "available": False,
        "units": {},
        "ports": {},
    }
    systemctl = shutil.which("systemctl")
    if systemctl:
        out["available"] = True
        for unit in ("hermes-gateway.service", "hermes-repo-cockpit.service"):
            result = _run([systemctl, "--user", "is-active", unit], timeout=3)
            value = result.stdout.strip() or result.stderr.strip()
            out["units"][unit] = {
                "returncode": result.returncode,
                "state": value,
                "timed_out": result.timed_out,
            }

    ss = shutil.which("ss")
    if ss:
        result = _run([ss, "-ltnp"], timeout=3)
        if result.returncode == 0:
            out["ports"]["8789"] = "127.0.0.1:8789" in result.stdout
            out["ports"]["8765"] = "127.0.0.1:8765" in result.stdout
    return out


def _find_non_owner_sample(root: Path, owner_uid: int, *, limit: int = 20) -> list[str]:
    sample: list[str] = []
    for current, dirs, files in os.walk(root):
        parts = Path(current).parts
        if ".git" in parts or "node_modules" in parts or "__pycache__" in parts:
            dirs[:] = []
            continue
        for name in dirs + files:
            path = Path(current) / name
            try:
                if path.lstat().st_uid != owner_uid:
                    sample.append(str(path))
            except OSError:
                continue
            if len(sample) >= limit:
                return sample
    return sample


def collect_updatecheck(
    *,
    project_root: Path | None = None,
    hermes_home: Path | None = None,
    fresh: bool = True,
    fetch_timeout: float = 20,
) -> dict[str, Any]:
    """Collect a read-only update readiness report."""
    from hermes_cli import __version__
    from hermes_constants import get_hermes_home

    repo = (project_root or Path(__file__).resolve().parent.parent).resolve()
    home = (hermes_home or get_hermes_home()).resolve()
    report: dict[str, Any] = {
        "schema": 1,
        "fresh": fresh,
        "project_root": str(repo),
        "hermes_home": str(home),
        "version": __version__,
        "issues": [],
        "warnings": [],
        "ok": [],
    }

    git_dir = repo / ".git"
    report["git_repo"] = git_dir.exists()
    if not git_dir.exists():
        report["status"] = "red"
        report["issues"].append("not a git checkout; `hermes update` cannot use git here")
        return report

    if fresh:
        fetch = _git(repo, "fetch", "origin", "main", "--quiet", timeout=fetch_timeout)
        report["fetch"] = {
            "returncode": fetch.returncode,
            "timed_out": fetch.timed_out,
            "stderr": fetch.stderr.strip()[:300],
        }
        if fetch.returncode != 0:
            report["warnings"].append("fresh fetch failed; update status may be stale")

    latest_release = _collect_latest_release(
        repo,
        timeout=max(3.0, min(float(fetch_timeout), 8.0)),
    )
    report["latest_release"] = latest_release
    if latest_release.get("available") and latest_release.get("tag"):
        report["ok"].append(f"latest stable release is {latest_release['tag']}")
    else:
        report["warnings"].append("could not resolve latest stable GitHub release tag")

    report["cache"] = _read_update_cache(home)
    age = report["cache"].get("age_seconds")
    if isinstance(age, int):
        report["cache"]["age_human"] = _format_age(age)

    for key, args in {
        "head": ("rev-parse", "HEAD"),
        "branch": ("rev-parse", "--abbrev-ref", "HEAD"),
        "origin_main": ("rev-parse", "origin/main"),
        "shallow": ("rev-parse", "--is-shallow-repository"),
    }.items():
        report[key] = _one_line(_git(repo, *args))

    status = _git(repo, "status", "--porcelain=v1", timeout=8)
    status_text = status.stdout if status.returncode == 0 else ""
    report["worktree"] = {
        "clean": not bool(status_text.strip()),
        "counts": _status_counts(status_text),
        "sample": status_text.splitlines()[:40],
    }

    if not report["worktree"]["clean"]:
        report["issues"].append("working tree has local changes/untracked files")

    shallow = str(report.get("shallow")).lower() == "true"
    report["shallow"] = shallow
    if shallow:
        report["warnings"].append("shallow checkout: exact behind count is not reliable")

    head = report.get("head")
    origin_main = report.get("origin_main")
    if head and origin_main:
        report["update_available"] = head != origin_main
        if head == origin_main:
            report["ok"].append("HEAD matches origin/main")
        elif shallow:
            report["warnings"].append("HEAD differs from origin/main")
        else:
            behind = _one_line(_git(repo, "rev-list", "--count", "HEAD..origin/main"))
            ahead = _one_line(_git(repo, "rev-list", "--count", "origin/main..HEAD"))
            report["behind_count"] = int(behind) if behind and behind.isdigit() else None
            report["ahead_count"] = int(ahead) if ahead and ahead.isdigit() else None
    else:
        report["warnings"].append("could not resolve HEAD or origin/main")

    report["disk"] = _disk_summary(home)
    if report["disk"]["free_gb"] < 8:
        report["issues"].append("less than 8GB free in HERMES_HOME filesystem")
    elif report["disk"]["free_gb"] < 12:
        report["warnings"].append("disk headroom is modest for backup + dependency rebuild")
    else:
        report["ok"].append("disk headroom is acceptable")

    try:
        owner_uid = repo.stat().st_uid
        non_owner = _find_non_owner_sample(repo, owner_uid)
        report["ownership"] = {
            "owner_uid": owner_uid,
            "non_owner_sample": non_owner,
        }
        if non_owner:
            report["issues"].append("checkout contains files not owned by the repo owner")
    except OSError:
        pass

    catalogs = _collect_catalogs(repo, home)
    report["catalogs"] = catalogs
    if catalogs["user_skills"] > 100:
        report["warnings"].append("large user skill catalog; update checks should stay cached/bounded")
    else:
        report["ok"].append(
            f"skill catalog size is modest ({catalogs['user_skills']} user skills)"
        )
    if catalogs["enabled_plugins"]:
        report["warnings"].append(
            "plugins are enabled; plugin update checks should be audited separately"
        )
    else:
        report["ok"].append("no user plugins are enabled")

    services = _collect_services()
    report["services"] = services
    units = services.get("units", {})
    if isinstance(units, dict):
        for unit, data in units.items():
            if not isinstance(data, dict):
                continue
            state = str(data.get("state") or "")
            rc = data.get("returncode")
            if rc == 0 and state == "active":
                report["ok"].append(f"{unit} is active")
            elif state and "No medium found" not in state:
                report["warnings"].append(f"{unit} state is {state!r}")
    ports = services.get("ports", {})
    if isinstance(ports, dict) and ports.get("8789"):
        report["issues"].append("unexpected Repo Cockpit dev port 8789 is listening")

    if report["issues"]:
        report["status"] = "red"
    elif report["warnings"]:
        report["status"] = "yellow"
    else:
        report["status"] = "green"
    return report


def _format_age(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def format_updatecheck(report: dict[str, Any]) -> str:
    status = str(report.get("status", "unknown")).upper()
    icon = {"GREEN": "OK", "YELLOW": "WARN", "RED": "BLOCK"}.get(status, "INFO")
    lines = [
        f"{icon} Hermes updatecheck: {status}",
        f"Version: {report.get('version', '?')}",
        f"Project: {report.get('project_root', '?')}",
    ]

    head = str(report.get("head") or "?")[:10]
    origin = str(report.get("origin_main") or "?")[:10]
    lines.append(f"Git: {head} -> origin/main {origin}")
    if report.get("update_available") is True:
        behind = report.get("behind_count")
        if behind is None:
            lines.append("Update: available (exact count unknown)")
        else:
            lines.append(f"Update: available ({behind} commits behind)")
    elif report.get("update_available") is False:
        lines.append("Update: not available")

    latest_release = report.get("latest_release") or {}
    if isinstance(latest_release, dict) and latest_release.get("tag"):
        release_line = f"Release: {latest_release.get('tag')}"
        if latest_release.get("url"):
            release_line += f" ({latest_release.get('url')})"
        lines.append(release_line)

    cache = report.get("cache") or {}
    if isinstance(cache, dict):
        if cache.get("exists"):
            lines.append(
                "Cache: "
                f"behind={cache.get('behind', '?')} "
                f"age={cache.get('age_human', '?')}"
            )
        else:
            lines.append("Cache: missing")

    worktree = report.get("worktree") or {}
    if isinstance(worktree, dict):
        counts = worktree.get("counts") or {}
        lines.append(
            "Worktree: "
            f"{'clean' if worktree.get('clean') else 'dirty'} "
            f"(modified={counts.get('modified', 0)}, "
            f"untracked={counts.get('untracked', 0)}, "
            f"deleted={counts.get('deleted', 0)})"
        )

    disk = report.get("disk") or {}
    if isinstance(disk, dict):
        lines.append(
            f"Disk: {disk.get('free_gb', '?')}GB free, "
            f"{disk.get('used_percent', '?')}% used"
        )

    catalogs = report.get("catalogs") or {}
    if isinstance(catalogs, dict):
        lines.append(
            "Catalogs: "
            f"{catalogs.get('user_skills', '?')} user skills, "
            f"{catalogs.get('bundled_skills', '?')} bundled, "
            f"{catalogs.get('user_plugins', '?')} user plugins, "
            f"{len(catalogs.get('enabled_plugins') or [])} enabled"
        )

    services = report.get("services") or {}
    if isinstance(services, dict):
        units = services.get("units") or {}
        ports = services.get("ports") or {}
        if units or ports:
            unit_bits = [
                f"{name.replace('.service', '')}={data.get('state', '?')}"
                for name, data in units.items()
                if isinstance(data, dict)
                and "No medium found" not in str(data.get("state") or "")
            ]
            port_bits = [
                f"port{port}={'on' if active else 'off'}"
                for port, active in ports.items()
            ]
            lines.append("Services: " + ", ".join(unit_bits + port_bits))

    for title, key in (("Issues", "issues"), ("Warnings", "warnings"), ("OK", "ok")):
        values = report.get(key) or []
        if not values:
            continue
        lines.append("")
        lines.append(f"{title}:")
        for item in values[:10]:
            lines.append(f"- {item}")
        if len(values) > 10:
            lines.append(f"- ... {len(values) - 10} more")

    return "\n".join(lines)


def format_updatecheck_short(report: dict[str, Any]) -> str:
    """Compact update readiness summary for chat surfaces."""
    status = str(report.get("status", "unknown")).upper()
    icon = {"GREEN": "OK", "YELLOW": "WARN", "RED": "BLOCK"}.get(status, "INFO")
    head = str(report.get("head") or "?")[:10]
    origin = str(report.get("origin_main") or "?")[:10]
    worktree = report.get("worktree") or {}
    counts = worktree.get("counts") if isinstance(worktree, dict) else {}
    disk = report.get("disk") or {}
    latest_release = report.get("latest_release") or {}

    if report.get("update_available") is True:
        behind = report.get("behind_count")
        update = "available" + (f" ({behind} commits behind)" if behind is not None else "")
    elif report.get("update_available") is False:
        update = "not available"
    else:
        update = "unknown"

    lines = [
        f"{icon} Updatecheck: {status}",
        f"Git: {head} -> origin/main {origin}",
        f"Update: {update}",
        "Worktree: "
        + ("clean" if worktree.get("clean") else "dirty")
        + f" (modified={counts.get('modified', 0)}, untracked={counts.get('untracked', 0)})",
        f"Disk: {disk.get('free_gb', '?')}GB free, {disk.get('used_percent', '?')}% used",
    ]
    if isinstance(latest_release, dict) and latest_release.get("tag"):
        lines.append(f"Release: {latest_release.get('tag')}")

    blockers = list(report.get("issues") or [])
    warnings = list(report.get("warnings") or [])
    if blockers:
        lines.append("")
        lines.append("Blockers:")
        lines.extend(f"- {item}" for item in blockers[:4])
    elif warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {item}" for item in warnings[:4])
    else:
        lines.append("")
        lines.append("Ready: no blocker found.")
    return "\n".join(lines)


def run_updatecheck(args: Any) -> int:
    report = collect_updatecheck(
        fresh=not bool(getattr(args, "cached", False)),
        fetch_timeout=float(getattr(args, "timeout", 20) or 20),
    )
    stateful = bool(getattr(args, "stateful", False))
    silent_unchanged = bool(getattr(args, "silent_unchanged", False))
    if stateful or silent_unchanged:
        state_arg = getattr(args, "state_path", None)
        state_path = (
            Path(state_arg).expanduser()
            if state_arg
            else _default_state_path(Path(str(report["hermes_home"])))
        )
        previous = _load_state(state_path)
        notification = evaluate_notification(report, previous)
        notification["state_path"] = str(state_path)
        report["notification"] = notification
        _write_state(state_path, _state_from_report(report))
        if silent_unchanged and not notification["should_notify"]:
            print("[SILENT]")
            return 0

    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_updatecheck(report))
    return 0 if report.get("status") in {"green", "yellow"} else 2
