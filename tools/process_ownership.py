"""Ownership markers and verified cleanup for local command process trees."""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

OWNER_ENV = "HERMES_PROCESS_OWNER"


def new_owner_id() -> str:
    return f"hpo_{uuid.uuid4().hex}"


def _owned_processes(owner_id: str) -> list[Any]:
    if not owner_id:
        return []
    try:
        import psutil
    except ImportError:
        return []
    owned = []
    for proc in psutil.process_iter(["pid"]):
        if proc.pid == os.getpid():
            continue
        try:
            if proc.environ().get(OWNER_ENV) == owner_id:
                owned.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return owned


def ownership_snapshot(owner_id: str) -> dict[str, Any]:
    """Return live owned PIDs, cwd values and listening ports."""
    pids: list[int] = []
    cwds: dict[int, str] = {}
    ports: set[int] = set()
    for proc in _owned_processes(owner_id):
        try:
            pids.append(proc.pid)
            try:
                cwds[proc.pid] = proc.cwd()
            except Exception:
                pass
            try:
                for conn in proc.net_connections(kind="inet"):
                    if conn.status == "LISTEN" and conn.laddr:
                        ports.add(int(conn.laddr.port))
            except Exception:
                pass
        except Exception:
            continue
    return {"pids": sorted(pids), "cwds": cwds, "ports": sorted(ports)}


def terminate_owned_processes(
    owner_id: str,
    *,
    grace_seconds: float = 1.0,
    discovery_seconds: float = 0.0,
) -> dict[str, Any]:
    """Terminate every process carrying ``owner_id``, including reparented forks.

    The random inherited environment marker survives ``fork``, ``setsid`` and
    double-fork daemonization, unlike PPID/PGID traversal. Cleanup verifies the
    marker again before every signal and reports remaining PIDs/cwds/ports.
    """
    try:
        import psutil
    except ImportError:
        return {"pids": [], "cwds": {}, "ports": [], "verified": False}

    # A successful shell wrapper can exit a few milliseconds before a
    # double-fork child becomes visible in the process table.  Natural-exit
    # callers opt into a short discovery window to close that race; explicit
    # timeout/stop paths already have a live target and stay immediate.
    targets = _owned_processes(owner_id)
    discovery_deadline = time.monotonic() + max(0.0, discovery_seconds)
    while not targets and time.monotonic() < discovery_deadline:
        time.sleep(0.01)
        targets = _owned_processes(owner_id)
    for proc in reversed(targets):
        try:
            if proc.environ().get(OWNER_ENV) == owner_id:
                proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            pass

    deadline = time.monotonic() + max(0.0, grace_seconds)
    quiet_since: float | None = None
    while time.monotonic() < deadline:
        current = _owned_processes(owner_id)
        if current:
            quiet_since = None
            # A child can fork after the first snapshot but before handling
            # SIGTERM. Re-signal every newly discovered owner member.
            for proc in reversed(current):
                try:
                    if proc.environ().get(OWNER_ENV) == owner_id:
                        proc.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                    pass
        else:
            now = time.monotonic()
            if quiet_since is None:
                quiet_since = now
            if now - quiet_since >= max(0.0, discovery_seconds):
                break
        time.sleep(0.01)

    for proc in _owned_processes(owner_id):
        try:
            if proc.environ().get(OWNER_ENV) == owner_id:
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            pass

    deadline = time.monotonic() + 2.0
    snapshot = ownership_snapshot(owner_id)
    while snapshot["pids"] and time.monotonic() < deadline:
        time.sleep(0.05)
        snapshot = ownership_snapshot(owner_id)
    snapshot["verified"] = not snapshot["pids"] and not snapshot["ports"]
    if snapshot["verified"]:
        logger.info("Verified process-owner cleanup: owner=%s", owner_id)
    else:
        logger.warning("Incomplete process-owner cleanup: owner=%s snapshot=%s", owner_id, snapshot)
    return snapshot


__all__ = [
    "OWNER_ENV",
    "new_owner_id",
    "ownership_snapshot",
    "terminate_owned_processes",
]
