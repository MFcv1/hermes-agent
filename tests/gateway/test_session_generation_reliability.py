from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.run import GatewayRunner


def _runner(tmp_path):
    runner = object.__new__(GatewayRunner)
    runner.config = SimpleNamespace(sessions_dir=tmp_path)
    runner._session_generation = {}
    runner._session_run_generation = {"session-a": 4}
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._pending_messages = {}
    runner._queued_events = {}
    runner._active_session_leases = {}
    runner._busy_ack_ts = {}
    runner._persist_active_agents = MagicMock()
    runner.adapters = {}
    return runner


def test_reset_generation_rejects_old_async_event_and_persists(tmp_path):
    runner = _runner(tmp_path)
    event = {
        "type": "completion",
        "session_key": "session-a",
        "session_generation": 0,
    }

    assert runner._event_generation_is_current(event)
    assert runner._advance_session_generation("session-a", reason="reset") == 1
    assert not runner._event_generation_is_current(event)

    reloaded = _runner(tmp_path)
    reloaded._session_generation = reloaded._load_session_generations()
    assert reloaded._current_session_generation("session-a") == 1
    assert not reloaded._event_generation_is_current(event)


@pytest.mark.asyncio
async def test_stop_closes_dispatch_rails_before_interrupt(tmp_path):
    runner = _runner(tmp_path)
    key = "session-a"
    adapter = SimpleNamespace(
        _pending_messages={key: object()},
        interrupt_session_activity=AsyncMock(),
    )
    source = SimpleNamespace(platform="fake", chat_id="chat-a")
    runner.adapters = {"fake": adapter}
    runner._pending_messages[key] = "pending"
    runner._queued_events[key] = ["queued"]

    def _interrupt(_reason):
        assert key not in runner._pending_messages
        assert key not in runner._queued_events
        assert key not in adapter._pending_messages
        assert runner._current_session_generation(key) == 1

    agent = SimpleNamespace(interrupt=MagicMock(side_effect=_interrupt))
    runner._running_agents[key] = agent

    with (
        patch("tools.async_delegation.interrupt_session") as interrupt_delegations,
        patch("tools.process_registry.process_registry.kill_all_for_session") as kill_processes,
    ):
        await runner._interrupt_and_clear_session(
            key,
            source,
            interrupt_reason="stop",
            invalidation_reason="stop_command",
        )

    interrupt_delegations.assert_called_once_with(key, reason="stop")
    kill_processes.assert_called_once_with(key)
    agent.interrupt.assert_called_once_with("stop")
    adapter.interrupt_session_activity.assert_awaited_once_with(key, "chat-a")
    assert key not in runner._running_agents


@pytest.mark.asyncio
async def test_stale_process_watcher_never_injects(tmp_path):
    runner = _runner(tmp_path)
    runner._session_generation["session-a"] = 2
    runner._running = True
    runner._load_background_notifications_mode = lambda: "result"
    runner._build_process_event_source = MagicMock()
    watcher = {
        "session_id": "missing",
        "check_interval": 0,
        "session_key": "session-a",
        "session_generation": 1,
    }

    await runner._run_process_watcher(watcher)

    runner._build_process_event_source.assert_not_called()
