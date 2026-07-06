import json
import re

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter


@pytest.mark.asyncio
async def test_api_server_health_exposes_git_sha_and_started_at():
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "test-key"}))

    response = await adapter._handle_health(None)
    payload = json.loads(response.text)

    assert payload["status"] == "ok"
    assert re.fullmatch(r"[0-9a-f]{7,40}|unknown", payload["git_sha"])
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", payload["started_at"])


@pytest.mark.asyncio
async def test_api_server_detailed_health_exposes_same_deploy_fields(monkeypatch):
    monkeypatch.setattr(
        "gateway.status.read_runtime_status",
        lambda: {"gateway_state": "running", "platforms": {}, "active_agents": 0},
    )
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "test-key"}))

    response = await adapter._handle_health_detailed(None)
    payload = json.loads(response.text)

    assert re.fullmatch(r"[0-9a-f]{7,40}|unknown", payload["git_sha"])
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", payload["started_at"])
