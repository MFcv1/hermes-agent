"""Runtime observation payload helpers for Repo Cockpit.

Phase 2 local gateway contract.  The Repo Cockpit server remains the source of
truth for fingerprinting/dedup; gateway-side helpers only shape, truncate, and
redact payloads before emission.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

OBSERVATION_SCHEMA_VERSION = 2
RAW_EXCERPT_LIMIT = 4000
DEFAULT_OBSERVATION_SOURCE = "telegram_runtime_observer"
RUNTIME_OBSERVATION_PATH = "/api/internal/tasks/{task_id}/runtime-observations"

_GENERIC_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|bearer)\b\s*[:=]\s*([^\s'\"`]+)"
)
_SECRET_ENV_KEY_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password|credential)")


def _utc_iso_from_epoch(epoch: int | float | None = None) -> str:
    ts = time.time() if epoch is None else float(epoch)
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def mask_observation_secrets(text: Any, *, env: dict[str, str] | None = None) -> str:
    """Mask obvious secret material before it leaves the gateway process."""
    value = str(text or "")
    source_env = os.environ if env is None else env
    for key, secret in source_env.items():
        if not _SECRET_ENV_KEY_RE.search(str(key)):
            continue
        secret_value = str(secret or "")
        if len(secret_value) < 8:
            continue
        value = value.replace(secret_value, "<secret-hidden>")
    return _GENERIC_SECRET_RE.sub(lambda m: f"{m.group(1)}=<secret-hidden>", value)


def build_runtime_observation_v2(
    *,
    task_id: str,
    raw_excerpt: Any,
    source: str = DEFAULT_OBSERVATION_SOURCE,
    run_id: str | None = None,
    phase: str | None = None,
    command: str | None = None,
    severity: str = "medium",
    detected_at: str | None = None,
    fingerprint: str | None = None,
) -> dict[str, Any]:
    """Build one Observation payload v2 item following the normative contract."""
    clean_task_id = str(task_id or "").strip()
    if not clean_task_id:
        raise ValueError("runtime observation requires task_id")
    clean_excerpt = mask_observation_secrets(raw_excerpt)[:RAW_EXCERPT_LIMIT]
    payload: dict[str, Any] = {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "task_id": clean_task_id,
        "run_id": run_id,
        "source": str(source or DEFAULT_OBSERVATION_SOURCE),
        "phase": phase,
        "command": command,
        "severity": str(severity or "medium"),
        "raw_excerpt": clean_excerpt,
        "detected_at": detected_at or _utc_iso_from_epoch(),
    }
    if fingerprint:
        payload["fingerprint"] = str(fingerprint)
    return payload


def runtime_observations_from_watch_report(
    *,
    task_id: str,
    report: dict[str, Any],
    source: str = DEFAULT_OBSERVATION_SOURCE,
    detected_at: str | None = None,
) -> list[dict[str, Any]]:
    """Convert a gateway watch-log report into v2 observation payloads."""
    items = report.get("items") if isinstance(report, dict) else None
    if not isinstance(items, list):
        return []
    observations: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            raw = item.get("line") or item.get("raw_excerpt") or item.get("message") or ""
            severity = str(item.get("severity") or report.get("severity") or "medium")
            phase = item.get("phase")
            command = item.get("command")
        else:
            raw = str(item)
            severity = str(report.get("severity") or "medium")
            phase = None
            command = None
        if not str(raw).strip():
            continue
        observations.append(
            build_runtime_observation_v2(
                task_id=task_id,
                raw_excerpt=raw,
                source=source,
                phase=phase,
                command=command,
                severity=severity,
                detected_at=detected_at,
            )
        )
    return observations


def build_legacy_runtime_observation_payload(
    *,
    task_id: str,
    report: dict[str, Any],
    source: str = DEFAULT_OBSERVATION_SOURCE,
    captured_at: int | None = None,
) -> dict[str, Any]:
    """Build the exact v1 payload shape that current Cockpit servers accept."""
    clean_task_id = str(task_id or "").strip()
    if not clean_task_id:
        raise ValueError("runtime observation requires task_id")
    return {
        "source": str(source or DEFAULT_OBSERVATION_SOURCE),
        "task_id": clean_task_id,
        "report": _mask_report(report),
        "captured_at": int(time.time() if captured_at is None else captured_at),
    }


def post_runtime_observations(
    api_sync: Callable[[str, str, dict[str, Any] | None, int], dict[str, Any]],
    *,
    task_id: str,
    report: dict[str, Any],
    timeout: int = 10,
    prefer_v2: bool = False,
) -> dict[str, Any]:
    """Post runtime observations through an existing Repo Cockpit API caller.

    ``prefer_v2`` is intentionally opt-in until Repo Cockpit backend migration is
    available in this checkout.  The default preserves the current v1 wire shape.
    """
    clean_task_id = str(task_id or "").strip()
    if not clean_task_id:
        raise ValueError("runtime observation requires task_id")
    path = RUNTIME_OBSERVATION_PATH.format(task_id=clean_task_id)
    if prefer_v2:
        observations = runtime_observations_from_watch_report(task_id=clean_task_id, report=report)
        results = [api_sync("POST", path, observation, timeout) for observation in observations]
        return {"ok": all(result.get("ok", True) for result in results), "results": results}
    payload = build_legacy_runtime_observation_payload(task_id=clean_task_id, report=report)
    return api_sync("POST", path, payload, timeout)


def _mask_report(report: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {}
    masked: dict[str, Any] = {}
    for key, value in report.items():
        if isinstance(value, str):
            masked[key] = mask_observation_secrets(value)
        elif isinstance(value, list):
            masked[key] = [_mask_report(item) if isinstance(item, dict) else mask_observation_secrets(item) for item in value]
        elif isinstance(value, dict):
            masked[key] = _mask_report(value)
        else:
            masked[key] = value
    return masked
