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
