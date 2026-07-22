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
