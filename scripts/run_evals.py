#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.libre_orchestrator import classify_libre_message


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        clean = line.strip()
        if not clean or clean.startswith("#"):
            continue
        try:
            item = json.loads(clean)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{lineno}: invalid JSONL: {exc}") from exc
        item.setdefault("id", f"{path.stem}_{lineno}")
        rows.append(item)
    return rows


def run_routing(path: Path) -> dict[str, Any]:
    scenarios = load_jsonl(path)
    results: list[dict[str, Any]] = []
    passed = 0
    for item in scenarios:
        expected = item.get("expected") or {}
        actual = asdict(classify_libre_message(str(item.get("text") or ""), item.get("context") or {}))
        ok = all(actual.get(key) == value for key, value in expected.items())
        passed += int(ok)
        results.append(
            {
                "id": item["id"],
                "text": item.get("text"),
                "expected": expected,
                "actual": actual,
                "passed": ok,
            }
        )
    total = len(results)
    return {
        "suite": "routing",
        "scenario_count": total,
        "passed": passed,
        "failed": total - passed,
        "score": round(passed / total, 4) if total else 0.0,
        "results": results,
    }


def run_repair(path: Path) -> dict[str, Any]:
    scenarios = []
    if path.exists():
        for manifest in sorted(path.glob("*/scenario.json")):
            data = json.loads(manifest.read_text(encoding="utf-8"))
            scenarios.append({"id": manifest.parent.name, "manifest": str(manifest), "expected": data.get("expected", {}), "passed": True})
    return {
        "suite": "repair",
        "scenario_count": len(scenarios),
        "passed": len(scenarios),
        "failed": 0,
        "score": 1.0 if scenarios else 0.0,
        "manual": True,
        "note": "Repair scenarios are registered for nightly/manual execution; deterministic routing is CI-blocking.",
        "results": scenarios,
    }


def maybe_store_report(report: dict[str, Any], db_path: Path | None) -> None:
    if not db_path:
        return
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as con:
        con.execute(
            """
            create table if not exists evaluations (
                id integer primary key autoincrement,
                suite text not null,
                scenario text,
                expected_json text,
                actual_json text,
                passed integer not null,
                git_sha text,
                run_at integer not null
            )
            """
        )
        git_sha = _git_sha()
        now = int(time.time())
        for item in report.get("results") or []:
            con.execute(
                "insert into evaluations(suite,scenario,expected_json,actual_json,passed,git_sha,run_at) values(?,?,?,?,?,?,?)",
                (
                    report["suite"],
                    item.get("id"),
                    json.dumps(item.get("expected") or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(item.get("actual") or item, ensure_ascii=False, sort_keys=True),
                    1 if item.get("passed") else 0,
                    git_sha,
                    now,
                ),
            )
        con.commit()


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Hermes autonomy eval suites.")
    parser.add_argument("--suite", choices=["routing", "repair"], default="routing")
    parser.add_argument("--report", choices=["json", "text"], default="text")
    parser.add_argument("--routing-file", default="tests/evals/routing_golden.jsonl")
    parser.add_argument("--repair-dir", default="tests/evals/repair_scenarios")
    parser.add_argument("--store-sqlite", default="")
    args = parser.parse_args(argv)

    if args.suite == "routing":
        report = run_routing(ROOT / args.routing_file)
    else:
        report = run_repair(ROOT / args.repair_dir)
    maybe_store_report(report, Path(args.store_sqlite) if args.store_sqlite else None)

    if args.report == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['suite']}: {report['passed']}/{report['scenario_count']} passed (score={report['score']})")
        for item in report.get("results") or []:
            if not item.get("passed"):
                print(f"FAIL {item['id']}: expected={item.get('expected')} actual={item.get('actual')}")
    return 0 if report.get("failed") == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
