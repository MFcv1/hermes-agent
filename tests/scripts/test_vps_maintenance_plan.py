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


def test_plan_contains_locally_validated_hardening_dashboard_and_offsite_backup(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(vps_maintenance_plan, "_home_for_user", lambda _user: tmp_path)
    monkeypatch.setattr(vps_maintenance_plan, "_uid_for_user", lambda _user: "1001")

    plan = vps_maintenance_plan.collect_plan()

    assert plan["local_validation"]["ok"] is True
    hardening = plan["hardening_override_content"]
    for directive in (
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "ProtectSystem=strict",
        "MemoryMax=",
        "TasksMax=",
        "KillMode=control-group",
    ):
        assert directive in hardening
    assert "ExecStart=" in plan["dashboard_unit_content"]
    assert "--host 127.0.0.1" in plan["dashboard_unit_content"]
    assert any("restic check" in command for command in plan["offsite_backup_plan"])
    assert any("restic restore" in command for command in plan["restore_drill_plan"])
    assert any("worktree add --detach" in command for command in plan["sha_release_plan"])
    assert plan["capacity_recommendation"]["minimum_ram_bytes"] == 2 * 1024**3
    assert any("rulesets" in command for command in plan["github_governance_audit"])
    assert plan["tailscale_readonly_audit"][0] == "tailscale status --json"
    assert "production" in plan["approval_required"]


def test_hardening_validator_fails_when_required_directive_is_removed():
    content = vps_maintenance_plan._hardening_override_content(
        ("/home/hermes/.hermes",), memory_max="2G"
    ).replace("NoNewPrivileges=true\n", "")

    result = vps_maintenance_plan.validate_generated_plan(
        hardening_content=content,
        dashboard_content="[Service]\nExecStart=/bin/true --host 127.0.0.1\n",
    )

    assert result["ok"] is False
    assert "NoNewPrivileges=true" in result["missing_hardening"]
