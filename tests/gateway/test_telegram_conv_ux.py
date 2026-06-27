"""Tests for Telegram Repo Cockpit /conv UX helpers."""

from __future__ import annotations

from gateway.config import PlatformConfig
from gateway.platforms import telegram as telegram_mod
from gateway.platforms.telegram import TelegramAdapter


class _Button:
    def __init__(self, text, **kwargs):
        self.text = text
        self.callback_data = kwargs.get("callback_data")


class _Markup:
    def __init__(self, rows):
        self.inline_keyboard = rows


def test_repo_selected_text_is_actionable():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))

    text = adapter._repo_selected_text("MFcv1/example-app", "ask_review", "thread_123")

    assert "Repo" in text
    assert "MFcv1/example-app" in text
    assert "Mode" in text
    assert "thread_123" in text
    assert "envoie ta tâche directement dans ce chat" in text


def test_repo_selected_keyboard_has_compact_followup_actions(monkeypatch):
    monkeypatch.setattr(telegram_mod, "InlineKeyboardButton", _Button)
    monkeypatch.setattr(telegram_mod, "InlineKeyboardMarkup", _Markup)
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))

    markup = adapter._repo_selected_keyboard("ask_review")
    rows = markup.inline_keyboard

    assert [[button.text for button in row] for row in rows] == [
        ["Changer repo", "Changer mode"],
        ["Annuler"],
    ]
    assert rows[0][0].callback_data == "rcn:existing:ask_review"
    assert rows[0][1].callback_data == "rcn:mode:autopilot"
    assert rows[1][0].callback_data == "rcn:cancel"
