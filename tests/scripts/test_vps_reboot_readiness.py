"""Tests for the Hermes VPS reboot readiness helper."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "vps_reboot_readiness.py"
spec = importlib.util.spec_from_file_location("vps_reboot_readiness", SCRIPT)
vps_reboot_readiness = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(vps_reboot_readiness)


def test_read_reboot_required_lists_packages(tmp_path):
    marker = tmp_path / "var" / "run" / "reboot-required"
    marker.parent.mkdir(parents=True)
    marker.write_text("*** System restart required ***\n")
    (marker.parent / "reboot-required.pkgs").write_text("libc6\nlinux-base\n")

    report = vps_reboot_readiness.read_reboot_required(tmp_path)

    assert report == {"required": True, "packages": ["libc6", "linux-base"]}


def test_format_report_includes_reboot_ports_and_findings():
    text = vps_reboot_readiness.format_report(
        {
            "status": "warn",
            "reboot_required": {"required": True, "packages": ["libc6"]},
            "disk": {"free_gb": 9.2, "used_percent": 61.0},
            "memory": {"mem_available_mib": 366, "swap_free_mib": 1996},
            "ports": {"80": True, "443": True, "8765": True, "8789": False},
            "issues": [],
            "warnings": ["OS reports a pending reboot"],
            "ok": ["SQLite DB integrity checks passed"],
        }
    )

    assert "Hermes VPS reboot readiness: WARN" in text
    assert "Reboot: required (libc6)" in text
    assert "Ports: 80=on, 443=on, 8765=on, 8789=off" in text
    assert "- OS reports a pending reboot" in text
