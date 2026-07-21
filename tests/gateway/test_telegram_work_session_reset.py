from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform
from gateway.platforms import telegram as telegram_mod
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionEntry, SessionSource


@pytest.mark.asyncio
async def test_work_session_reset_waits_for_final_session_and_marks_trusted_boundary():
    source = SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c1",
        chat_type="dm",
    )
    event = MessageEvent(
        text="placeholder",
        source=source,
        message_type=MessageType.TEXT,
        message_id="m1",
    )
    final_entry = SessionEntry(
        session_key="agent:main:telegram:dm:u1",
        session_id="final-session",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    store = SimpleNamespace(get_or_create_session=lambda _source: final_entry)
    seen = {}

    async def handler(reset_event):
        seen["text"] = reset_event.text
        seen["trusted"] = reset_event._trusted_destructive_slash
        seen["discard_empty"] = reset_event._discard_empty_previous_session
        return "ignored reset result"

    adapter = object.__new__(telegram_mod.TelegramAdapter)
    adapter._build_message_event = lambda *_args, **_kwargs: event
    adapter._apply_telegram_group_observe_attribution = lambda value: value
    adapter._message_handler = AsyncMock(side_effect=handler)
    adapter._session_store = store
    msg = SimpleNamespace()

    session_id, session_key = await adapter._reset_current_work_session_chat(msg)

    adapter._message_handler.assert_awaited_once()
    assert seen == {"text": "/new", "trusted": True, "discard_empty": True}
    assert session_id == "final-session"
    assert session_key == "agent:main:telegram:dm:u1"
