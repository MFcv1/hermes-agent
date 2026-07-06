"""Repo Cockpit HTTP client used by gateway integrations.

Small synchronous core by design: Telegram handlers can call it through
``asyncio.to_thread`` without pulling HTTP details into platform adapters.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

LOCAL_COCKPIT_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_COCKPIT_WEBAPP_URL = (
    "https://cockpit.134.122.73.242.sslip.io/?v=20260620-immediate-close"
)


@dataclass(frozen=True)
class RepoCockpitClient:
    """Tiny Repo Cockpit HTTP client for gateway-side calls."""

    base_url: str = LOCAL_COCKPIT_BASE_URL

    def api_sync(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout: int = 20,
    ) -> dict[str, Any]:
        """Call Repo Cockpit and preserve legacy Telegram error shapes."""
        url = self.base_url.rstrip("/") + "/" + path.lstrip("/")
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            with urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return {
                "ok": False,
                "error_code": exc.code,
                "description": exc.read().decode("utf-8", "replace")[:1200],
            }
        except Exception as exc:
            return {"ok": False, "description": str(exc)}


def cockpit_webapp_url(path: str = "/", **params: str | None) -> str:
    """Build the public Repo Cockpit WebApp URL with cache busting."""
    base = os.getenv("REPO_COCKPIT_URL", DEFAULT_COCKPIT_WEBAPP_URL)
    parsed = urllib.parse.urlsplit(base)
    clean_path = "/" + path.lstrip("/")
    existing = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    existing.update({key: value for key, value in params.items() if value is not None})
    existing["v"] = str(int(time.time()))
    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            clean_path,
            urllib.parse.urlencode(existing),
            parsed.fragment,
        )
    )
