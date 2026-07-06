"""Tests for Repo Cockpit Telegram Pilote mode."""

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest


class FakeButton:
    def __init__(self, text, callback_data=None, **kwargs):
        self.text = text
        self.callback_data = callback_data
        self.kwargs = kwargs


class FakeMarkup:
    def __init__(self, inline_keyboard):
        self.inline_keyboard = inline_keyboard


def _ensure_telegram_mock():
    mod = MagicMock()
    mod.InlineKeyboardButton = FakeButton
    mod.InlineKeyboardMarkup = FakeMarkup
    mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    mod.constants.ParseMode.MARKDOWN = "Markdown"
    mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    mod.constants.ParseMode.HTML = "HTML"
    mod.constants.ChatType.PRIVATE = "private"
    mod.constants.ChatType.GROUP = "group"
    mod.constants.ChatType.SUPERGROUP = "supergroup"
    mod.constants.ChatType.CHANNEL = "channel"
    mod.error.NetworkError = type("NetworkError", (OSError,), {})
    mod.error.TimedOut = type("TimedOut", (OSError,), {})
    mod.error.BadRequest = type("BadRequest", (Exception,), {})

    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules[name] = mod
    sys.modules["telegram.error"] = mod.error


_ensure_telegram_mock()

from gateway.config import HomeChannel, Platform, PlatformConfig
from gateway.platforms import telegram as telegram_mod
from gateway.platforms.telegram import TelegramAdapter, normalize_cockpit_mode


def _make_adapter():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token"))
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    return adapter


def _flatten_buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def test_normalize_cockpit_mode_accepts_pilote():
    assert normalize_cockpit_mode("pilote") == "pilote"
    assert normalize_cockpit_mode("autopilot") == "autopilot"
    assert normalize_cockpit_mode("ask_review") == "ask_review"
    assert normalize_cockpit_mode("unknown") == "ask_review"


def test_mode_title_and_note_cover_pilote():
    adapter = _make_adapter()

    assert adapter._mode_title("pilote") == "Pilote"
    assert "Architect/Deploy" in adapter._mode_note("pilote")
    assert adapter._thread_mode_label("pilote") == "Pilote"


def test_new_chat_keyboard_with_prefs_contains_three_modes(monkeypatch):
    monkeypatch.setattr(telegram_mod, "InlineKeyboardButton", FakeButton)
    monkeypatch.setattr(telegram_mod, "InlineKeyboardMarkup", FakeMarkup)
    adapter = _make_adapter()
    markup = adapter._new_chat_keyboard_with_prefs("pilote", "user-1")
    buttons = _flatten_buttons(markup)
    labels = [button.text for button in buttons]
    callbacks = [button.callback_data for button in buttons]

    assert "Ask review" in labels
    assert "✓ Pilote" in labels
    assert "Autopilot" in labels
    assert "Projet GitHub existant" in labels
    assert "rcn:mode:ask_review" in callbacks
    assert "rcn:mode:pilote" in callbacks
    assert "rcn:mode:autopilot" in callbacks
    assert "rcn:existing:pilote" in callbacks
    assert "rcn:scratch:pilote" in callbacks


def test_repo_selected_keyboard_offers_all_mode_choices(monkeypatch):
    monkeypatch.setattr(telegram_mod, "InlineKeyboardButton", FakeButton)
    monkeypatch.setattr(telegram_mod, "InlineKeyboardMarkup", FakeMarkup)
    adapter = _make_adapter()
    markup = adapter._repo_selected_keyboard("pilote")
    callbacks = [button.callback_data for button in _flatten_buttons(markup)]

    assert "rcn:existing:pilote" in callbacks
    assert "rcn:mode:ask_review" in callbacks
    assert "rcn:mode:pilote" in callbacks
    assert "rcn:mode:autopilot" in callbacks


def test_pilot_existing_intent_keyboard_has_repo_work_routes(monkeypatch):
    monkeypatch.setattr(telegram_mod, "InlineKeyboardButton", FakeButton)
    monkeypatch.setattr(telegram_mod, "InlineKeyboardMarkup", FakeMarkup)
    adapter = _make_adapter()
    markup = adapter._pilot_existing_intent_keyboard("pilote")
    buttons = _flatten_buttons(markup)
    labels = [button.text for button in buttons]
    callbacks = [button.callback_data for button in buttons]

    assert "Comprendre / auditer le repo" in labels
    assert "Modifier / ajouter une feature" in labels
    assert "Corriger un bug" in labels
    assert "Déployer / vérifier prod" in labels
    assert "Refactor / sécuriser" in labels
    assert "Je ne sais pas" in labels
    assert "rcn:intent:audit_repo:pilote" in callbacks
    assert "rcn:intent:feature_work:pilote" in callbacks
    assert "rcn:intent:debug_fix:pilote" in callbacks
    assert "rcn:intent:deploy:pilote" in callbacks


@pytest.mark.asyncio
async def test_pilot_intake_text_creates_task_without_task_command(monkeypatch):
    adapter = _make_adapter()
    adapter._create_task_from_thread_command = AsyncMock()

    class User:
        id = "42"

    class Msg:
        from_user = User()
        chat_id = "100"

    adapter._pilot_intake_states["42"] = {
        "awaiting": "prompt",
        "mode": "pilote",
        "origin": "github_existing",
        "intent": "feature_work",
    }

    handled = await adapter._maybe_handle_pilot_intake_text(Msg(), "Ajoute un dashboard SEO")

    assert handled is True
    assert "42" not in adapter._pilot_intake_states
    adapter._create_task_from_thread_command.assert_awaited_once()
    assert adapter._create_task_from_thread_command.await_args.args[1] == "Ajoute un dashboard SEO"


@pytest.mark.asyncio
async def test_libre_command_soft_closes_pilot_state_without_hard_reset(monkeypatch, tmp_path):
    from gateway.libre_orchestrator import ActiveWorkStore

    adapter = _make_adapter()
    monkeypatch.setattr(adapter, "_libre_store", lambda: ActiveWorkStore(tmp_path / "libre_state.json"))
    adapter._send_cockpit_text = AsyncMock()
    adapter._pilot_intake_states["42"] = {"awaiting": "prompt"}
    adapter._repo_new_chat_choices["42"] = {"repos": []}
    active = {
        "repo": "MFcv1/hermes-agent",
        "thread_id": "thread_123",
        "thread_mode": "pilote",
        "last_task_title": "Améliorer /new",
    }
    adapter._get_active_cockpit_thread = AsyncMock(return_value=("42", {"ok": True, "active": active}, active))

    class User:
        id = "42"

    class Msg:
        from_user = User()
        chat_id = "100"

    await adapter._send_libre_command(Msg(), "")

    assert "42" not in adapter._pilot_intake_states
    assert "42" not in adapter._repo_new_chat_choices
    assert adapter._libre_chat_states["42"]["mode"] == "libre"
    sent = adapter._send_cockpit_text.await_args.args[1]
    assert "Mode libre" in sent
    assert "MFcv1/hermes-agent" in sent
    assert "Mémoire durable conservée" in sent
    assert "Pilote" in sent and "Autopilot" in sent


@pytest.mark.asyncio
async def test_libre_text_learns_model_policy_without_repo_task(monkeypatch, tmp_path):
    from gateway.libre_orchestrator import ActiveWorkStore

    adapter = _make_adapter()
    monkeypatch.setattr(adapter, "_libre_store", lambda: ActiveWorkStore(tmp_path / "libre_state.json"))
    adapter._send_cockpit_text = AsyncMock()
    adapter._libre_chat_states["42"] = {"mode": "libre"}

    class User:
        id = "42"

    class Msg:
        from_user = User()
        chat_id = "100"

    handled = await adapter._maybe_handle_libre_text(Msg(), "Pour les plans mets toi en GPT-5.5 high")

    assert handled is True
    sent = adapter._send_cockpit_text.await_args.args[1]
    assert "Règle apprise" in sent
    assert "planning" in sent
    assert "gpt-5.5" in sent
    assert "high" in sent


@pytest.mark.asyncio
async def test_libre_text_routes_repo_work_to_cockpit_with_selected_mode(monkeypatch):
    adapter = _make_adapter()
    adapter._send_cockpit_text = AsyncMock()
    adapter._libre_chat_states["42"] = {"mode": "libre"}
    active = {"repo": "MFcv1/hermes-agent", "thread_id": "thread_123", "thread_mode": "ask_review"}
    adapter._get_active_cockpit_thread = AsyncMock(return_value=("42", {"ok": True, "active": active}, active))
    api_calls = []

    def fake_api(method, path, payload=None, timeout=20):
        api_calls.append((method, path, payload, timeout))
        return {"ok": True, "id": "op_123", "repo": "MFcv1/hermes-agent", "mode": payload.get("mode"), "status": "queued_plan"}

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    created = []

    def fake_create_task(coro):
        coro.close()
        task = MagicMock()
        created.append(task)
        return task

    monkeypatch.setattr(adapter, "_cockpit_api_sync", fake_api)
    monkeypatch.setattr(telegram_mod.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(telegram_mod.asyncio, "create_task", fake_create_task)

    class User:
        id = "42"

    class Msg:
        from_user = User()
        chat_id = "100"

    handled = await adapter._maybe_handle_libre_text(Msg(), "corrige le bug du menu /new sur le repo Hermes")

    assert handled is True
    assert api_calls[0][0:2] == ("POST", "/api/internal/tasks/from-thread")
    assert api_calls[0][2]["mode"] == "pilote"
    assert api_calls[0][2]["intent"] == "debug_fix"
    assert api_calls[0][2]["source"] == "telegram_libre_router"
    assert len(created) == 1


@pytest.mark.asyncio
async def test_libre_watch_subcommand_summarizes_logs(monkeypatch, tmp_path):
    adapter = _make_adapter()
    adapter._send_cockpit_text = AsyncMock()
    log = tmp_path / "gateway.log"
    log.write_text("ERROR callback failed\n", encoding="utf-8")
    monkeypatch.setattr(adapter, "_libre_watch_log_paths", lambda: [log])

    class User:
        id = "42"

    class Msg:
        from_user = User()
        chat_id = "100"

    await adapter._send_libre_command(Msg(), "watch")

    sent = adapter._send_cockpit_text.await_args.args[1]
    assert "Watch Libre" in sent
    assert "attention" in sent
    assert "callback failed" in sent


def test_libre_background_watch_is_opt_in_not_default():
    default_adapter = TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))
    enabled_adapter = TelegramAdapter(
        PlatformConfig(enabled=True, token="fake-token", extra={"libre_watch_enabled": True})
    )

    assert default_adapter._libre_watch_enabled is False
    assert enabled_adapter._libre_watch_enabled is True


@pytest.mark.asyncio
async def test_libre_watch_tick_autonomously_sends_home_alert(monkeypatch, tmp_path):
    config = PlatformConfig(
        enabled=True,
        token="fake-token",
        home_channel=HomeChannel(Platform.TELEGRAM, "home-chat", "Home", thread_id="99"),
    )
    adapter = TelegramAdapter(config)
    sent = []

    async def fake_send(chat_id, content, reply_to=None, metadata=None):
        sent.append((chat_id, content, metadata))
        return type("Result", (), {"success": True})()

    log = tmp_path / "gateway.log"
    log.write_text("ERROR callback failed\n", encoding="utf-8")
    monkeypatch.setattr(adapter, "_libre_watch_log_paths", lambda: [log])
    monkeypatch.setattr(adapter, "send", fake_send)

    delivered = await adapter._libre_watch_tick()

    assert delivered is True
    assert sent[0][0] == "home-chat"
    assert sent[0][2]["thread_id"] == "99"
    assert sent[0][2]["notify"] is True
    assert "Watch Libre autonome" in sent[0][1]
    assert "callback failed" in sent[0][1]


@pytest.mark.asyncio
async def test_libre_watch_tick_deduplicates_same_error(monkeypatch, tmp_path):
    config = PlatformConfig(
        enabled=True,
        token="fake-token",
        home_channel=HomeChannel(Platform.TELEGRAM, "home-chat", "Home"),
    )
    adapter = TelegramAdapter(config)
    sent = []

    async def fake_send(chat_id, content, reply_to=None, metadata=None):
        sent.append(content)
        return type("Result", (), {"success": True})()

    log = tmp_path / "gateway.log"
    log.write_text("ERROR same failure\n", encoding="utf-8")
    monkeypatch.setattr(adapter, "_libre_watch_log_paths", lambda: [log])
    monkeypatch.setattr(adapter, "send", fake_send)

    first = await adapter._libre_watch_tick()
    second = await adapter._libre_watch_tick()

    assert first is True
    assert second is False
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_autonomous_worker_payload_enables_runtime_observer(monkeypatch):
    adapter = _make_adapter()
    adapter._format_autopilot_live_card = MagicMock(return_value="live")
    adapter._autonomy_keyboard = MagicMock(return_value=None)
    adapter._send_cockpit_panel = AsyncMock(return_value=type("Panel", (), {"message_id": 1})())
    adapter._send_cockpit_text = AsyncMock()
    adapter._edit_cockpit_panel = AsyncMock(return_value=True)
    monkeypatch.setattr(adapter, "_libre_watch_log_paths", lambda: [])
    api_calls = []

    def fake_api(method, path, payload=None, timeout=20):
        api_calls.append((method, path, payload, timeout))
        if path.endswith("/autonomy"):
            return {"ok": True, "task": {"id": "op_123", "status": "queued_plan", "mode": "pilote"}}
        if path == "/api/worker/run-once":
            return {"ok": True, "result": {"status": "completed"}, "status": "completed"}
        if path == "/api/tasks/op_123":
            return {"ok": True, "status": "completed"}
        return {"ok": True}

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(adapter, "_cockpit_api_sync", fake_api)
    monkeypatch.setattr(telegram_mod.asyncio, "to_thread", fake_to_thread)

    class Msg:
        chat_id = "100"

    await adapter._run_autopilot_worker_after_task_create(Msg(), "op_123")

    worker_payload = next(payload for _method, path, payload, _timeout in api_calls if path == "/api/worker/run-once")
    assert worker_payload["runtime_observer"]["enabled"] is True
    assert worker_payload["runtime_observer"]["task_id"] == "op_123"
    assert worker_payload["runtime_observer"]["mode"] == "during_work"
