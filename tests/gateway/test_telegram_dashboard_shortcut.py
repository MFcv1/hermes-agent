from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.platforms import telegram as telegram_mod


class _Button:
    def __init__(self, text, **kwargs):
        self.text = text
        self.kwargs = kwargs


class _Markup:
    def __init__(self, rows):
        self.rows = rows


@pytest.mark.asyncio
async def test_dashboard_shortcut_uses_normal_browser_url_not_web_app(monkeypatch):
    monkeypatch.setenv(
        "HERMES_DASHBOARD_PUBLIC_URL",
        "https://hermes-vps.tail59f02f.ts.net",
    )
    monkeypatch.setattr("gateway.dashboard_links.time.time", lambda: 1234)
    monkeypatch.setattr(telegram_mod, "InlineKeyboardButton", _Button)
    monkeypatch.setattr(telegram_mod, "InlineKeyboardMarkup", _Markup)

    adapter = object.__new__(telegram_mod.TelegramAdapter)
    adapter._link_preview_kwargs = lambda: {}
    msg = SimpleNamespace(reply_text=AsyncMock())

    await adapter._send_dashboard_shortcut(msg)

    kwargs = msg.reply_text.await_args.kwargs
    button = kwargs["reply_markup"].rows[0][0]
    assert button.kwargs == {
        "url": "https://hermes-vps.tail59f02f.ts.net/sessions?v=1234"
    }
    assert "web_app" not in button.kwargs
    assert "Lien copiable" in msg.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_dashboard_shortcut_falls_back_to_private_tunnel(monkeypatch):
    monkeypatch.delenv("HERMES_DASHBOARD_PUBLIC_URL", raising=False)

    adapter = object.__new__(telegram_mod.TelegramAdapter)
    adapter._link_preview_kwargs = lambda: {}
    msg = SimpleNamespace(reply_text=AsyncMock())

    await adapter._send_dashboard_shortcut(msg)

    text = msg.reply_text.await_args.args[0]
    assert "Aucune URL web publique" in text
    assert "http://127.0.0.1:9120/sessions" in text
