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
        "github": {"skipped": True},
        "deploy": {"skipped": True},
    }

    paths = supervisor.write_reports(report, tmp_path)

    markdown = Path(paths["markdown"]).read_text(encoding="utf-8")
    data = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert "did not approve, merge, deploy, delete, or change DNS" in markdown
    assert data["intent"] == "deploy smoke"
