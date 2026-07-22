"""Shared dashboard/Mini App URL helpers for gateway surfaces."""

from __future__ import annotations

import time
import urllib.parse


def build_url(base: str, path: str = "/", **params: str | None) -> str:
    """Build a cache-busted URL under ``base`` without losing existing query."""
    parsed = urllib.parse.urlsplit(base.rstrip("/"))
    prefix = parsed.path.rstrip("/")
    clean_path = f"{prefix}/{path.lstrip('/')}" if prefix else "/" + path.lstrip("/")
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


def hermes_mini_app_url(path: str = "/work-sessions", **params: str | None) -> str:
    """Return the operator-configured Hermes Mini App URL."""
    from hermes_cli.dashboard_auth.prefix import resolve_public_url

    public_url = resolve_public_url()
    if not public_url:
        return ""
    return build_url(public_url, path, **params)


def hermes_dashboard_url(path: str = "/sessions", **params: str | None) -> str:
    """Return the operator-configured full browser dashboard URL.

    An empty result tells the caller to offer private access instructions
    instead of linking to an unrelated service.
    """
    from hermes_cli.dashboard_auth.prefix import resolve_public_url

    public_url = resolve_public_url()
    if not public_url:
        return ""
    return build_url(public_url, path, **params)
