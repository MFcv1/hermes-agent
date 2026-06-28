#!/usr/bin/env python3
"""No-agent GitHub release watcher for Hermes cron jobs.

First run records the current release ids and stays silent. Later runs emit a
short Markdown alert only for newly observed releases.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable


FetchFn = Callable[[str, int, int], list[dict[str, Any]]]


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()


def _state_dir() -> Path:
    return Path(os.environ.get("WATCHER_STATE_DIR") or _hermes_home() / "watcher-state")


def _repo_slug(repo: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", repo.strip()).strip("-").lower()


def _state_path(repo: str, name: str | None = None) -> Path:
    stem = _repo_slug(name or f"github-releases-{repo}")
    return _state_dir() / f"{stem}.json"


def _load_seen(path: Path) -> set[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return set()
    return {str(item) for item in data.get("seen_ids", [])}


def _save_seen(path: Path, seen_ids: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"seen_ids": sorted({str(item) for item in seen_ids})}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Hermes-GitHub-Release-Watcher",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fetch_releases(repo: str, per_page: int, timeout: int) -> list[dict[str, Any]]:
    url = f"https://api.github.com/repos/{repo}/releases?per_page={per_page}"
    req = urllib.request.Request(url, headers=_github_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"GitHub API HTTP {exc.code} for {repo}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GitHub API request failed for {repo}: {exc}") from exc

    data = json.loads(body)
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected GitHub releases response for {repo}")
    return data


def _release_id(release: dict[str, Any]) -> str:
    return str(release.get("id") or release.get("html_url") or release.get("tag_name") or "")


def _format_release(repo: str, release: dict[str, Any]) -> str:
    tag = str(release.get("tag_name") or release.get("name") or "release")
    name = str(release.get("name") or tag)
    url = str(release.get("html_url") or f"https://github.com/{repo}/releases")
    published = str(release.get("published_at") or release.get("created_at") or "unknown")
    prerelease = "yes" if release.get("prerelease") else "no"
    return (
        f"## New GitHub release: {repo} {tag}\n\n"
        f"**Name:** {name}\n"
        f"**Published:** {published}\n"
        f"**Prerelease:** {prerelease}\n"
        f"**URL:** {url}"
    )


def run_once(
    repo: str,
    *,
    include_prereleases: bool = False,
    max_items: int = 5,
    per_page: int = 20,
    timeout: int = 20,
    name: str | None = None,
    fetch_releases: FetchFn = _fetch_releases,
) -> str:
    releases = fetch_releases(repo, per_page, timeout)
    filtered = [
        release for release in releases
        if include_prereleases or not release.get("prerelease")
    ]
    current_ids = {_release_id(release) for release in filtered if _release_id(release)}
    state_path = _state_path(repo, name)
    seen_ids = _load_seen(state_path)

    if not seen_ids:
        _save_seen(state_path, current_ids)
        return ""

    new_releases = [
        release for release in filtered
        if _release_id(release) and _release_id(release) not in seen_ids
    ][:max_items]
    _save_seen(state_path, seen_ids | current_ids)

    if not new_releases:
        return ""
    return "\n\n---\n\n".join(_format_release(repo, release) for release in new_releases)


def _bool_arg(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Watch GitHub releases and print only new ones.")
    parser.add_argument("--repo", required=True, help="GitHub repository as owner/name")
    parser.add_argument("--include-prereleases", type=_bool_arg, default=False)
    parser.add_argument("--max", dest="max_items", type=int, default=5)
    parser.add_argument("--per-page", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--name", help="Optional state bucket name")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if "/" not in args.repo.strip("/"):
        print("--repo must be in owner/name form", file=sys.stderr)
        return 2
    output = run_once(
        args.repo.strip("/"),
        include_prereleases=args.include_prereleases,
        max_items=max(1, args.max_items),
        per_page=max(1, min(args.per_page, 100)),
        timeout=max(1, args.timeout),
        name=args.name,
    )
    if output:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
