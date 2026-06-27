#!/usr/bin/env python3
"""Read-only write-root audit for Hermes + Repo Cockpit on the VPS."""

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
    "repo_cockpit": "/home/hermes/repo-cockpit",
    "repo_workspaces": "/home/hermes/repo-cockpit/workspaces",
    "repo_data": "/home/hermes/repo-cockpit/data",
}

REQUIRED_WRITE_ROOT_NAMES = ("hermes_home", "repo_cockpit")


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


def _count_workspaces(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(1 for child in path.iterdir() if child.is_dir())


def _split_safe_roots(value: str) -> list[str]:
    roots: list[str] = []
    for item in value.replace(",", ":").split(":"):
        item = item.strip()
        if item:
            roots.append(item)
    return roots


def _configured_safe_roots() -> tuple[str, list[str], str]:
    multi = os.environ.get("HERMES_WRITE_SAFE_ROOTS", "").strip()
    if multi:
        return "HERMES_WRITE_SAFE_ROOTS", _split_safe_roots(multi), multi
    single = os.environ.get("HERMES_WRITE_SAFE_ROOT", "").strip()
    if single:
        return "HERMES_WRITE_SAFE_ROOT", _split_safe_roots(single), single
    return "", [], ""


def _path_covered_by_any(path: Path, roots: list[str]) -> bool:
    for raw in roots:
        try:
            root = Path(raw).expanduser()
            if _is_relative_to(path, root):
                return True
        except Exception:
            continue
    return False


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
        entry = {"path": str(path), "exists": exists}
        if exists:
            entry["resolved"] = str(path.resolve())
            entry["owner"] = _owner(path)
            entry["mode"] = oct(path.stat().st_mode & 0o777)
            if entry["owner"] != expected_owner:
                issues.append(f"{name} is owned by {entry['owner']}, expected {expected_owner}")
            else:
                ok.append(f"{name} owner is {expected_owner}")
        else:
            issues.append(f"{name} is missing: {path}")
        entries[name] = entry

    # Known/natural nesting. These are warnings because broad safe roots can
    # accidentally permit writes in more places than intended.
    nesting: list[str] = []
    for child_name, parent_name in (
        ("hermes_agent", "hermes_home"),
        ("repo_workspaces", "repo_cockpit"),
        ("repo_data", "repo_cockpit"),
    ):
        child = Path(roots[child_name])
        parent = Path(roots[parent_name])
        if child.exists() and parent.exists() and _is_relative_to(child, parent):
            nesting.append(f"{child_name} inside {parent_name}")
    if nesting:
        warnings.append("nested roots: " + ", ".join(nesting))

    workspace_count = _count_workspaces(Path(roots["repo_workspaces"]))
    entries["repo_workspaces"]["workspace_count"] = workspace_count
    if workspace_count:
        ok.append(f"repo_workspaces contains {workspace_count} workspace(s)")
    else:
        warnings.append("repo_workspaces contains no workspace directories")

    data = Path(roots["repo_data"])
    dbs = sorted(p.name for p in data.glob("*.sqlite")) + sorted(p.name for p in data.glob("*.db"))
    entries["repo_data"]["db_files"] = dbs
    if dbs:
        ok.append("repo_data contains DB files outside workspaces")
    else:
        warnings.append("repo_data has no DB files detected")

    recommended = [roots[name] for name in REQUIRED_WRITE_ROOT_NAMES]
    recommended_export = "HERMES_WRITE_SAFE_ROOTS=" + ":".join(recommended)
    env_name, configured_roots, env_value = _configured_safe_roots()
    policy = {
        "env_name": env_name,
        "raw": env_value,
        "configured_roots": configured_roots,
        "required_roots": recommended,
        "recommended_export": recommended_export,
        "covered": {},
        "missing": [],
    }
    if not configured_roots:
        warnings.append("no explicit Hermes write-safe roots configured in this process")
    else:
        for name in REQUIRED_WRITE_ROOT_NAMES:
            covered = _path_covered_by_any(Path(roots[name]), configured_roots)
            policy["covered"][name] = covered
            if not covered:
                policy["missing"].append(name)
        if policy["missing"]:
            warnings.append(
                "configured write-safe roots do not cover: "
                + ", ".join(str(item) for item in policy["missing"])
            )
        else:
            ok.append("configured write-safe roots cover Hermes and Repo Cockpit roots")

    status = "block" if issues else ("warn" if warnings else "ready")
    return {
        "schema": 1,
        "status": status,
        "roots": entries,
        "recommended_write_roots": recommended,
        "write_root_policy": policy,
        "env_HERMES_WRITE_SAFE_ROOT": os.environ.get("HERMES_WRITE_SAFE_ROOT", ""),
        "env_HERMES_WRITE_SAFE_ROOTS": os.environ.get("HERMES_WRITE_SAFE_ROOTS", ""),
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
        if "workspace_count" in entry:
            suffix += f" workspaces={entry['workspace_count']}"
        if "db_files" in entry:
            suffix += f" dbs={','.join(entry['db_files']) or '-'}"
        lines.append(f"- {name}: {entry['path']} exists={entry.get('exists')}{suffix}")
    lines.append("Recommended write roots:")
    for root in report.get("recommended_write_roots") or []:
        lines.append(f"- {root}")
    policy = report.get("write_root_policy") or {}
    if isinstance(policy, dict):
        lines.append(f"Recommended export: {policy.get('recommended_export', '-')}")
        configured = policy.get("configured_roots") or []
        lines.append(
            "Configured write roots: "
            + (", ".join(str(item) for item in configured) if configured else "-")
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
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = collect_write_roots()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_report(report))
    return 0 if report["status"] in {"ready", "warn"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
