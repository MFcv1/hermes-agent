"""Tests for Telegram Repo Cockpit /conv UX helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

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
        ["Changer repo", "Ask review"],
        ["Pilote", "Autopilot"],
        ["Annuler"],
    ]
    assert rows[0][0].callback_data == "rcn:existing:ask_review"
    assert rows[0][1].callback_data == "rcn:mode:ask_review"
    assert rows[1][0].callback_data == "rcn:mode:pilote"
    assert rows[1][1].callback_data == "rcn:mode:autopilot"
    assert rows[2][0].callback_data == "rcn:cancel"


def test_audit_task_text_is_bounded_and_readonly():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))
    active = {"repo": "MFcv1/example-app", "thread_id": "thread_123"}

    text = adapter._audit_task_text(active, "focus tests")

    assert "MFcv1/example-app" in text
    assert "thread_123" in text
    assert "focus tests" in text
    assert "sans modifier le repo" in text
    assert "pas de déploiement" in text
    assert "pas de restart service" in text


def test_audit_started_panel_has_tracking_and_resume_instructions():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))

    text = adapter._format_audit_started(
        job_id="audit_120000_abcd",
        task={
            "id": "op_123",
            "repo": "MFcv1/example-app",
            "status": "queued_plan",
            "mode": "ask_review",
        },
        active={"repo": "MFcv1/example-app"},
    )

    assert "audit_120000_abcd" in text
    assert "op_123" in text
    assert "MFcv1/example-app" in text
    assert "queued_plan" in text
    assert "dry-run" in text
    assert "/status op_123" in text
    assert "/runs op_123" in text


def test_dev_menu_groups_beginner_workflows(monkeypatch):
    monkeypatch.setattr(telegram_mod, "InlineKeyboardButton", _Button)
    monkeypatch.setattr(telegram_mod, "InlineKeyboardMarkup", _Markup)
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))

    text = adapter._dev_menu_text()
    keyboard = adapter._dev_menu_keyboard()

    assert "Dev cockpit simple" in text
    assert "/new" in text
    assert "/task" in text
    assert "/vps" in text
    labels = [[button.text for button in row] for row in keyboard.inline_keyboard]
    assert labels == [
        ["Nouveau projet", "Conversations"],
        ["GitHub flow", "Ops / deploy"],
        ["Apprendre", "Accueil"],
    ]


def test_dev_github_section_teaches_branch_pr_flow():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))

    text = adapter._dev_menu_text("github")

    assert "GitHub sans friction" in text
    assert "crée une branche" in text
    assert "ouvre une PR" in text
    assert "/audit" in text


@pytest.mark.asyncio
async def test_send_dev_command_uses_panel(monkeypatch):
    monkeypatch.setattr(telegram_mod, "InlineKeyboardButton", _Button)
    monkeypatch.setattr(telegram_mod, "InlineKeyboardMarkup", _Markup)
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))
    adapter._send_cockpit_panel = AsyncMock()
    msg = SimpleNamespace(chat_id="chat-1")

    await adapter._send_dev_command(msg, "ops")

    adapter._send_cockpit_panel.assert_awaited_once()
    sent_text = adapter._send_cockpit_panel.await_args.args[1]
    assert "Déploiement / maintenance" in sent_text
    assert "/updatecheck" in sent_text


def test_format_watch_jobs_lists_release_and_vps_watchers():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))

    text = adapter._format_watch_jobs([
        {
            "id": "job_release",
            "script": "github_release_watch.py",
            "script_args": ["--repo", "NousResearch/hermes-agent"],
            "schedule_display": "0 */6 * * *",
            "enabled": True,
        },
        {
            "id": "job_vps",
            "script": "vps_healthcheck.py",
            "schedule_display": "0 */6 * * *",
            "enabled": True,
        },
    ])

    assert "job_release" in text
    assert "NousResearch/hermes-agent" in text
    assert "job_vps" in text
    assert "vps" in text


@pytest.mark.asyncio
async def test_send_watch_releases_uses_blueprint_shortcut(monkeypatch):
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))
    adapter._send_cockpit_text = AsyncMock()
    seen = {}

    def fake_handle(args, *, origin=None, surface="cli"):
        seen["args"] = args
        seen["origin"] = origin
        seen["surface"] = surface
        return SimpleNamespace(text="Scheduled 'GitHub release watcher'")

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    import hermes_cli.blueprint_cmd as blueprint_cmd

    monkeypatch.setattr(blueprint_cmd, "handle_blueprint_command", fake_handle)
    monkeypatch.setattr(telegram_mod.asyncio, "to_thread", fake_to_thread)
    msg = SimpleNamespace(chat_id="chat-1", message_thread_id=42, chat=SimpleNamespace(title="Ops"))

    await adapter._send_watch_command(msg, "releases NousResearch/hermes-agent 3")

    assert "github-release-watch" in seen["args"]
    assert "repo=NousResearch/hermes-agent" in seen["args"]
    assert "interval_hours=3" in seen["args"]
    assert seen["origin"]["thread_id"] == "42"
    adapter._send_cockpit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_watch_vps_uses_healthcheck_blueprint(monkeypatch):
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))
    adapter._send_cockpit_text = AsyncMock()
    seen = {}

    def fake_handle(args, *, origin=None, surface="cli"):
        seen["args"] = args
        return SimpleNamespace(text="Scheduled 'VPS healthcheck'")

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    import hermes_cli.blueprint_cmd as blueprint_cmd

    monkeypatch.setattr(blueprint_cmd, "handle_blueprint_command", fake_handle)
    monkeypatch.setattr(telegram_mod.asyncio, "to_thread", fake_to_thread)
    msg = SimpleNamespace(chat_id="chat-1", chat=SimpleNamespace(title="Ops"))

    await adapter._send_watch_command(msg, "vps 12")

    assert seen["args"] == "vps-healthcheck interval_hours=12 deliver=origin"
    adapter._send_cockpit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_vps_command_formats_overview(monkeypatch):
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))
    adapter._send_cockpit_text = AsyncMock()

    async def fake_to_thread(func, *args, **kwargs):
        return {
            "status": "green",
            "disk": {
                "root": {"free_gb": 20, "used_percent": 40},
                "home": {"free_gb": 20, "used_percent": 40},
            },
            "cron": {"age_seconds": 5},
            "jobs": {"enabled": 1, "total": 1},
            "services": [],
        }

    monkeypatch.setattr(telegram_mod.asyncio, "to_thread", fake_to_thread)
    msg = SimpleNamespace(chat_id="chat-1")

    await adapter._send_vps_command(msg)

    sent = adapter._send_cockpit_text.await_args.args[1]
    assert "VPS status: GREEN" in sent
    assert "Root disk" in sent


@pytest.mark.asyncio
async def test_send_updatecheck_command_formats_short_report(monkeypatch):
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))
    adapter._send_cockpit_text = AsyncMock()

    async def fake_to_thread(func, *args, **kwargs):
        return {
            "status": "green",
            "head": "abc123456789",
            "origin_main": "abc123456789",
            "update_available": False,
            "worktree": {"clean": True, "counts": {"modified": 0, "untracked": 0}},
            "disk": {"free_gb": 20, "used_percent": 40},
            "latest_release": {"tag": "v2026.6.29"},
            "issues": [],
            "warnings": [],
        }

    monkeypatch.setattr(telegram_mod.asyncio, "to_thread", fake_to_thread)
    msg = SimpleNamespace(chat_id="chat-1")

    await adapter._send_updatecheck_command(msg)

    sent = adapter._send_cockpit_text.await_args.args[1]
    assert "Updatecheck: GREEN" in sent
    assert "Update: not available" in sent
    assert "Ready: no blocker found." in sent


@pytest.mark.asyncio
async def test_send_audit_command_creates_task_and_schedules_background_worker(monkeypatch):
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))
    adapter._send_cockpit_text = AsyncMock()
    adapter._get_active_cockpit_thread = AsyncMock(
        return_value=(
            "user-1",
            {"ok": True, "active": {"repo": "MFcv1/example-app", "thread_id": "thread_123"}},
            {"repo": "MFcv1/example-app", "thread_id": "thread_123", "thread_mode": "ask_review"},
        )
    )

    api_calls = []

    def fake_api(method, path, payload=None, timeout=20):
        api_calls.append((method, path, payload, timeout))
        assert path == "/api/internal/tasks/from-thread"
        return {
            "ok": True,
            "id": "op_123",
            "repo": "MFcv1/example-app",
            "status": "queued_plan",
            "mode": "ask_review",
        }

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    created_tasks = []

    def fake_create_task(coro):
        coro.close()
        task = MagicMock()
        created_tasks.append(task)
        return task

    monkeypatch.setattr(adapter, "_cockpit_api_sync", fake_api)
    monkeypatch.setattr(telegram_mod.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(telegram_mod.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(telegram_mod.os, "urandom", lambda n: b"\xab" * n)

    msg = SimpleNamespace(chat_id="chat-1", from_user=SimpleNamespace(id="user-1"))

    await adapter._send_audit_command(msg, "focus tests")

    assert api_calls[0][0:2] == ("POST", "/api/internal/tasks/from-thread")
    assert api_calls[0][2]["source"] == "telegram_audit_command"
    assert "focus tests" in api_calls[0][2]["task"]
    adapter._send_cockpit_text.assert_awaited_once()
    content = adapter._send_cockpit_text.await_args.args[1]
    assert "op_123" in content
    assert "MFcv1/example-app" in content
    assert "dry-run" in content
    assert len(created_tasks) == 1
    assert created_tasks[0] in adapter._cockpit_background_tasks
