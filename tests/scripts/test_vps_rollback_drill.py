"""Tests for the read-only VPS rollback drill helper."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "vps_rollback_drill.py"
spec = importlib.util.spec_from_file_location("vps_rollback_drill", SCRIPT)
vps_rollback_drill = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(vps_rollback_drill)


def test_collect_drill_maps_known_before_file_to_target(tmp_path):
    snap = tmp_path / "file-safety-multi-root"
    snap.mkdir()
    before = snap / "file_safety.py.before"
    before.write_text("old")

    report = vps_rollback_drill.collect_drill(root=tmp_path)

    assert report["status"] == "ready"
    assert report["snapshot_file"] == str(before)
    assert report["target"] == "/home/hermes/.hermes/hermes-agent/agent/file_safety.py"
    assert report["service"] == "hermes-gateway.service"
    assert any("systemctl --user stop hermes-gateway.service" in cmd for cmd in report["commands"])
    assert any("py_compile" in cmd for cmd in report["commands"])
    assert any("diff -u" in cmd for cmd in report["verify_commands"])


def test_collect_drill_can_select_by_file_substring(tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    (old / "file_safety.py.before").write_text("old")
    target = new / "vps_ops_preflight.py.before"
    target.write_text("preflight")

    report = vps_rollback_drill.collect_drill(
        root=tmp_path,
        snapshot_or_file="vps_ops_preflight.py.before",
    )

    assert report["status"] == "ready"
    assert report["snapshot_file"] == str(target)
    assert report["service"] is None
    assert not any("systemctl --user stop" in cmd for cmd in report["commands"])


def test_collect_drill_rejects_unknown_before_file(tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "unknown.py.before").write_text("old")

    report = vps_rollback_drill.collect_drill(root=tmp_path)

    assert report["status"] == "no_known_candidate"
    assert report["issues"]


def test_format_drill_includes_verify_and_warning(tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "updatecheck.py.before").write_text("old")

    text = vps_rollback_drill.format_drill(vps_rollback_drill.collect_drill(root=tmp_path))

    assert "Hermes VPS rollback drill: READY" in text
    assert "Verify before rollback:" in text
    assert "Rollback commands:" in text
    assert "Never bulk-restore" in text
