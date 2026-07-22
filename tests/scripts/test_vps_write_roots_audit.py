"""Tests for the Hermes VPS write-root audit helper."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "vps_write_roots_audit.py"
spec = importlib.util.spec_from_file_location("vps_write_roots_audit", SCRIPT)
vps_write_roots_audit = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(vps_write_roots_audit)


def test_collect_write_roots_warns_for_nested_roots_without_blocking(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_agent = hermes_home / "hermes-agent"
    work_sessions = hermes_home / "work-sessions"
    for path in (hermes_agent, work_sessions / "ws_123"):
        path.mkdir(parents=True)
    owner = vps_write_roots_audit._owner(tmp_path)
    monkeypatch.delenv("HERMES_WRITE_SAFE_ROOT", raising=False)

    report = vps_write_roots_audit.collect_write_roots(
        {
            "hermes_home": str(hermes_home),
            "hermes_agent": str(hermes_agent),
            "work_sessions": str(work_sessions),
        },
        expected_owner=owner,
    )

    assert report["status"] == "warn"
    assert not report["issues"]
    assert any("nested roots" in warning for warning in report["warnings"])
    assert report["roots"]["work_sessions"]["session_count"] == 1
    assert report["write_root_policy"]["recommended_export"].startswith(
        "HERMES_WRITE_SAFE_ROOTS="
    )
    assert any("no explicit Hermes write-safe roots" in warning for warning in report["warnings"])


def test_collect_write_roots_accepts_explicit_multi_root_policy(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_agent = hermes_home / "hermes-agent"
    work_sessions = hermes_home / "work-sessions"
    for path in (hermes_agent, work_sessions / "ws_123"):
        path.mkdir(parents=True)
    owner = vps_write_roots_audit._owner(tmp_path)
    monkeypatch.setenv("HERMES_WRITE_SAFE_ROOTS", str(hermes_home))
    monkeypatch.delenv("HERMES_WRITE_SAFE_ROOT", raising=False)

    report = vps_write_roots_audit.collect_write_roots(
        {
            "hermes_home": str(hermes_home),
            "hermes_agent": str(hermes_agent),
            "work_sessions": str(work_sessions),
        },
        expected_owner=owner,
    )

    assert report["write_root_policy"]["env_name"] == "HERMES_WRITE_SAFE_ROOTS"
    assert report["write_root_policy"]["covered"] == {
        "hermes_home": True,
    }
    assert not any("write-safe roots" in warning for warning in report["warnings"])
    assert any("configured write-safe roots cover" in item for item in report["ok"])


def test_format_report_lists_recommended_roots():
    text = vps_write_roots_audit.format_report(
        {
            "status": "warn",
            "roots": {"repo": {"path": "/repo", "exists": True, "owner": "hermes", "mode": "0o775"}},
            "recommended_write_roots": ["/home/hermes/.hermes"],
            "write_root_policy": {
                "recommended_export": "HERMES_WRITE_SAFE_ROOTS=/home/hermes/.hermes",
                "configured_roots": [],
            },
            "issues": [],
            "warnings": ["nested roots"],
            "ok": ["repo owner is hermes"],
        }
    )

    assert "Hermes VPS write roots audit: WARN" in text
    assert "Recommended write roots:" in text
    assert "- /home/hermes/.hermes" in text
    assert "Recommended export: HERMES_WRITE_SAFE_ROOTS=/home/hermes/.hermes" in text
