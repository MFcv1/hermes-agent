from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "cron" / "scripts" / "vps_healthcheck.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("vps_healthcheck", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_vps_healthcheck_green_is_silent(monkeypatch, capsys):
    mod = _load_script()

    monkeypatch.setattr(
        "hermes_cli.vps_status.collect_vps_overview",
        lambda: {"status": "green"},
    )

    assert mod.main([]) == 0
    assert capsys.readouterr().out == ""


def test_vps_healthcheck_warning_prints_report(monkeypatch, capsys):
    mod = _load_script()

    monkeypatch.setattr(
        "hermes_cli.vps_status.collect_vps_overview",
        lambda: {
            "status": "yellow",
            "disk": {
                "root": {"free_gb": 7, "used_percent": 80},
                "home": {"free_gb": 7, "used_percent": 80},
            },
            "cron": {"age_seconds": None},
            "jobs": {"enabled": 0, "total": 0},
            "services": [],
            "warnings": ["cron heartbeat is missing or stale"],
        },
    )

    assert mod.main([]) == 0
    assert "VPS status: YELLOW" in capsys.readouterr().out
