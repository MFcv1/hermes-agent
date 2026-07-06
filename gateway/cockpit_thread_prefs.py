"""Pending per-topic model/reasoning prefs set during Repo Cockpit /new flow.

When a user picks model + reasoning before selecting a repo, the Telegram
adapter stores prefs keyed by (platform, chat_id, thread_id). The gateway
consumes them on the first message in that topic and applies session-scoped
/model and /reasoning overrides.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional, Tuple

_LOCK = threading.Lock()
_PENDING: Dict[str, Dict[str, Any]] = {}


def _key(platform: str, chat_id: str, thread_id: str) -> str:
    return f"{platform}:{chat_id}:{thread_id}"


def set_pending(
    *,
    platform: str,
    chat_id: str,
    thread_id: str,
    model: str,
    provider: str,
    reasoning_effort: Optional[str] = None,
) -> None:
    if not platform or not chat_id or not thread_id or not model:
        return
    entry: Dict[str, Any] = {
        "model": str(model),
        "provider": str(provider or ""),
    }
    if reasoning_effort:
        entry["reasoning_effort"] = str(reasoning_effort)
    with _LOCK:
        _PENDING[_key(platform, str(chat_id), str(thread_id))] = entry


def pop_pending(
    *,
    platform: str,
    chat_id: str,
    thread_id: str,
) -> Optional[Dict[str, Any]]:
    if not platform or not chat_id or not thread_id:
        return None
    k = _key(platform, str(chat_id), str(thread_id))
    with _LOCK:
        return _PENDING.pop(k, None)


def peek_pending(
    *,
    platform: str,
    chat_id: str,
    thread_id: str,
) -> Optional[Dict[str, Any]]:
    if not platform or not chat_id or not thread_id:
        return None
    k = _key(platform, str(chat_id), str(thread_id))
    with _LOCK:
        val = _PENDING.get(k)
        return dict(val) if isinstance(val, dict) else None