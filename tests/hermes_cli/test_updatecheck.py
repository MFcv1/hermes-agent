"""Tests for the read-only updatecheck report."""

from pathlib import Path
from types import SimpleNamespace

from hermes_cli import updatecheck


def test_status_counts_classifies_dirty_entries():
    counts = updatecheck._status_counts(" M file.py\n?? new.py\n D old.py\nR  a -> b\n")

    assert counts["modified"] == 1
    assert counts["untracked"] == 1
    assert counts["deleted"] == 1
    assert counts["renamed"] == 1


def test_collect_updatecheck_shallow_differs_without_exact_count(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    repo.mkdir()
    home.mkdir()
    (repo / ".git").mkdir()

    calls = []

    def fake_git(_repo: Path, *args: str, timeout: float = 8):
        calls.append(args)
        if args == ("rev-parse", "HEAD"):
            return updatecheck.CommandResult(0, "local-sha\n", "")
        if args == ("rev-parse", "--abbrev-ref", "HEAD"):
            return updatecheck.CommandResult(0, "main\n", "")
        if args == ("rev-parse", "origin/main"):
            return updatecheck.CommandResult(0, "remote-sha\n", "")
        if args == ("rev-parse", "--is-shallow-repository"):
            return updatecheck.CommandResult(0, "true\n", "")
        if args == ("status", "--porcelain=v1"):
            return updatecheck.CommandResult(0, "", "")
        if args == ("ls-remote", "--tags", "--refs", "origin", "v*"):
            return updatecheck.CommandResult(
                0,
                "aaa refs/tags/v2026.6.5\nbbb refs/tags/v2026.6.19\n",
                "",
            )
        if args == ("remote", "get-url", "origin"):
            return updatecheck.CommandResult(0, "https://github.com/NousResearch/hermes-agent.git\n", "")
        if args[:2] == ("rev-list", "--count"):
            raise AssertionError("shallow updatecheck must not count across boundary")
        raise AssertionError(f"unexpected git args: {args!r}")

    monkeypatch.setattr(updatecheck, "_git", fake_git)

    report = updatecheck.collect_updatecheck(
        project_root=repo,
        hermes_home=home,
        fresh=False,
    )

    assert report["status"] == "yellow"
    assert report["update_available"] is True
    assert report.get("behind_count") is None
    assert report["latest_release"]["tag"] == "v2026.6.19"
    assert report["latest_release"]["url"] == (
        "https://github.com/NousResearch/hermes-agent/releases/tag/v2026.6.19"
    )
    assert any("shallow checkout" in warning for warning in report["warnings"])


def test_latest_release_parses_stable_tags_and_ignores_noise(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()

    def fake_git(_repo: Path, *args: str, timeout: float = 8):
        if args == ("ls-remote", "--tags", "--refs", "origin", "v*"):
            return updatecheck.CommandResult(
                0,
                "\n".join(
                    [
                        "aaa refs/tags/v2026.6.5",
                        "bbb refs/tags/v2026.6.19",
                        "ccc refs/tags/v2026.7.1",
                        "ddd refs/tags/vNext",
                    ]
                )
                + "\n",
                "",
            )
        if args == ("remote", "get-url", "origin"):
            return updatecheck.CommandResult(0, "git@github.com:NousResearch/hermes-agent.git\n", "")
        raise AssertionError(f"unexpected git args: {args!r}")

    monkeypatch.setattr(updatecheck, "_git", fake_git)

    release = updatecheck._collect_latest_release(repo, timeout=1)

    assert release["available"] is True
    assert release["tag"] == "v2026.7.1"
    assert release["stable_tag_count"] == 3
    assert release["url"] == "https://github.com/NousResearch/hermes-agent/releases/tag/v2026.7.1"


def test_notification_signature_tracks_latest_release():
    old_report = {
        "status": "green",
        "update_available": False,
        "latest_release": {"tag": "v2026.6.19"},
        "issues": [],
        "warnings": [],
    }
    new_report = {**old_report, "latest_release": {"tag": "v2026.7.1"}}
    previous = {"signature": updatecheck._notification_signature(old_report)}

    decision = updatecheck.evaluate_notification(new_report, previous)

    assert decision["should_notify"] is True
    assert decision["reason"] == "status_changed"


def test_find_non_owner_sample_does_not_follow_symlink(tmp_path):
    target = tmp_path / "target"
    target.write_text("outside owner is irrelevant")
    link = tmp_path / "link"
    link.symlink_to(target)

    assert updatecheck._find_non_owner_sample(tmp_path, tmp_path.stat().st_uid) == []


def test_format_updatecheck_includes_cache_and_dirty_summary():
    text = updatecheck.format_updatecheck(
        {
            "status": "red",
            "version": "0.1.0",
            "project_root": "/tmp/hermes",
            "head": "abc123456789",
            "origin_main": "def123456789",
            "update_available": True,
            "latest_release": {
                "tag": "v2026.6.19",
                "url": "https://github.com/NousResearch/hermes-agent/releases/tag/v2026.6.19",
            },
            "cache": {"exists": True, "behind": 0, "age_human": "6h"},
            "worktree": {
                "clean": False,
                "counts": {"modified": 2, "untracked": 3, "deleted": 0},
            },
            "disk": {"free_gb": 9.0, "used_percent": 62.0},
            "issues": ["working tree has local changes/untracked files"],
            "warnings": [],
            "ok": [],
        }
    )

    assert "Hermes updatecheck: RED" in text
    assert "Release: v2026.6.19 (https://github.com/NousResearch/hermes-agent/releases/tag/v2026.6.19)" in text
    assert "Cache: behind=0 age=6h" in text
    assert "Worktree: dirty (modified=2, untracked=3, deleted=0)" in text


def test_format_updatecheck_hides_unavailable_user_systemd_bus():
    text = updatecheck.format_updatecheck(
        {
            "status": "yellow",
            "version": "0.1.0",
            "project_root": "/tmp/hermes",
            "cache": {"exists": False},
            "worktree": {"clean": True, "counts": {}},
            "services": {
                "units": {
                    "hermes-gateway.service": {
                        "state": "Failed to connect to bus: No medium found"
                    }
                },
                "ports": {"8765": True, "8789": False},
            },
            "issues": [],
            "warnings": ["disk headroom is modest"],
            "ok": [],
        }
    )

    assert "No medium found" not in text
    assert "Services: port8765=on, port8789=off" in text


def test_evaluate_notification_silences_unchanged_green():
    report = {
        "status": "green",
        "update_available": False,
        "issues": [],
        "warnings": [],
    }
    previous = {"signature": updatecheck._notification_signature(report)}

    decision = updatecheck.evaluate_notification(report, previous)

    assert decision["should_notify"] is False
    assert decision["reason"] == "unchanged_green"


def test_evaluate_notification_keeps_red_visible_even_unchanged():
    report = {
        "status": "red",
        "update_available": True,
        "issues": ["working tree has local changes/untracked files"],
        "warnings": [],
    }
    previous = {"signature": updatecheck._notification_signature(report)}

    decision = updatecheck.evaluate_notification(report, previous)

    assert decision["should_notify"] is True
    assert decision["reason"] == "red"


def test_run_updatecheck_silent_unchanged_writes_state(tmp_path, monkeypatch, capsys):
    report = {
        "status": "green",
        "version": "1.0.0",
        "project_root": str(tmp_path / "repo"),
        "hermes_home": str(tmp_path / "home"),
        "head": "abc",
        "origin_main": "abc",
        "update_available": False,
        "issues": [],
        "warnings": [],
        "ok": ["HEAD matches origin/main"],
    }
    state_path = tmp_path / "last.json"
    state_path.write_text(
        '{"signature":{"issues":[],"latest_release":null,"status":"green","update_available":false,"warnings":[]}}\n'
    )
    monkeypatch.setattr(updatecheck, "collect_updatecheck", lambda **_: dict(report))

    rc = updatecheck.run_updatecheck(
        SimpleNamespace(
            cached=True,
            json=False,
            timeout=20,
            stateful=False,
            silent_unchanged=True,
            state_path=str(state_path),
        )
    )

    assert rc == 0
    assert capsys.readouterr().out.strip() == "[SILENT]"
    saved = updatecheck._load_state(state_path)
    assert saved is not None
    assert saved["signature"]["status"] == "green"
