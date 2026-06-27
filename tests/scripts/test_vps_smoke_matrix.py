"""Tests for the read-only VPS smoke matrix helper."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "vps_smoke_matrix.py"
spec = importlib.util.spec_from_file_location("vps_smoke_matrix", SCRIPT)
vps_smoke_matrix = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(vps_smoke_matrix)


def _fake_loader(preflight_status="warn", maintenance_status="ready_to_apply", rollback_status="ready"):
    def load(name: str):
        if name == "vps_ops_preflight":
            return SimpleNamespace(
                collect_preflight=lambda **_: {
                    "status": preflight_status,
                    "warnings": ["updatecheck is RED because the live worktree is dirty"],
                    "issues": [],
                }
            )
        if name == "vps_maintenance_plan":
            return SimpleNamespace(
                collect_plan=lambda: {
                    "status": maintenance_status,
                    "override": {"exists": False},
                }
            )
        if name == "vps_rollback_drill":
            return SimpleNamespace(
                collect_drill=lambda: {
                    "status": rollback_status,
                    "snapshot_file": "/snap/file_safety.py.before",
                    "target": "/home/hermes/.hermes/hermes-agent/agent/file_safety.py",
                    "service": "hermes-gateway.service",
                }
            )
        raise AssertionError(name)

    return load


def test_collect_matrix_ready_with_warn_preflight(monkeypatch):
    monkeypatch.setattr(vps_smoke_matrix, "_load_script_module", _fake_loader())

    report = vps_smoke_matrix.collect_matrix()

    assert report["status"] == "ready"
    assert [check["name"] for check in report["checks"]] == [
        "ops-preflight",
        "maintenance-plan",
        "rollback-drill",
    ]
    assert report["telegram_cua"]["status"] == "operator_required"
    assert any(item["name"] == "conv-existing-repo" for item in report["telegram_cua"]["commands"])


def test_collect_matrix_blocks_when_rollback_drill_missing(monkeypatch):
    monkeypatch.setattr(
        vps_smoke_matrix,
        "_load_script_module",
        _fake_loader(rollback_status="no_known_candidate"),
    )

    report = vps_smoke_matrix.collect_matrix()

    assert report["status"] == "block"
    failed = [check for check in report["checks"] if not check["ok"]]
    assert failed[0]["name"] == "rollback-drill"


def test_format_matrix_lists_cua_commands(monkeypatch):
    monkeypatch.setattr(vps_smoke_matrix, "_load_script_module", _fake_loader())

    text = vps_smoke_matrix.format_matrix(vps_smoke_matrix.collect_matrix())

    assert "Hermes VPS smoke matrix: READY" in text
    assert "Telegram CUA commands to run from the operator Mac:" in text
    assert "normal-chat" in text
    assert "conv-existing-repo" in text
    assert "screenshot_review_required" in text
