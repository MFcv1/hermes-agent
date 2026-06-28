from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "cron" / "scripts" / "github_release_watch.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("github_release_watch", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _release(release_id, tag, *, prerelease=False):
    return {
        "id": release_id,
        "tag_name": tag,
        "name": tag,
        "html_url": f"https://github.com/acme/project/releases/tag/{tag}",
        "published_at": "2026-06-27T10:00:00Z",
        "prerelease": prerelease,
    }


def test_first_run_baselines_and_stays_silent(tmp_path, monkeypatch):
    mod = _load_script()
    monkeypatch.setenv("WATCHER_STATE_DIR", str(tmp_path))

    output = mod.run_once(
        "acme/project",
        fetch_releases=lambda _repo, _per_page, _timeout: [_release(1, "v1.0.0")],
    )

    assert output == ""
    assert (tmp_path / "github-releases-acme-project.json").is_file()


def test_second_run_emits_only_new_release(tmp_path, monkeypatch):
    mod = _load_script()
    monkeypatch.setenv("WATCHER_STATE_DIR", str(tmp_path))

    mod.run_once(
        "acme/project",
        fetch_releases=lambda _repo, _per_page, _timeout: [_release(1, "v1.0.0")],
    )
    output = mod.run_once(
        "acme/project",
        fetch_releases=lambda _repo, _per_page, _timeout: [
            _release(2, "v1.1.0"),
            _release(1, "v1.0.0"),
        ],
    )

    assert "New GitHub release: acme/project v1.1.0" in output
    assert "v1.0.0" not in output


def test_prereleases_are_filtered_by_default(tmp_path, monkeypatch):
    mod = _load_script()
    monkeypatch.setenv("WATCHER_STATE_DIR", str(tmp_path))

    mod.run_once(
        "acme/project",
        fetch_releases=lambda _repo, _per_page, _timeout: [_release(1, "v1.0.0")],
    )
    output = mod.run_once(
        "acme/project",
        fetch_releases=lambda _repo, _per_page, _timeout: [
            _release(2, "v2.0.0-beta", prerelease=True),
            _release(1, "v1.0.0"),
        ],
    )

    assert output == ""


def test_include_prereleases_allows_prerelease_alert(tmp_path, monkeypatch):
    mod = _load_script()
    monkeypatch.setenv("WATCHER_STATE_DIR", str(tmp_path))

    mod.run_once(
        "acme/project",
        include_prereleases=True,
        fetch_releases=lambda _repo, _per_page, _timeout: [_release(1, "v1.0.0")],
    )
    output = mod.run_once(
        "acme/project",
        include_prereleases=True,
        fetch_releases=lambda _repo, _per_page, _timeout: [
            _release(2, "v2.0.0-beta", prerelease=True),
            _release(1, "v1.0.0"),
        ],
    )

    assert "v2.0.0-beta" in output
    assert "**Prerelease:** yes" in output
