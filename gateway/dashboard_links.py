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
    """Return the Hermes dashboard-backed Mini App URL.

    Production should set ``dashboard.public_url`` on the VPS.  Until then,
    fall back to the legacy Repo Cockpit webapp URL so Telegram keeps offering
    a usable button during migration.
    """
    from gateway.repo_cockpit_client import cockpit_webapp_url
    from hermes_cli.dashboard_auth.prefix import resolve_public_url

    public_url = resolve_public_url()
    if public_url:
        return build_url(public_url, path, **params)
    return cockpit_webapp_url(path, **params)


def hermes_dashboard_url(path: str = "/sessions", **params: str | None) -> str:
    """Return the operator-configured full browser dashboard URL.

    Unlike :func:`hermes_mini_app_url`, this helper deliberately has no
    Repo Cockpit fallback: ``/dashboard`` must never present the Mini App as
    if it were the full VPS dashboard.  An empty result tells the caller to
    offer the private SSH-tunnel instructions instead.
    """
    from hermes_cli.dashboard_auth.prefix import resolve_public_url

    public_url = resolve_public_url()
    if not public_url:
        return ""
    return build_url(public_url, path, **params)
