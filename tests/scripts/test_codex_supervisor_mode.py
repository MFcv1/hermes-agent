"""Tests for the Codex Supervisor Mode read-only shell."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "codex_supervisor_mode.py"
spec = importlib.util.spec_from_file_location("codex_supervisor_mode", SCRIPT)
supervisor = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(supervisor)


def _args(tmp_path, **overrides):
    data = {
        "message": "test instruction",
        "command": None,
        "send": False,
        "no_enter": False,
        "skip_telegram": False,
        "telegram_app": "Telegram",
        "cua_mode": "som",
        "evidence_dir": str(tmp_path / "evidence"),
        "wait_after_send": 0,
        "skip_cockpit": False,
        "cockpit_base_url": "http://127.0.0.1:8765",
        "cockpit_token": "token",
        "cockpit_token_env": "REPO_COCKPIT_INTERNAL_TOKEN",
        "local_env_file": str(tmp_path / ".env"),
        "vps_ssh": "",
        "vps_env_file": "/home/hermes/.hermes/.env",
        "task_id": "",
        "watch_task": False,
        "watch_timeout": 1,
        "poll_interval": 0.5,
        "github_repo": "",
        "github_branch": "",
        "deploy_url": "",
        "timeout": 1,
        "report_dir": str(tmp_path / "reports"),
        "ledger_path": str(tmp_path / "reports" / "ledger.jsonl"),
        "run_id": "",
        "session_id": "",
        "project_root": "",
        "model": "",
        "provider": "",
        "effort": "",
        "budget_calls": None,
        "used_calls": None,
        "input_tokens": None,
        "output_tokens": None,
        "artifact_digest": "",
        "deployment_id": "",
        "json": False,
    }
    data.update(overrides)
    return argparse.Namespace(**data)


def test_supervisor_writes_report_with_all_checks_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(
        supervisor,
        "run_telegram",
        lambda args: {"status": "screenshot_review_required", "evidence": {"json": "telegram.json"}},
    )
    monkeypatch.setattr(
        supervisor,
        "collect_cockpit",
        lambda args: {
            "mode": "http",
            "token_present": True,
            "endpoints": {
                "/health": {"ok": True, "status_code": 200, "body": {"status": "ok"}},
                "/api/hosting/capabilities": {"ok": True, "status_code": 200, "body": {"providers": []}},
            },
        },
    )
    monkeypatch.setattr(supervisor, "collect_github", lambda args: {"skipped": True})
    monkeypatch.setattr(supervisor, "smoke_deploy_url", lambda args: {"skipped": True})

    report = supervisor.run_supervisor(_args(tmp_path))

    assert report["status"] == "ready_for_human_review"
    assert report["checks"] == {
        "telegram": {
            "outcome": "pass",
            "required": True,
            "reason": "Telegram/CUA reached screenshot_review_required",
        },
        "cockpit": {
            "outcome": "pass",
            "required": True,
            "reason": "Required Cockpit endpoints passed",
        },
        "github": {
            "outcome": "skipped",
            "required": False,
            "reason": "GitHub collection was skipped",
        },
        "deploy": {
            "outcome": "skipped",
            "required": False,
            "reason": "Deploy smoke was skipped",
        },
        "task_watch": {
            "outcome": "skipped",
            "required": False,
            "reason": "Task watch was skipped",
        },
        "handoff": {
            "outcome": "skipped",
            "required": False,
            "reason": "Project handoff was not required",
        },
    }
    assert Path(report["reports"]["json"]).is_file()
    assert Path(report["reports"]["markdown"]).is_file()
    assert report["legacy_checks"]["github"] is False
    assert report["run_id"].startswith("sup_")
    ledger = Path(report["ledger"]["path"])
    assert ledger.is_file()
    records = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert records[0]["event_type"] == "supervisor_run_started"
    record = records[-1]
    assert record["run_id"] == report["run_id"]
    assert record["lineage"]["status"] == "ready_for_human_review"
    assert record["previous_hash"] == records[0]["record_hash"]


def test_supervisor_marks_attention_when_cockpit_health_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "run_telegram", lambda args: {"skipped": True})
    monkeypatch.setattr(
        supervisor,
        "collect_cockpit",
        lambda args: {
            "mode": "http",
            "token_present": True,
            "endpoints": {
                "/health": {"ok": False, "status_code": 500},
                "/api/hosting/capabilities": {"ok": True, "status_code": 200},
            },
        },
    )
    monkeypatch.setattr(supervisor, "collect_github", lambda args: {"skipped": True})
    monkeypatch.setattr(supervisor, "smoke_deploy_url", lambda args: {"skipped": True})

    report = supervisor.run_supervisor(_args(tmp_path, skip_telegram=True))

    assert report["status"] == "attention_required"
    assert report["checks"]["cockpit"]["outcome"] == "fail"


def test_github_branch_failure_is_not_ready(monkeypatch):
    report = {
        "telegram": {"skipped": True},
        "cockpit": {"skipped": True},
        "github": {
            "repo": "MFcv1/example",
            "view_ok": True,
            "branch": {"name": "main", "ok": False},
        },
        "deploy": {"skipped": True},
    }

    assert supervisor.summarize_status(report) == "attention_required"
    assert report["checks"]["github"]["outcome"] == "fail"


def test_write_reports_contains_guardrail(tmp_path):
    report = {
        "started_at": "2026-07-09T00:00:00+00:00",
        "intent": "deploy smoke",
        "status": "ready_for_human_review",
        "checks": {
            name: {"outcome": "skipped", "required": False, "reason": "not requested"}
            for name in supervisor.CHECK_NAMES
        },
        "telegram": {"skipped": True},
        "cockpit": {"skipped": True},
        "task_watch": {"skipped": True},
        "handoff": {"skipped": True},
        "github": {"skipped": True},
        "deploy": {"skipped": True},
    }

    paths = supervisor.write_reports(report, tmp_path)

    markdown = Path(paths["markdown"]).read_text(encoding="utf-8")
    data = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert "did not approve, merge, deploy, delete, or change DNS" in markdown
    assert data["intent"] == "deploy smoke"


def test_watch_task_stops_on_terminal_status(tmp_path, monkeypatch):
    calls = []

    def fake_collect(args):
        calls.append(args.task_id)
        return {
            "mode": "http",
            "token_present": True,
            "endpoints": {
                "/health": {"ok": True, "status_code": 200},
                "/api/hosting/capabilities": {"ok": True, "status_code": 200},
                "/api/internal/tasks/op_1/autonomy": {
                    "ok": True,
                    "status_code": 200,
                    "body": {
                        "ok": True,
                        "task": {
                            "id": "op_1",
                            "status": "pilot_questions_required",
                            "current_phase": "plan_ready",
                            "repo": "MFcv1/example",
                        },
                        "runs": [{"phase": "plan_ready", "status": "completed"}],
                    },
                },
            },
        }

    monkeypatch.setattr(supervisor, "collect_cockpit", fake_collect)

    report = supervisor.watch_task(_args(tmp_path, task_id="op_1", watch_task=True))

    assert report["ok"] is True
    assert report["status"] == "pilot_questions_required"
    assert report["final"]["phase"] == "plan_ready"
    assert report["final"]["repo"] == "MFcv1/example"
    assert len(report["samples"]) == 1
    assert calls == ["op_1"]


def test_watch_task_times_out_when_status_keeps_running(tmp_path, monkeypatch):
    monkeypatch.setattr(
        supervisor,
        "collect_cockpit",
        lambda args: {
            "mode": "http",
            "token_present": True,
            "endpoints": {
                "/health": {"ok": True, "status_code": 200},
                "/api/hosting/capabilities": {"ok": True, "status_code": 200},
                "/api/internal/tasks/op_running/autonomy": {
                    "ok": True,
                    "status_code": 200,
                    "body": {
                        "ok": True,
                        "task": {
                            "id": "op_running",
                            "status": "running_gpt55",
                            "current_phase": "worker",
                        },
                    },
                },
            },
        },
    )

    report = supervisor.watch_task(
        _args(tmp_path, task_id="op_running", watch_task=True, watch_timeout=0, poll_interval=0.5)
    )

    assert report["ok"] is False
    assert report["status"] == "watch_timeout"
    assert report["timeout"] is True
    assert report["final"]["last_task_status"] == "running_gpt55"


def test_supervisor_includes_task_watch_check(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "run_telegram", lambda args: {"skipped": True})
    monkeypatch.setattr(
        supervisor,
        "watch_task",
        lambda args: {
            "ok": True,
            "task_id": "op_1",
            "status": "completed",
            "terminal": True,
            "timeout": False,
            "samples": [],
            "final": {
                "status": "completed",
                "project_status": {
                    "ok": True,
                    "path": "PROJECT_STATUS.md",
                    "source_commit": "a" * 40,
                },
            },
        },
    )
    monkeypatch.setattr(
        supervisor,
        "collect_cockpit",
        lambda args: {
            "mode": "http",
            "token_present": True,
            "endpoints": {
                "/health": {"ok": True, "status_code": 200},
                "/api/hosting/capabilities": {"ok": True, "status_code": 200},
            },
        },
    )
    monkeypatch.setattr(supervisor, "collect_github", lambda args: {"skipped": True})
    monkeypatch.setattr(supervisor, "smoke_deploy_url", lambda args: {"skipped": True})

    report = supervisor.run_supervisor(_args(tmp_path, skip_telegram=True, task_id="op_1", watch_task=True))

    assert report["status"] == "ready_for_human_review"
    assert report["checks"]["task_watch"]["outcome"] == "pass"


@pytest.mark.parametrize(
    "task_status",
    ("failed", "blocked", "cancelled", "needs_approval", "pilot_questions_required"),
)
def test_terminal_task_requiring_attention_is_not_a_pass(task_status):
    report = _summary_report("task_watch", "pass", required=True)
    report["task_watch"]["status"] = task_status

    assert supervisor.summarize_status(report) == "attention_required"
    assert report["checks"]["task_watch"]["outcome"] == "fail"


def _check_evidence(name, outcome):
    evidence = {
        "telegram": {
            "pass": {"status": "screenshot_review_required"},
            "fail": {"status": "failed"},
            "skipped": {"skipped": True},
            "unknown": {},
        },
        "cockpit": {
            "pass": {
                "endpoints": {
                    "/health": {"ok": True},
                    "/api/hosting/capabilities": {"ok": True},
                }
            },
            "fail": {
                "endpoints": {
                    "/health": {"ok": False},
                    "/api/hosting/capabilities": {"ok": True},
                }
            },
            "skipped": {"skipped": True},
            "unknown": {},
        },
        "github": {
            "pass": {"view_ok": True},
            "fail": {"view_ok": False},
            "skipped": {"skipped": True},
            "unknown": {},
        },
        "deploy": {
            "pass": {"ok": True},
            "fail": {"ok": False},
            "skipped": {"skipped": True},
            "unknown": {},
        },
        "task_watch": {
            "pass": {"ok": True, "terminal": True, "timeout": False, "status": "completed"},
            "fail": {"ok": False, "terminal": True, "timeout": True, "status": "watch_timeout"},
            "skipped": {"skipped": True},
            "unknown": {},
        },
        "handoff": {
            "pass": {"ok": True, "path": "PROJECT_STATUS.md", "source_commit": "a" * 40},
            "fail": {"ok": False, "errors": ["missing Source SHA"]},
            "skipped": {"skipped": True},
            "unknown": {},
        },
    }
    return evidence[name][outcome]


def _summary_report(name, outcome, *, required):
    report = {
        "skip_telegram": name != "telegram" or not required,
        "skip_cockpit": name != "cockpit" or not required,
        "task_id": "op_1" if name == "task_watch" and required else None,
        "watch_task": name == "task_watch" and required,
        "github_repo": "MFcv1/example" if name == "github" and required else None,
        "github_branch": None,
        "deploy_url": "https://example.test" if name == "deploy" and required else None,
        "project_root": "/tmp/project" if name == "handoff" and required else None,
    }
    for check_name in supervisor.CHECK_NAMES:
        report[check_name] = {"skipped": True}
    if name == "task_watch" and required:
        report["cockpit"] = _check_evidence("cockpit", "pass")
        report["handoff"] = _check_evidence("handoff", "pass")
    report[name] = _check_evidence(name, outcome)
    return report


@pytest.mark.parametrize("name", supervisor.CHECK_NAMES)
@pytest.mark.parametrize("outcome", ("pass", "fail", "unknown"))
def test_check_outcome_matrix(name, outcome):
    report = _summary_report(name, outcome, required=True)

    status = supervisor.summarize_status(report)

    assert report["checks"][name]["outcome"] == outcome
    expected = {
        "pass": "ready_for_human_review",
        "fail": "attention_required",
        "unknown": "incomplete_evidence",
    }
    assert status == expected[outcome]


@pytest.mark.parametrize("name", supervisor.CHECK_NAMES)
def test_required_skipped_is_incomplete_evidence(name):
    report = _summary_report(name, "skipped", required=True)

    assert supervisor.summarize_status(report) == "incomplete_evidence"
    assert report["checks"][name] == {
        "outcome": "skipped",
        "required": True,
        "reason": report["checks"][name]["reason"],
    }


@pytest.mark.parametrize("name", supervisor.CHECK_NAMES)
def test_optional_skipped_is_neutral(name):
    report = _summary_report(name, "skipped", required=False)

    assert supervisor.summarize_status(report) == "ready_for_human_review"
    assert report["checks"][name]["outcome"] == "skipped"
    assert report["checks"][name]["required"] is False


def test_requested_task_without_watch_cannot_be_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "run_telegram", lambda args: {"skipped": True})
    monkeypatch.setattr(supervisor, "watch_task", lambda args: {"skipped": True})
    monkeypatch.setattr(supervisor, "collect_cockpit", lambda args: {"skipped": True})
    monkeypatch.setattr(supervisor, "collect_github", lambda args: {"skipped": True})
    monkeypatch.setattr(supervisor, "smoke_deploy_url", lambda args: {"skipped": True})

    report = supervisor.run_supervisor(
        _args(tmp_path, skip_telegram=True, skip_cockpit=True, task_id="op_1", watch_task=False)
    )

    assert report["status"] == "incomplete_evidence"
    assert report["checks"]["task_watch"]["required"] is True


@pytest.mark.parametrize("status", ("attention_required", "incomplete_evidence"))
def test_main_returns_nonzero_for_non_ready_status(status, monkeypatch):
    monkeypatch.setattr(supervisor, "run_supervisor", lambda args: {"status": status, "checks": {}})

    assert supervisor.main(["--message", "check", "--json"]) == 2


def test_report_is_redacted_and_bounded(tmp_path):
    report = {
        "started_at": "2026-07-13T00:00:00+00:00",
        "intent": "inspect",
        "status": "attention_required",
        "checks": {},
        "cockpit": {
            "access_token": "short-secret-value",
            "body": "sk-proj-abcdefghijklmnopqrstuvwxyz" + ("x" * 5000),
            "items": list(range(100)),
        },
    }

    sanitized = supervisor.sanitize_report(report)
    paths = supervisor.write_reports(report, tmp_path)
    serialized = Path(paths["json"]).read_text(encoding="utf-8")

    assert "short-secret-value" not in serialized
    assert "sk-proj-abcdefghijklmnopqrstuvwxyz" not in serialized
    assert "TRUNCATED" in serialized
    assert len(sanitized["cockpit"]["items"]) == supervisor.MAX_REPORT_LIST_ITEMS + 1


def test_completed_task_without_project_status_is_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "run_telegram", lambda args: {"skipped": True})
    monkeypatch.setattr(
        supervisor,
        "watch_task",
        lambda args: {
            "ok": True,
            "task_id": "op_1",
            "status": "completed",
            "terminal": True,
            "timeout": False,
            "samples": [],
            "final": {"status": "completed"},
        },
    )
    monkeypatch.setattr(
        supervisor,
        "collect_cockpit",
        lambda args: {
            "endpoints": {
                "/health": {"ok": True},
                "/api/hosting/capabilities": {"ok": True},
            }
        },
    )
    monkeypatch.setattr(supervisor, "collect_github", lambda args: {"skipped": True})
    monkeypatch.setattr(supervisor, "smoke_deploy_url", lambda args: {"skipped": True})

    report = supervisor.run_supervisor(
        _args(tmp_path, skip_telegram=True, task_id="op_1", watch_task=True)
    )

    assert report["status"] == "incomplete_evidence"
    assert report["checks"]["handoff"]["required"] is True
    assert report["checks"]["handoff"]["outcome"] == "unknown"


def test_project_status_contract_and_ledger_hash_chain(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "PROJECT_STATUS.md").write_text(
        """# Project Status

## Status
Ready for review.
## Source
Commit: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
Branch: feat/example
## Gates
Tests pass.
## URLs
No deployment.
## Resources and limits
No paid resources.
## Rollback
Revert the commit.
## Next action
Human review.
""",
        encoding="utf-8",
    )

    handoff = supervisor.collect_handoff(
        _args(tmp_path, project_root=str(project)),
        {"skipped": True},
        {"skipped": True},
    )
    assert handoff["ok"] is True
    assert handoff["source_commit"] == "a" * 40

    ledger = tmp_path / "ledger.jsonl"
    first = supervisor.append_ledger_event(
        ledger, {"run_id": "run_1", "status": "started"}
    )
    second = supervisor.append_ledger_event(
        ledger, {"run_id": "run_1", "status": "completed"}
    )
    lines = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert first["sequence"] == 1
    assert second["sequence"] == 2
    assert lines[1]["previous_hash"] == lines[0]["record_hash"]
    assert supervisor.verify_ledger(ledger)["ok"] is True
    lines[0]["status"] = "tampered"
    ledger.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    assert supervisor.verify_ledger(ledger)["ok"] is False
