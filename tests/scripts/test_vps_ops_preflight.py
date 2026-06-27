"""Tests for the aggregate VPS ops preflight helper."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "vps_ops_preflight.py"
spec = importlib.util.spec_from_file_location("vps_ops_preflight", SCRIPT)
vps_ops_preflight = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(vps_ops_preflight)


def test_updatecheck_dirty_worktree_is_warning_not_blocker():
    report = {"issues": ["working tree has local changes/untracked files"]}

    assert vps_ops_preflight._updatecheck_blocking_issue(report) is False


def test_updatecheck_other_issue_blocks():
    report = {"issues": ["checkout contains files not owned by the repo owner"]}

    assert vps_ops_preflight._updatecheck_blocking_issue(report) is True


def test_format_preflight_summarizes_sections():
    text = vps_ops_preflight.format_preflight(
        {
            "status": "warn",
            "issues": [],
            "warnings": ["reboot readiness is WARN"],
            "ok": ["rollback inventory has 2 useful recent bundle(s)"],
            "updatecheck": {"status": "red", "update_available": True},
            "reboot": {"status": "warn", "ports": {"8765": True}, "disk": {"free_gb": 9.2}},
            "rollback": {"snapshot_count": 26, "useful_recent": [{"name": "snap"}]},
            "write_roots": {
                "status": "warn",
                "workspace_count": 2,
                "policy": {"configured_roots": []},
            },
        }
    )

    assert "Hermes VPS ops preflight: WARN" in text
    assert "Updatecheck: RED, update_available=True" in text
    assert "Rollback: 26 snapshots, 1 useful recent" in text
    assert "Write roots: WARN, workspaces=2, policy=unset" in text
