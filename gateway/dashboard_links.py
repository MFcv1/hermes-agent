"""URLs shared by Telegram and the private Hermes dashboard."""

from __future__ import annotations

import time
import urllib.parse


def hermes_mini_app_url(path: str = "/sessions") -> str:
    """Build a cache-busted Mini App URL from the configured dashboard URL.

    The public URL is an operator-declared Tailnet/tunnel URL, never a guessed
    network address. Callers can surface a clear fallback when it is absent.
    """
    from hermes_cli.dashboard_auth.prefix import resolve_public_url

    public_url = resolve_public_url()
    if not public_url:
        return ""
    parsed = urllib.parse.urlsplit(public_url.rstrip("/"))
    prefix = parsed.path.rstrip("/")
    clean_path = f"{prefix}/{path.lstrip('/')}" if prefix else f"/{path.lstrip('/')}"
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query["v"] = str(int(time.time()))
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, clean_path, urllib.parse.urlencode(query), parsed.fragment)
    )
