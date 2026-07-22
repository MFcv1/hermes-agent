#!/usr/bin/env python3
"""Read-only write-root audit for the Hermes Dashboard VPS."""

from __future__ import annotations

import argparse
import json
import os
import pwd
from pathlib import Path
from typing import Any


DEFAULT_ROOTS = {
    "hermes_home": "/home/hermes/.hermes",
    "hermes_agent": "/home/hermes/.hermes/hermes-agent",
    "work_sessions": "/home/hermes/.hermes/work-sessions",
}
REQUIRED_WRITE_ROOT_NAMES = ("hermes_home",)


def _owner(path: Path) -> str:
    try:
        return pwd.getpwuid(path.stat().st_uid).pw_name
    except Exception:
        return str(path.stat().st_uid)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _split_safe_roots(value: str) -> list[str]:
    return [item.strip() for item in value.replace(",", ":").split(":") if item.strip()]


def _configured_safe_roots() -> tuple[str, list[str], str]:
    multi = os.environ.get("HERMES_WRITE_SAFE_ROOTS", "").strip()
    if multi:
        return "HERMES_WRITE_SAFE_ROOTS", _split_safe_roots(multi), multi
    single = os.environ.get("HERMES_WRITE_SAFE_ROOT", "").strip()
    if single:
        return "HERMES_WRITE_SAFE_ROOT", _split_safe_roots(single), single
    return "", [], ""


def _path_covered_by_any(path: Path, roots: list[str]) -> bool:
    return any(_is_relative_to(path, Path(raw).expanduser()) for raw in roots)


def collect_write_roots(
    roots: dict[str, str] | None = None,
    *,
    expected_owner: str = "hermes",
) -> dict[str, Any]:
    roots = roots or DEFAULT_ROOTS
    entries: dict[str, dict[str, Any]] = {}
    issues: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []

    for name, raw in roots.items():
        path = Path(raw)
        exists = path.exists()
        entry: dict[str, Any] = {"path": str(path), "exists": exists}
        if exists:
            entry.update(
                resolved=str(path.resolve()),
                owner=_owner(path),
                mode=oct(path.stat().st_mode & 0o777),
            )
            if entry["owner"] != expected_owner:
                issues.append(f"{name} is owned by {entry['owner']}, expected {expected_owner}")
            else:
                ok.append(f"{name} owner is {expected_owner}")
        else:
            issues.append(f"{name} is missing: {path}")
        entries[name] = entry

    home = Path(roots["hermes_home"])
    nested = [
        name
        for name in ("hermes_agent", "work_sessions")
        if name in roots and Path(roots[name]).exists() and home.exists()
        and _is_relative_to(Path(roots[name]), home)
    ]
    if nested:
        warnings.append("nested roots under hermes_home: " + ", ".join(nested))

    if "work_sessions" in roots and Path(roots["work_sessions"]).is_dir():
        count = sum(1 for child in Path(roots["work_sessions"]).iterdir() if child.is_dir())
        entries["work_sessions"]["session_count"] = count
        ok.append(f"work_sessions contains {count} session directorie(s)")

    recommended = [roots[name] for name in REQUIRED_WRITE_ROOT_NAMES]
    env_name, configured_roots, env_value = _configured_safe_roots()
    covered = {
        name: _path_covered_by_any(Path(roots[name]), configured_roots)
        for name in REQUIRED_WRITE_ROOT_NAMES
    } if configured_roots else {}
    missing = [name for name, value in covered.items() if not value]
    if not configured_roots:
        warnings.append("no explicit Hermes write-safe roots configured in this process")
    elif missing:
        warnings.append("configured write-safe roots do not cover: " + ", ".join(missing))
    else:
        ok.append("configured write-safe roots cover Hermes home")

    policy = {
        "env_name": env_name,
        "raw": env_value,
        "configured_roots": configured_roots,
        "required_roots": recommended,
        "recommended_export": "HERMES_WRITE_SAFE_ROOTS=" + ":".join(recommended),
        "covered": covered,
        "missing": missing,
    }
    status = "block" if issues else ("warn" if warnings else "ready")
    return {
        "schema": 1,
        "status": status,
        "roots": entries,
        "recommended_write_roots": recommended,
        "write_root_policy": policy,
        "issues": issues,
        "warnings": warnings,
        "ok": ok,
    }


def format_report(report: dict[str, Any]) -> str:
    lines = [f"Hermes VPS write roots audit: {str(report.get('status')).upper()}"]
    for name, entry in (report.get("roots") or {}).items():
        if not isinstance(entry, dict) or "path" not in entry:
            continue
        suffix = ""
        if "owner" in entry:
            suffix = f" owner={entry['owner']} mode={entry.get('mode')}"
        if "session_count" in entry:
            suffix += f" sessions={entry['session_count']}"
        lines.append(f"- {name}: {entry['path']} exists={entry.get('exists')}{suffix}")
    lines.append("Recommended write roots:")
    lines.extend(f"- {root}" for root in report.get("recommended_write_roots") or [])
    policy = report.get("write_root_policy") or {}
    lines.append(f"Recommended export: {policy.get('recommended_export', '-')}")
    configured = policy.get("configured_roots") or []
    lines.append("Configured write roots: " + (", ".join(configured) if configured else "-"))
    for title, key in (("Issues", "issues"), ("Warnings", "warnings"), ("OK", "ok")):
        values = report.get(key) or []
        if values:
            lines.extend(["", f"{title}:", *(f"- {item}" for item in values)])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = collect_write_roots()
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else format_report(report))
    return 0 if report["status"] in {"ready", "warn"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
