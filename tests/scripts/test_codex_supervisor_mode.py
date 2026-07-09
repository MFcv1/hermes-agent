"""Tests for the Codex Supervisor Mode read-only shell."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


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
        "telegram": True,
        "cockpit": True,
        "github": True,
        "deploy": True,
        "task_watch": True,
    }
    assert Path(report["reports"]["json"]).is_file()
    assert Path(report["reports"]["markdown"]).is_file()


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
    assert report["checks"]["cockpit"] is False


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
    assert report["checks"]["github"] is False


def test_write_reports_contains_guardrail(tmp_path):
    report = {
        "started_at": "2026-07-09T00:00:00+00:00",
        "intent": "deploy smoke",
        "status": "ready_for_human_review",
        "checks": {"telegram": True, "cockpit": True, "github": True, "deploy": True},
        "telegram": {"skipped": True},
        "cockpit": {"skipped": True},
        "task_watch": {"skipped": True},
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
            "final": {"status": "completed"},
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
    assert report["checks"]["task_watch"] is True
