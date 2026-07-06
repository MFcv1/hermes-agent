"""Tests for /myskills command formatting."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def hermes_home(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    (skills / "devops" / "demo-skill").mkdir(parents=True)
    (skills / "devops" / "demo-skill" / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Demo for tests.\n---\n# Demo\n",
        encoding="utf-8",
    )
    (skills / ".bundled_manifest").write_text("bundled-only:abc\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def test_myskills_lists_personal_skill(hermes_home):
    from hermes_cli.myskills_cmd import handle_myskills_command

    out = handle_myskills_command(surface="gateway")
    assert "demo-skill" in out
    assert "/demo-skill" in out
    assert "Demo for tests" in out


def test_myskills_empty_when_only_bundled(hermes_home):
    import shutil
    from hermes_cli.myskills_cmd import handle_myskills_command

    shutil.rmtree(hermes_home / "skills" / "devops" / "demo-skill")
    (hermes_home / "skills" / "bundled-only").mkdir()
    (hermes_home / "skills" / "bundled-only" / "SKILL.md").write_text(
        "---\nname: bundled-only\ndescription: Built-in.\n---\n",
        encoding="utf-8",
    )
    out = handle_myskills_command(surface="gateway")
    assert "Aucun skill perso" in out or "personal" in out.lower() or "perso" in out