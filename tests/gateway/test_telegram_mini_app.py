"""Telegram Mini App session-resume bridge."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from plugins.platforms.telegram.adapter import TelegramAdapter


def test_mini_app_resume_uses_the_existing_resume_command():
    """The WebApp payload resumes the selected session in the same chat."""
    adapter = object.__new__(TelegramAdapter)
    captured = []
    message = SimpleNamespace(
        web_app_data=SimpleNamespace(
            data=json.dumps({"action": "session.resume", "session_id": "session abc"})
        )
    )

    adapter._effective_update_message = lambda update: message
    adapter._should_process_message = lambda msg: True
    adapter._build_message_event = lambda msg, kind, update_id=None: SimpleNamespace()
    adapter._apply_telegram_group_observe_attribution = lambda event: event

    async def handle_message(event):
        captured.append(event.text)

    adapter.handle_message = handle_message
    asyncio.run(adapter._handle_web_app_data(SimpleNamespace(update_id=17), None))

    assert captured == ["/resume 'session abc'"]


def test_dashboard_resume_callback_dispatches_in_the_linked_chat(monkeypatch):
    adapter = object.__new__(TelegramAdapter)
    captured = []
    answers = []
    edits = []
    message = SimpleNamespace(
        chat_id=123,
        chat=SimpleNamespace(type="private"),
        message_thread_id=None,
    )
    actor = SimpleNamespace(id=42, first_name="Matthis", full_name="Matthis")

    class Query:
        data = "wsr:ws_123"
        from_user = actor

        def __init__(self, query_message):
            self.message = query_message

        async def answer(self, **kwargs):
            answers.append(kwargs)

        async def edit_message_text(self, **kwargs):
            edits.append(kwargs)

    class Store:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_session(self, session_id):
            assert session_id == "ws_123"
            return {"id": session_id, "title": "homepageV2", "repo": "MFcv1/site"}

    monkeypatch.setattr("work_sessions.WorkSessionStore", Store)
    adapter._is_callback_user_authorized = lambda *args, **kwargs: True

    async def dispatch(msg, store, session, **kwargs):
        captured.append((msg, session["id"], kwargs["actor"].id))

    adapter._dispatch_work_session_resume = dispatch
    update = SimpleNamespace(callback_query=Query(message), update_id=19)

    asyncio.run(adapter._handle_callback_query(update, None))

    assert captured == [(message, "ws_123", 42)]
    assert answers == [{"text": "Reprise dans ce chat…"}]
    assert "Session reprise dans Telegram" in edits[0]["text"]
