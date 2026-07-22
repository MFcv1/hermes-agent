from __future__ import annotations

import time


def test_format_vps_overview_includes_storage_cron_and_services():
    from hermes_cli.vps_status import format_vps_overview

    report = {
        "status": "yellow",
        "disk": {
            "root": {"free_gb": 7.5, "used_percent": 81.2},
            "home": {"free_gb": 20.0, "used_percent": 50.0},
        },
        "cron": {"age_seconds": 125},
        "jobs": {"enabled": 2, "total": 3},
        "services": [{"name": "hermes-gateway", "state": "active"}],
        "uptime": "up 1 day, load average: 0.10, 0.20, 0.30",
        "warnings": ["root disk headroom is low"],
    }

    text = format_vps_overview(report)

    assert "VPS status: YELLOW" in text
    assert "Root disk: 7.5GB free" in text
    assert "Cron: heartbeat age 2m, jobs 2/3 enabled" in text
    assert "gateway=active" in text
    assert "root disk headroom is low" in text


def test_collect_vps_overview_uses_cron_heartbeat(tmp_path, monkeypatch):
    from hermes_cli import vps_status

    home = tmp_path / ".hermes"
    cron_dir = home / "cron"
    cron_dir.mkdir(parents=True)
    heartbeat = cron_dir / "ticker_last_success"
    heartbeat.write_text("ok")
    now = time.time()
    monkeypatch.setattr(vps_status.time, "time", lambda: now)
    monkeypatch.setattr(vps_status, "_service_state", lambda name: {"name": name, "state": "active", "ok": True})
    monkeypatch.setattr(vps_status, "_run", lambda *_a, **_k: {"ok": True, "output": "up 1 min"})

    report = vps_status.collect_vps_overview(hermes_home=home)

    assert report["cron"]["ok"] is True
    assert report["services"][0]["state"] == "active"
    assert report["jobs"]["total"] == 0


def test_collect_vps_overview_lists_projects_and_flags_repo_outside_root(tmp_path, monkeypatch):
    from hermes_cli import vps_status

    home = tmp_path / ".hermes"
    (home / "hermes-agent" / ".git").mkdir(parents=True)
    project = tmp_path / "mes-projets" / "Portfolio"
    project.mkdir(parents=True)
    (project / ".git").mkdir()
    stray = tmp_path / "old-clones" / "Forgotten"
    stray.mkdir(parents=True)
    (stray / ".git").mkdir()

    def fake_git(repo, *args):
        if args[:2] == ("branch", "--show-current"):
            return "main"
        if args[:3] == ("remote", "get-url", "origin"):
            return f"https://github.com/MFcv1/{repo.name}.git"
        return ""

    monkeypatch.setattr(vps_status, "_git_value", fake_git)
    monkeypatch.setattr(vps_status, "_service_state", lambda name: {"name": name, "state": "active", "ok": True})
    monkeypatch.setattr(vps_status, "_run", lambda *_a, **_k: {"ok": True, "output": "up 1 min"})

    report = vps_status.collect_vps_overview(hermes_home=home)

    assert [item["name"] for item in report["inventory"]["projects"]] == ["Portfolio"]
    assert [item["path"] for item in report["inventory"]["unorganized"]] == [str(stray)]
    assert str(home / "hermes-agent") not in [item["path"] for item in report["inventory"]["unorganized"]]


def test_format_vps_projects_view_is_simple_and_read_only():
    from hermes_cli.vps_status import format_vps_projects_view

    report = {
        "disk": {"root": {"free_gb": 12.0, "used_percent": 52.0}},
        "services": [
            {"name": "hermes-gateway", "state": "active"},
            {"name": "hermes-dashboard", "state": "active"},
        ],
        "inventory": {
            "system_repo": "/home/hermes/.hermes/hermes-agent",
            "projects": [{
                "name": "portfolio-v2-hermes-test",
                "remote_label": "MFcv1/portfolio-v2-hermes-test",
                "branch": "main",
                "dirty": False,
            }],
            "unorganized": [{"path": "/home/hermes/forgotten/repo"}],
        },
    }

    text = format_vps_projects_view(report)

    assert "🖥 VPS Hermes" in text
    assert "Hermes Agent · Gateway active · Dashboard active" in text
    assert "📁 Mes projets — 1" in text
    assert "MFcv1/portfolio-v2-hermes-test" in text
    assert "/home/hermes/forgotten/repo" in text
