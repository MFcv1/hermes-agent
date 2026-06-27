"""Tests for the Hermes VPS rollback inventory helper."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "vps_rollback_inventory.py"
spec = importlib.util.spec_from_file_location("vps_rollback_inventory", SCRIPT)
vps_rollback_inventory = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(vps_rollback_inventory)


def test_collect_inventory_finds_restorable_files(tmp_path):
    snap = tmp_path / "20260627T140343Z" / "updatecheck-live-port"
    snap.mkdir(parents=True)
    (snap / "gateway_run.py.before").write_text("old")
    (snap / "state.db").write_text("db")
    (snap / "notes.txt").write_text("not restorable")

    report = vps_rollback_inventory.collect_inventory(tmp_path, limit=5)

    assert report["exists"] is True
    assert report["snapshot_count"] >= 1
    item = next(item for item in report["snapshots"] if item["name"] == "20260627T140343Z")
    assert item["restorable_count"] == 2
    assert "updatecheck-live-port/gateway_run.py.before" in item["restorable_sample"]
    assert "updatecheck-live-port/state.db" in item["db_sample"]


def test_format_inventory_includes_restore_warning(tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "app.py.before").write_text("old")

    text = vps_rollback_inventory.format_inventory(
        vps_rollback_inventory.collect_inventory(tmp_path)
    )

    assert "Hermes VPS rollback inventory" in text
    assert "app.py.before" in text
    assert "Never bulk-restore the whole tree while services are running." in text
