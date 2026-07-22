#!/usr/bin/env python3
"""Read-only VPS reboot readiness check for the Hermes deployment."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_UNITS = ("hermes-gateway.service", "hermes-dashboard.service")
DEFAULT_DISABLED_UNITS: tuple[str, ...] = ()
DEFAULT_DBS = (
    "/home/hermes/.hermes/state.db",
    "/home/hermes/.hermes/work_sessions.db",
)


def _run(argv: list[str], *, timeout: float = 8) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            argv,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": 124,
            "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "").strip() if isinstance(exc.stderr, str) else "",
            "timed_out": True,
        }
    except Exception as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc), "timed_out": False}


def _user_uid(user: str) -> str | None:
    result = _run(["id", "-u", user], timeout=3)
    if result["returncode"] != 0:
        return None
    return str(result["stdout"]).strip() or None


def _user_systemctl(user: str, *args: str) -> dict[str, Any]:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return {"returncode": 127, "stdout": "", "stderr": "systemctl not found", "timed_out": False}
    uid = _user_uid(user)
    env = os.environ.copy()
    if uid:
        env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
    if hasattr(os, "geteuid") and os.geteuid() == 0 and shutil.which("sudo"):
        argv = ["sudo", "-u", user]
        if uid:
            argv.append(f"XDG_RUNTIME_DIR=/run/user/{uid}")
        argv.extend([systemctl, "--user", *args])
    else:
        argv = [systemctl, "--user", *args]
    try:
        proc = subprocess.run(
            argv,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=8,
            check=False,
            env=env,
        )
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "timed_out": False,
        }
    except Exception as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc), "timed_out": False}


def read_reboot_required(root: Path = Path("/")) -> dict[str, Any]:
    marker = root / "var" / "run" / "reboot-required"
    pkgs = root / "var" / "run" / "reboot-required.pkgs"
    packages: list[str] = []
    if pkgs.exists():
        packages = [line.strip() for line in pkgs.read_text().splitlines() if line.strip()]
    return {"required": marker.exists(), "packages": packages}


def disk_summary(path: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(path)
    return {
        "path": str(path),
        "free_gb": round(usage.free / (1024**3), 2),
        "used_percent": round(usage.used / usage.total * 100, 1) if usage.total else 0,
    }


def memory_summary() -> dict[str, Any]:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return {}
    data: dict[str, int] = {}
    for line in meminfo.read_text(encoding="utf-8").splitlines():
        key, _, rest = line.partition(":")
        value = rest.strip().split()[0] if rest.strip() else "0"
        if value.isdigit():
            data[key] = int(value)
    return {
        "mem_total_mib": round(data.get("MemTotal", 0) / 1024),
        "mem_available_mib": round(data.get("MemAvailable", 0) / 1024),
        "swap_total_mib": round(data.get("SwapTotal", 0) / 1024),
        "swap_free_mib": round(data.get("SwapFree", 0) / 1024),
    }


def db_integrity(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "ok": False, "status": "missing"}
    try:
        conn = sqlite3.connect(path)
        status = conn.execute("pragma integrity_check").fetchone()[0]
        conn.close()
        return {"path": str(path), "ok": status == "ok", "status": status}
    except Exception as exc:
        return {"path": str(path), "ok": False, "status": str(exc)}


def collect_readiness(
    *,
    user: str = "hermes",
    units: tuple[str, ...] = DEFAULT_UNITS,
    disabled_units: tuple[str, ...] = DEFAULT_DISABLED_UNITS,
    dbs: tuple[str, ...] = DEFAULT_DBS,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema": 1,
        "user": user,
        "issues": [],
        "warnings": [],
        "ok": [],
    }
    report["reboot_required"] = read_reboot_required()
    if report["reboot_required"]["required"]:
        report["warnings"].append("OS reports a pending reboot")
    else:
        report["ok"].append("no reboot-required marker")

    linger = _run(["loginctl", "show-user", user, "-p", "Linger", "-p", "State"], timeout=5)
    report["linger"] = linger["stdout"]
    if "Linger=yes" in linger["stdout"]:
        report["ok"].append(f"{user} linger is enabled")
    else:
        report["issues"].append(f"{user} linger is not confirmed enabled")

    unit_state: dict[str, dict[str, Any]] = {}
    for unit in units:
        active = _user_systemctl(user, "is-active", unit)
        enabled = _user_systemctl(user, "is-enabled", unit)
        unit_state[unit] = {
            "active": active["stdout"] or active["stderr"],
            "enabled": enabled["stdout"] or enabled["stderr"],
        }
        if active["returncode"] == 0 and enabled["returncode"] == 0:
            report["ok"].append(f"{unit} active and enabled")
        else:
            report["issues"].append(f"{unit} is not active/enabled")
    for unit in disabled_units:
        active = _user_systemctl(user, "is-active", unit)
        enabled = _user_systemctl(user, "is-enabled", unit)
        unit_state[unit] = {
            "active": active["stdout"] or active["stderr"],
            "enabled": enabled["stdout"] or enabled["stderr"],
        }
        if str(unit_state[unit]["enabled"]).strip() == "disabled":
            report["ok"].append(f"{unit} remains disabled")
        else:
            report["warnings"].append(f"{unit} is not disabled")
    report["units"] = unit_state

    ss = _run(["ss", "-ltnp"], timeout=5)
    ports_text = ss["stdout"]
    report["ports"] = {
        "80": ":80 " in ports_text,
        "443": ":443 " in ports_text,
        "8765": "127.0.0.1:8765" in ports_text,
        "9119": "127.0.0.1:9119" in ports_text,
    }
    if report["ports"]["80"] and report["ports"]["443"] and report["ports"]["8765"] and report["ports"]["9119"]:
        report["ok"].append("expected gateway and dashboard ports are listening")
    else:
        report["issues"].append("port state does not match expected reboot baseline")

    report["disk"] = disk_summary(Path("/"))
    if report["disk"]["free_gb"] < 8:
        report["issues"].append("less than 8GB free before reboot/update")
    else:
        report["ok"].append("disk free is above 8GB")
    report["memory"] = memory_summary()

    report["dbs"] = [db_integrity(Path(path)) for path in dbs]
    bad_dbs = [item for item in report["dbs"] if not item["ok"]]
    if bad_dbs:
        report["issues"].append("one or more SQLite DB integrity checks failed")
    else:
        report["ok"].append("SQLite DB integrity checks passed")

    report["status"] = "block" if report["issues"] else ("warn" if report["warnings"] else "ready")
    return report


def format_report(report: dict[str, Any]) -> str:
    status = str(report.get("status", "unknown")).upper()
    lines = [f"Hermes VPS reboot readiness: {status}"]
    reboot = report.get("reboot_required") or {}
    packages = reboot.get("packages") or []
    lines.append(
        "Reboot: "
        + ("required" if reboot.get("required") else "not required")
        + (f" ({', '.join(packages)})" if packages else "")
    )
    disk = report.get("disk") or {}
    lines.append(f"Disk: {disk.get('free_gb', '?')}GB free, {disk.get('used_percent', '?')}% used")
    mem = report.get("memory") or {}
    if mem:
        lines.append(
            "Memory: "
            f"{mem.get('mem_available_mib', '?')}MiB available, "
            f"swap free {mem.get('swap_free_mib', '?')}MiB"
        )
    ports = report.get("ports") or {}
    if ports:
        lines.append(
            "Ports: "
            + ", ".join(f"{port}={'on' if active else 'off'}" for port, active in ports.items())
        )
    for title, key in (("Issues", "issues"), ("Warnings", "warnings"), ("OK", "ok")):
        values = report.get(key) or []
        if not values:
            continue
        lines.append("")
        lines.append(f"{title}:")
        lines.extend(f"- {item}" for item in values)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--user", default="hermes")
    args = parser.parse_args()

    report = collect_readiness(user=args.user)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_report(report))
    return 0 if report["status"] in {"ready", "warn"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
