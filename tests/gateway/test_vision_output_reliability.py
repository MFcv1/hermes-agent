from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from gateway.run import GatewayRunner


@pytest.mark.asyncio
async def test_oversized_auto_vision_is_artifact_backed(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    runner = object.__new__(GatewayRunner)
    runner.hooks = SimpleNamespace(emit=AsyncMock())
    runner._is_session_run_current = lambda _key, _generation: True

    result_json = json.dumps({"success": True, "analysis": "V" * 50_000})
    with patch("tools.vision_tools.vision_analyze_tool", AsyncMock(return_value=result_json)):
        enriched = await runner._enrich_message_with_vision(
            "caption",
            ["/tmp/image.png"],
            session_key="session-a",
            run_generation=3,
        )

    assert len(enriched) < 5_000
    assert "<persisted-output>" in enriched
    artifact = next((tmp_path / "artifacts" / "tool-results" / "vision").glob("*.txt"))
    assert len(artifact.read_text(encoding="utf-8")) == 50_000
    runner.hooks.emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_stop_prevents_second_vision_call():
    runner = object.__new__(GatewayRunner)
    runner.hooks = SimpleNamespace(emit=AsyncMock())
    current = {"value": True}
    runner._is_session_run_current = lambda _key, _generation: current["value"]
    calls = 0

    async def _vision(**_kwargs):
        nonlocal calls
        calls += 1
        current["value"] = False
        return json.dumps({"success": True, "analysis": "stale"})

    with patch("tools.vision_tools.vision_analyze_tool", _vision):
        enriched = await runner._enrich_message_with_vision(
            "caption",
            ["/tmp/one.png", "/tmp/two.png"],
            session_key="session-a",
            run_generation=3,
        )

    assert calls == 1
    assert enriched == "caption"
