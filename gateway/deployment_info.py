"""Deployment metadata for health endpoints.

Kept in a small module so platform adapters can expose deploy state without
embedding git/process probing logic in monolithic gateway files.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

PROCESS_STARTED_AT = datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def current_git_sha() -> str:
    """Return the current source checkout SHA, or ``unknown``.

    Health must never fail because git metadata is absent in a packaged install.
    ``HERMES_GIT_SHA`` lets service managers pin the deployed SHA explicitly.
    """
    explicit = os.getenv("HERMES_GIT_SHA", "").strip()
    if explicit:
        return explicit
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(_repo_root()),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
    except Exception:
        return "unknown"
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and sha else "unknown"


def deployment_health_fields() -> dict[str, Any]:
    """Fields every Hermes health endpoint should expose for rollout checks."""
    return {"git_sha": current_git_sha(), "started_at": PROCESS_STARTED_AT}
