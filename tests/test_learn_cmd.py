"""Tests for /learn shared command handler."""

from hermes_cli.learn_cmd import handle_learn_command, build_learn_agent_seed
from hermes_cli.commands import resolve_command, is_gateway_known_command


def test_learn_command_registered():
    cmd = resolve_command("learn")
    assert cmd is not None
    assert cmd.name == "learn"
    assert is_gateway_known_command("learn")


def test_learn_requires_args():
    r = handle_learn_command("", surface="gateway")
    assert r.agent_seed is None
    assert "Usage" in r.text


def test_learn_returns_agent_seed():
    brief = "Cloudflare Pages Workers D1 R2 deployment"
    r = handle_learn_command(brief, surface="gateway")
    assert r.agent_seed is not None
    assert brief in r.agent_seed
    assert "skill_manage" in r.agent_seed
    assert "Learn" in r.text or "learn" in r.text.lower()


def test_build_learn_seed_includes_sections():
    seed = build_learn_agent_seed("topic X")
    assert "When to Use" in seed
    assert "topic X" in seed