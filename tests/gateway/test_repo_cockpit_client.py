import json
from unittest.mock import MagicMock
from urllib.error import HTTPError

from gateway.repo_cockpit_client import RepoCockpitClient, cockpit_webapp_url


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def test_repo_cockpit_client_api_sync_posts_utf8_json(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = dict(req.header_items())
        captured["data"] = req.data
        captured["timeout"] = timeout
        return _Response({"ok": True, "name": "équipe"})

    monkeypatch.setattr("gateway.repo_cockpit_client.urlopen", fake_urlopen)

    result = RepoCockpitClient().api_sync(
        "POST",
        "/api/internal/state",
        {"telegram_user_id": "42", "mode": "pilote", "name": "équipe"},
        timeout=7,
    )

    assert result == {"ok": True, "name": "équipe"}
    assert captured["url"] == "http://127.0.0.1:8765/api/internal/state"
    assert captured["method"] == "POST"
    assert captured["headers"]["Content-type"] == "application/json"
    assert captured["timeout"] == 7
    assert json.loads(captured["data"].decode("utf-8"))["name"] == "équipe"


def test_repo_cockpit_client_api_sync_preserves_http_error_shape(monkeypatch):
    err = HTTPError(
        "http://127.0.0.1:8765/api/tasks/missing",
        404,
        "Not Found",
        hdrs=None,
        fp=MagicMock(read=lambda: b'{"detail":"missing"}'),
    )

    def fake_urlopen(req, timeout):
        raise err

    monkeypatch.setattr("gateway.repo_cockpit_client.urlopen", fake_urlopen)

    result = RepoCockpitClient().api_sync("GET", "/api/tasks/missing", None, timeout=3)

    assert result == {
        "ok": False,
        "error_code": 404,
        "description": '{"detail":"missing"}',
    }


def test_cockpit_webapp_url_keeps_existing_query_adds_params_and_busts_cache(monkeypatch):
    monkeypatch.setenv("REPO_COCKPIT_URL", "https://cockpit.example/root?existing=1&v=old")
    monkeypatch.setattr("gateway.repo_cockpit_client.time", MagicMock(time=lambda: 1234567890))

    result = cockpit_webapp_url("/select-repo", mode="pilote", ignored=None)

    assert result == "https://cockpit.example/select-repo?existing=1&v=1234567890&mode=pilote"


def test_repo_cockpit_client_posts_runtime_observation(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["data"] = req.data
        captured["timeout"] = timeout
        return _Response({"ok": True})

    monkeypatch.setattr("gateway.repo_cockpit_client.urlopen", fake_urlopen)

    result = RepoCockpitClient().post_runtime_observation(
        "op_123",
        {"schema_version": 2, "task_id": "op_123", "raw_excerpt": "failed"},
        timeout=6,
    )

    assert result == {"ok": True}
    assert captured["url"] == "http://127.0.0.1:8765/api/internal/tasks/op_123/runtime-observations"
    assert captured["method"] == "POST"
    assert json.loads(captured["data"].decode("utf-8"))["schema_version"] == 2
    assert captured["timeout"] == 6
