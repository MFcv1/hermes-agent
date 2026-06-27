"""Tests for the read-only VPS maintenance plan helper."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "vps_maintenance_plan.py"
spec = importlib.util.spec_from_file_location("vps_maintenance_plan", SCRIPT)
vps_maintenance_plan = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(vps_maintenance_plan)


def test_collect_plan_generates_write_safe_root_override(tmp_path, monkeypatch):
    monkeypatch.setattr(vps_maintenance_plan, "_home_for_user", lambda _user: tmp_path)
    monkeypatch.setattr(vps_maintenance_plan, "_uid_for_user", lambda _user: "1001")

    plan = vps_maintenance_plan.collect_plan()

    assert plan["status"] == "ready_to_apply"
    assert plan["override"]["path"].endswith(
        ".config/systemd/user/hermes-gateway.service.d/10-write-safe-roots.conf"
    )
    assert plan["expected_override_content"] == (
        "[Service]\n"
        "Environment=HERMES_WRITE_SAFE_ROOTS=/home/hermes/.hermes:/home/hermes/repo-cockpit\n"
    )
    assert any("vps_ops_preflight.py" in cmd for cmd in plan["apply_commands"])
    assert any("systemctl --user restart hermes-gateway.service" in cmd for cmd in plan["apply_commands"])
    assert any("telegram_desktop_cua_smoke.py" in cmd for cmd in plan["postcheck_commands"])
    assert any("rm -f" in cmd for cmd in plan["rollback_commands"])


def test_collect_plan_detects_existing_matching_override(tmp_path, monkeypatch):
    monkeypatch.setattr(vps_maintenance_plan, "_home_for_user", lambda _user: tmp_path)
    monkeypatch.setattr(vps_maintenance_plan, "_uid_for_user", lambda _user: "1001")
    override = (
        tmp_path
        / ".config"
        / "systemd"
        / "user"
        / "hermes-gateway.service.d"
        / "10-write-safe-roots.conf"
    )
    override.parent.mkdir(parents=True)
    override.write_text(
        "[Service]\n"
        "Environment=HERMES_WRITE_SAFE_ROOTS=/home/hermes/.hermes:/home/hermes/repo-cockpit\n"
    )

    plan = vps_maintenance_plan.collect_plan()

    assert plan["status"] == "already_applied"


def test_format_plan_includes_apply_postcheck_and_rollback_sections(tmp_path, monkeypatch):
    monkeypatch.setattr(vps_maintenance_plan, "_home_for_user", lambda _user: tmp_path)
    monkeypatch.setattr(vps_maintenance_plan, "_uid_for_user", lambda _user: "1001")

    text = vps_maintenance_plan.format_plan(vps_maintenance_plan.collect_plan())

    assert "Hermes VPS maintenance plan: READY_TO_APPLY" in text
    assert "Expected override content:" in text
    assert "Apply commands:" in text
    assert "Post-check commands:" in text
    assert "Rollback commands:" in text
