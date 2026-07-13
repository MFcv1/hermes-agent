from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "selfops_effectiveness_guard.py"
spec = importlib.util.spec_from_file_location("selfops_effectiveness_guard", SCRIPT)
guard = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(guard)


def test_two_successful_commands_without_delta_trigger_cooldown_and_escalation(tmp_path):
    state_path = tmp_path / "state.json"

    first = guard.evaluate_action(
        state_path=state_path,
        action="disk_full_cleanup",
        exit_code=0,
        before_bytes=1000,
        after_bytes=1000,
        paths_removed=0,
        min_delta_bytes=100,
        now=1000,
        top_consumers=[{"path": "/data/cache", "bytes": 900}],
    )
    second = guard.evaluate_action(
        state_path=state_path,
        action="disk_full_cleanup",
        exit_code=0,
        before_bytes=1000,
        after_bytes=1000,
        paths_removed=0,
        min_delta_bytes=100,
        now=1100,
        top_consumers=[{"path": "/data/cache", "bytes": 900}],
    )

    assert first["command_executed"] is True
    assert first["objective_achieved"] is False
    assert first["status"] == "no_effect"
    assert second["status"] == "ineffective"
    assert second["cooldown_until"] == 1100 + 24 * 3600
    assert second["escalation_required"] is True
    assert second["top_consumers"][0]["path"] == "/data/cache"
    assert guard.action_readiness(state_path, "disk_full_cleanup", now=1200)["ready"] is False


def test_real_delta_resets_noop_counter(tmp_path):
    state_path = tmp_path / "state.json"
    guard.evaluate_action(
        state_path=state_path,
        action="cleanup",
        exit_code=0,
        before_bytes=1000,
        after_bytes=1000,
        paths_removed=0,
        min_delta_bytes=100,
        now=100,
    )

    result = guard.evaluate_action(
        state_path=state_path,
        action="cleanup",
        exit_code=0,
        before_bytes=1000,
        after_bytes=700,
        paths_removed=1,
        min_delta_bytes=100,
        now=200,
    )

    assert result["status"] == "objective_achieved"
    assert result["delta_bytes"] == 300
    assert result["consecutive_noops"] == 0
    assert guard.action_readiness(state_path, "cleanup", now=201)["ready"] is True


def test_failed_command_is_not_reported_as_executed_or_effective(tmp_path):
    result = guard.evaluate_action(
        state_path=tmp_path / "state.json",
        action="cleanup",
        exit_code=1,
        before_bytes=1000,
        after_bytes=500,
        paths_removed=2,
        min_delta_bytes=100,
        now=100,
    )

    assert result["command_executed"] is False
    assert result["objective_achieved"] is False
    assert result["status"] == "command_failed"


def test_dry_run_returns_decision_without_mutating_state(tmp_path):
    state_path = tmp_path / "state.json"
    result = guard.evaluate_action(
        state_path=state_path,
        action="cleanup",
        exit_code=0,
        before_bytes=1000,
        after_bytes=1000,
        paths_removed=0,
        min_delta_bytes=100,
        now=100,
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert not state_path.exists()
    assert not state_path.with_suffix(".events.jsonl").exists()


def test_persisted_events_distinguish_command_and_objective(tmp_path):
    state_path = tmp_path / "state.json"
    guard.evaluate_action(
        state_path=state_path,
        action="cleanup",
        exit_code=0,
        before_bytes=1000,
        after_bytes=1000,
        paths_removed=0,
        min_delta_bytes=100,
        now=100,
    )

    event = json.loads(state_path.with_suffix(".events.jsonl").read_text().splitlines()[0])
    assert event["command_executed"] is True
    assert event["objective_achieved"] is False
