#!/usr/bin/env python3
"""Read-only rollback inventory for Hermes VPS ops snapshots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path("/home/hermes/ops-snapshots")
RESTORABLE_SUFFIXES = (".before", ".bak", ".db", ".sqlite", ".service", ".timer")


def _snapshot_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)


def _file_summary(snapshot: Path, *, limit: int = 80) -> dict[str, Any]:
    files = [p for p in snapshot.rglob("*") if p.is_file()]
    restorable = [
        str(p.relative_to(snapshot))
        for p in files
        if p.name.endswith(RESTORABLE_SUFFIXES) or p.suffix in {".db", ".sqlite"}
    ]
    dbs = [
        str(p.relative_to(snapshot))
        for p in files
        if p.suffix in {".db", ".sqlite"} or "state.db" in p.name or "cockpit.sqlite" in p.name
    ]
    return {
        "file_count": len(files),
        "restorable_count": len(restorable),
        "restorable_sample": restorable[:limit],
        "db_sample": dbs[:limit],
    }


def collect_inventory(root: Path = DEFAULT_ROOT, *, limit: int = 12) -> dict[str, Any]:
    snapshots = []
    for snapshot in _snapshot_dirs(root)[:limit]:
        summary = _file_summary(snapshot)
        snapshots.append(
            {
                "path": str(snapshot),
                "name": snapshot.name,
                "mtime": int(snapshot.stat().st_mtime),
                **summary,
            }
        )
    return {
        "schema": 1,
        "root": str(root),
        "exists": root.exists(),
        "snapshot_count": len(_snapshot_dirs(root)),
        "snapshots": snapshots,
    }


def format_inventory(report: dict[str, Any]) -> str:
    lines = [
        "Hermes VPS rollback inventory",
        f"Root: {report.get('root')}",
        f"Snapshots: {report.get('snapshot_count', 0)}",
    ]
    if not report.get("exists"):
        lines.append("Status: snapshot root missing")
        return "\n".join(lines)

    for item in report.get("snapshots") or []:
        lines.append("")
        lines.append(f"- {item['name']}")
        lines.append(f"  path: {item['path']}")
        lines.append(
            f"  files: {item['file_count']} total, "
            f"{item['restorable_count']} restorable-looking"
        )
        dbs = item.get("db_sample") or []
        if dbs:
            lines.append("  dbs: " + ", ".join(dbs[:5]))
        sample = item.get("restorable_sample") or []
        if sample:
            lines.append("  sample: " + ", ".join(sample[:5]))

    lines.append("")
    lines.append("Rollback rule: restore only from a matching snapshot after stopping the affected service.")
    lines.append("Never bulk-restore the whole tree while services are running.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = collect_inventory(args.root, limit=max(1, args.limit))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_inventory(report))
    return 0 if report.get("exists") else 2


if __name__ == "__main__":
    raise SystemExit(main())
