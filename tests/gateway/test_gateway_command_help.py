"""Gateway command help rendering tests."""

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


def _make_event(text: str, platform: Platform) -> MessageEvent:
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform=platform,
            chat_id="chat-1",
            user_id="user-1",
            user_name="tester",
            chat_type="dm",
        ),
    )


def _make_runner():
    from gateway.run import GatewayRunner

    return object.__new__(GatewayRunner)


def test_start_is_known_gateway_command():
    """Telegram sends /start automatically; gateway should intercept it as a no-op."""
    from hermes_cli.commands import GATEWAY_KNOWN_COMMANDS, resolve_command

    cmd = resolve_command("start")
    assert "start" in GATEWAY_KNOWN_COMMANDS
    assert cmd is not None
    assert cmd.name == "start"


@pytest.mark.asyncio
async def test_app_command_requires_dashboard_public_url(monkeypatch):
    monkeypatch.delenv("HERMES_DASHBOARD_PUBLIC_URL", raising=False)

    result = await _make_runner()._handle_app_command(
        _make_event("/app", Platform.TELEGRAM)
    )

    assert "Mini App indisponible" in result
    assert "dashboard.public_url" in result


@pytest.mark.asyncio
async def test_app_command_prefers_dashboard_public_url(monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_PUBLIC_URL", "https://hermes.tailnet.ts.net")

    result = await _make_runner()._handle_app_command(
        _make_event("/app", Platform.TELEGRAM)
    )

    assert "Mini App Hermes" in result
    assert "https://hermes.tailnet.ts.net/work-sessions?" in result


@pytest.mark.asyncio
async def test_dashboard_command_uses_configured_public_url(monkeypatch):
    monkeypatch.setattr("gateway.dashboard_links.time.time", lambda: 1234)
    monkeypatch.setenv("HERMES_DASHBOARD_PUBLIC_URL", "https://hermes.example.com")

    result = await _make_runner()._handle_dashboard_command(
        _make_event("/dashboard", Platform.TELEGRAM)
    )

    assert "Dashboard Hermes" in result
    assert "https://hermes.example.com/sessions?v=1234" in result
    assert "projet courant restent les mêmes" in result


@pytest.mark.asyncio
async def test_dashboard_command_falls_back_to_private_tunnel_hint(monkeypatch):
    monkeypatch.delenv("HERMES_DASHBOARD_PUBLIC_URL", raising=False)

    result = await _make_runner()._handle_dashboard_command(
        _make_event("/dashboard", Platform.TELEGRAM)
    )

    assert "mode privé" in result
    assert "http://127.0.0.1:9120/sessions" in result


@pytest.mark.asyncio
async def test_help_sanitizes_slash_command_mentions_for_telegram(monkeypatch):
    """Telegram help output must not expose invalid uppercase/hyphenated slashes."""
    monkeypatch.setattr(
        "agent.skill_commands.get_skill_commands",
        lambda: {
            "/Linear": {"description": "Open Linear"},
            "/Custom-Thing": {"description": "Run a custom thing"},
        },
    )

    result = await _make_runner()._handle_help_command(
        _make_event("/help", Platform.TELEGRAM)
    )

    assert "`/linear`" in result
    assert "`/custom_thing`" in result
    assert "`/Linear`" not in result
    assert "`/Custom-Thing`" not in result


@pytest.mark.asyncio
async def test_commands_sanitizes_slash_command_mentions_for_telegram(monkeypatch):
    """Paginated Telegram /commands output uses Telegram-valid slash mentions."""
    monkeypatch.setattr(
        "agent.skill_commands.get_skill_commands",
        lambda: {"/Linear": {"description": "Open Linear"}},
    )

    result = await _make_runner()._handle_commands_command(
        _make_event("/commands 999", Platform.TELEGRAM)
    )

    assert "`/linear`" in result
    assert "`/Linear`" not in result


@pytest.mark.asyncio
async def test_help_keeps_non_telegram_slash_command_mentions_unchanged(monkeypatch):
    """Only Telegram needs slash mentions rewritten to Telegram command names."""
    monkeypatch.setattr(
        "agent.skill_commands.get_skill_commands",
        lambda: {"/Linear": {"description": "Open Linear"}},
    )

    result = await _make_runner()._handle_help_command(
        _make_event("/help", Platform.DISCORD)
    )

    assert "`/Linear`" in result
