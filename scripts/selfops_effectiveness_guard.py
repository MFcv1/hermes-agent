#!/usr/bin/env python3
"""Evaluate Self-Ops actions by measured effect, with no-op circuit breaking."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


DEFAULT_COOLDOWN_SECONDS = 24 * 3600
DEFAULT_NOOP_LIMIT = 2


def _load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema": 1, "actions": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": 1, "actions": {}}
    return value if isinstance(value, dict) else {"schema": 1, "actions": {}}


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def action_readiness(state_path: Path, action: str, *, now: int | None = None) -> dict[str, Any]:
    timestamp = int(time.time() if now is None else now)
    state = _load_state(state_path)
    current = (state.get("actions") or {}).get(action) or {}
    cooldown_until = int(current.get("cooldown_until") or 0)
    return {
        "action": action,
        "ready": cooldown_until <= timestamp,
        "cooldown_until": cooldown_until or None,
        "consecutive_noops": int(current.get("consecutive_noops") or 0),
        "status": "ready" if cooldown_until <= timestamp else "cooldown",
    }


def evaluate_action(
    *,
    state_path: Path,
    action: str,
    exit_code: int,
    before_bytes: int,
    after_bytes: int,
    paths_removed: int,
    min_delta_bytes: int,
    min_paths_removed: int = 1,
    noop_limit: int = DEFAULT_NOOP_LIMIT,
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    now: int | None = None,
    top_consumers: list[dict[str, Any]] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    timestamp = int(time.time() if now is None else now)
    state = _load_state(state_path)
    actions = state.setdefault("actions", {})
    previous = actions.get(action) if isinstance(actions.get(action), dict) else {}
    delta_bytes = max(0, int(before_bytes) - int(after_bytes))
    command_executed = int(exit_code) == 0
    objective_achieved = command_executed and (
        delta_bytes >= max(0, int(min_delta_bytes))
        or int(paths_removed) >= max(1, int(min_paths_removed))
    )

    consecutive_noops = int(previous.get("consecutive_noops") or 0)
    cooldown_until = int(previous.get("cooldown_until") or 0)
    escalation_required = False
    if not command_executed:
        status = "command_failed"
    elif objective_achieved:
        status = "objective_achieved"
        consecutive_noops = 0
        cooldown_until = 0
    else:
        consecutive_noops += 1
        status = "no_effect"
        if consecutive_noops >= max(1, int(noop_limit)):
            status = "ineffective"
            cooldown_until = timestamp + max(1, int(cooldown_seconds))
            escalation_required = True

    result = {
        "schema": 1,
        "action": action,
        "evaluated_at": timestamp,
        "exit_code": int(exit_code),
        "command_executed": command_executed,
        "objective_achieved": objective_achieved,
        "delta_bytes": delta_bytes,
        "paths_removed": max(0, int(paths_removed)),
        "minimum_delta_bytes": max(0, int(min_delta_bytes)),
        "consecutive_noops": consecutive_noops,
        "status": status,
        "cooldown_until": cooldown_until or None,
        "escalation_required": escalation_required,
        "top_consumers": list(top_consumers or [])[:20],
        "dry_run": bool(dry_run),
    }
    if not dry_run:
        actions[action] = {
            "consecutive_noops": consecutive_noops,
            "cooldown_until": cooldown_until,
            "last_status": status,
            "last_evaluated_at": timestamp,
        }
        _write_state(state_path, state)
        event_path = state_path.with_suffix(".events.jsonl")
        with event_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    parser.add_argument("--before-bytes", type=int, required=True)
    parser.add_argument("--after-bytes", type=int, required=True)
    parser.add_argument("--paths-removed", type=int, default=0)
    parser.add_argument("--min-delta-bytes", type=int, required=True)
    parser.add_argument("--noop-limit", type=int, default=DEFAULT_NOOP_LIMIT)
    parser.add_argument("--cooldown-seconds", type=int, default=DEFAULT_COOLDOWN_SECONDS)
    parser.add_argument("--top-consumers-json", default="[]")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        consumers = json.loads(args.top_consumers_json)
    except json.JSONDecodeError as exc:
        parser.error(f"invalid --top-consumers-json: {exc}")
    result = evaluate_action(
        state_path=args.state,
        action=args.action,
        exit_code=args.exit_code,
        before_bytes=args.before_bytes,
        after_bytes=args.after_bytes,
        paths_removed=args.paths_removed,
        min_delta_bytes=args.min_delta_bytes,
        noop_limit=args.noop_limit,
        cooldown_seconds=args.cooldown_seconds,
        top_consumers=consumers if isinstance(consumers, list) else [],
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["objective_achieved"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
