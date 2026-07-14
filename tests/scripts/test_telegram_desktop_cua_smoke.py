"""Tests for the Telegram Desktop CUA smoke helper."""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "telegram_desktop_cua_smoke.py"
spec = importlib.util.spec_from_file_location("telegram_desktop_cua_smoke", SCRIPT)
telegram_smoke = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(telegram_smoke)


class FakeBackend:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.typed: list[str] = []
        self.keys: list[str] = []

    def is_available(self) -> bool:
        return True

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def focus_app(self, app: str, raise_window: bool = False):
        return SimpleNamespace(ok=True, message=f"targeted {app}")

    def capture(self, mode: str = "som", app: str | None = None):
        raw = b"\xff\xd8fake-jpeg"
        return SimpleNamespace(
            mode=mode,
            app=app or "Telegram",
            window_title="Hermes smoke chat",
            width=640,
            height=480,
            png_b64=base64.b64encode(raw).decode("ascii"),
            png_bytes_len=len(raw),
            elements=[object(), object()],
        )

    def type_text(self, text: str):
        self.typed.append(text)
        return SimpleNamespace(ok=True, message="typed")

    def key(self, keys: str):
        self.keys.append(keys)
        return SimpleNamespace(ok=True, message="pressed")


class PrefixAppBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.focused: list[str] = []

    def focus_app(self, app: str, raise_window: bool = False):
        self.focused.append(app)
        if app == "- Telegram":
            return SimpleNamespace(ok=True, message="targeted prefixed Telegram")
        return SimpleNamespace(ok=False, message=f"No on-screen window found for app {app!r}.")

    def list_apps(self):
        return [{"name": "- Telegram", "pid": 660}]


class OffscreenTelegramBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self._session = SimpleNamespace(call_tool=self._call_tool)

    def focus_app(self, app: str, raise_window: bool = False):
        return SimpleNamespace(ok=False, message="No on-screen window found")

    def list_apps(self):
        return [{"name": "Telegram", "pid": 660}]

    def _call_tool(self, name: str, args: dict):
        assert name == "list_windows"
        return {
            "structuredContent": {
                "windows": [
                    {
                        "app_name": "Telegram",
                        "title": "Herme_core",
                        "is_on_screen": False,
                        "bounds": {"x": 0, "y": 39, "width": 1389, "height": 981},
                        "pid": 660,
                        "window_id": 39,
                    }
                ]
            }
        }


def _args(tmp_path, **overrides):
    data = {
        "message": "hello smoke",
        "command": None,
        "send": False,
        "no_enter": False,
        "app": "Telegram",
        "mode": "som",
        "evidence_dir": str(tmp_path),
        "cua_bridge_command": "",
        "json": False,
    }
    data.update(overrides)
    return argparse.Namespace(**data)


def test_dry_run_captures_evidence_without_typing(tmp_path):
    backend = FakeBackend()

    report = telegram_smoke.run_smoke(_args(tmp_path), backend_factory=lambda: backend)

    assert report["status"] == "screenshot_review_required"
    assert backend.started is True
    assert backend.stopped is True
    assert backend.typed == []
    assert backend.keys == []
    assert Path(report["evidence"]["json"]).is_file()
    assert Path(report["evidence"]["image"]).is_file()


def test_send_types_command_and_presses_return(tmp_path):
    backend = FakeBackend()

    report = telegram_smoke.run_smoke(
        _args(tmp_path, message=None, command="/version", send=True),
        backend_factory=lambda: backend,
    )

    assert report["status"] == "sent_review_required"
    assert backend.typed == ["/version"]
    assert backend.keys == ["return"]


def test_send_can_type_without_enter(tmp_path):
    backend = FakeBackend()

    report = telegram_smoke.run_smoke(
        _args(tmp_path, send=True, no_enter=True),
        backend_factory=lambda: backend,
    )

    assert report["status"] == "sent_review_required"
    assert backend.typed == ["hello smoke"]
    assert backend.keys == []


def test_retries_cua_reported_prefixed_app_name(tmp_path):
    backend = PrefixAppBackend()

    report = telegram_smoke.run_smoke(_args(tmp_path), backend_factory=lambda: backend)

    assert report["status"] == "screenshot_review_required"
    assert backend.focused == ["Telegram", "- Telegram"]
    assert report["focus"]["retry_app"] == "- Telegram"


def test_reports_offscreen_telegram_window_separately(tmp_path):
    backend = OffscreenTelegramBackend()

    report = telegram_smoke.run_smoke(_args(tmp_path), backend_factory=lambda: backend)

    assert report["status"] == "telegram_window_not_on_current_space"
    assert report["windows"][0]["title"] == "Herme_core"
    assert report["windows"][0]["is_on_screen"] is False


def test_mcp_backend_available_is_selected(tmp_path):
    backend = FakeBackend()

    report = telegram_smoke.run_smoke(_args(tmp_path), backend_factory=lambda: backend)

    assert report["backend"] == {"selected": "mcp", "fallback_used": False}


class MissingMcpBackend(FakeBackend):
    def start(self) -> None:
        exc = ModuleNotFoundError("No module named 'mcp'")
        exc.name = "mcp"
        raise exc


def test_missing_mcp_uses_explicit_operational_bridge(tmp_path):
    requests = []

    def bridge_runner(command, request):
        requests.append((command, request))
        return {
            "schema": 1,
            "status": "sent_review_required",
            "intent": request["intent"],
            "app": request["app"],
            "send": request["send"],
        }

    report = telegram_smoke.run_smoke(
        _args(tmp_path, send=True, cua_bridge_command="codex-cua-bridge --stdio"),
        backend_factory=MissingMcpBackend,
        cli_binary_resolver=lambda: None,
        bridge_runner=bridge_runner,
    )

    assert report["status"] == "sent_review_required"
    assert report["backend"]["selected"] == "external_bridge"
    assert report["backend"]["fallback_used"] is True
    assert report["backend"]["mcp_error_type"] == "ModuleNotFoundError"
    assert requests[0][0] == "codex-cua-bridge --stdio"
    assert requests[0][1]["operation"] == "telegram_desktop_cua_smoke"


def test_missing_mcp_without_bridge_is_explicitly_blocked(tmp_path):
    report = telegram_smoke.run_smoke(
        _args(tmp_path, send=True),
        backend_factory=MissingMcpBackend,
    )

    assert report["status"] == "cua_cli_unavailable"
    assert report["backend"]["selected"] is None
    assert report["backend"]["mcp_error_type"] == "ModuleNotFoundError"
    assert report["fallback"]["available"] is False


def test_missing_mcp_bridge_error_is_not_reported_as_success(tmp_path):
    def broken_bridge(command, request):
        raise RuntimeError("bridge exited 9: unavailable")

    report = telegram_smoke.run_smoke(
        _args(tmp_path, send=True, cua_bridge_command="broken-bridge"),
        backend_factory=MissingMcpBackend,
        cli_binary_resolver=lambda: None,
        bridge_runner=broken_bridge,
    )

    assert report["status"] == "cua_bridge_failed"
    assert report["backend"]["selected"] == "external_bridge"
    assert report["backend"]["fallback_used"] is True
    assert report["fallback"]["ok"] is False
    assert "bridge exited 9" in report["fallback"]["error"]


def _cli_result(**payload):
    return json.dumps(payload)


def test_mcp_priority_does_not_invoke_cli_subprocess(tmp_path):
    backend = FakeBackend()
    with patch.object(telegram_smoke.subprocess, "run") as run:
        report = telegram_smoke.run_smoke(
            _args(tmp_path),
            backend_factory=lambda: backend,
            cli_binary_resolver=lambda: "/Users/matthis/.local/bin/cua-driver",
        )

    assert report["backend"] == {"selected": "mcp", "fallback_used": False}
    run.assert_not_called()


def test_missing_mcp_uses_local_cli_for_full_send_smoke_without_real_send(tmp_path):
    windows = {
        "windows": [{
            "app_name": "Telegram", "pid": 660, "window_id": 39,
            "is_on_screen": True, "title": "Herme_core", "z_index": 0,
        }]
    }
    responses = [
        _cli_result(current_space_id=1, **windows),
        _cli_result(current_space_id=1, **windows),
        _cli_result(
            tree_markdown='Telegram — 2 elements\n[1] AXWindow "Herme_core"\n[2] AXTextArea id=Message',
            screenshot_png_b64=base64.b64encode(b"\xff\xd8fake-jpeg").decode("ascii"),
            elements=[{"index": 1}, {"index": 2}],
            screenshot_width=640,
            screenshot_height=480,
        ),
        _cli_result(success=True),
        _cli_result(success=True),
    ]

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=responses.pop(0), stderr="")

    with patch.object(telegram_smoke.subprocess, "run", side_effect=fake_run) as run:
        report = telegram_smoke.run_smoke(
            _args(tmp_path, send=True),
            backend_factory=MissingMcpBackend,
            cli_binary_resolver=lambda: "/Users/matthis/.local/bin/cua-driver",
        )

    assert report["status"] == "sent_review_required"
    assert report["backend"]["selected"] == "cua_driver_cli"
    assert report["backend"]["fallback_used"] is True
    assert report["capture"]["elements"] == 2
    assert report["capture"]["png_bytes_len"] > 0
    tools = [call.args[0][2] for call in run.call_args_list]
    assert tools == ["list_windows", "list_windows", "get_window_state", "type_text", "press_key"]
    assert json.loads(run.call_args_list[-2].args[0][3])["text"] == "hello smoke"


def test_missing_mcp_and_cli_absent_fails_explicitly(tmp_path):
    report = telegram_smoke.run_smoke(
        _args(tmp_path, send=True),
        backend_factory=MissingMcpBackend,
        cli_binary_resolver=lambda: None,
    )

    assert report["status"] == "cua_cli_unavailable"
    assert report["backend"]["selected"] is None
    assert report["fallback"]["available"] is False


def test_cli_nonzero_exit_is_reported_with_stderr(tmp_path):
    completed = subprocess.CompletedProcess([], 9, stdout="", stderr="driver unavailable")
    with patch.object(telegram_smoke.subprocess, "run", return_value=completed):
        report = telegram_smoke.run_smoke(
            _args(tmp_path),
            backend_factory=MissingMcpBackend,
            cli_binary_resolver=lambda: "/Users/matthis/.local/bin/cua-driver",
        )

    assert report["status"] == "cua_cli_failed"
    assert "exited 9" in report["error"]
    assert "driver unavailable" in report["error"]


def test_cli_invalid_json_is_reported(tmp_path):
    completed = subprocess.CompletedProcess([], 0, stdout="not-json", stderr="")
    with patch.object(telegram_smoke.subprocess, "run", return_value=completed):
        report = telegram_smoke.run_smoke(
            _args(tmp_path),
            backend_factory=MissingMcpBackend,
            cli_binary_resolver=lambda: "/Users/matthis/.local/bin/cua-driver",
        )

    assert report["status"] == "cua_cli_failed"
    assert "invalid JSON" in report["error"]


def test_cli_normalizes_real_top_level_list_windows_payload():
    payload = {
        "current_space_id": 1,
        "windows": [{
            "app_name": "Telegram", "pid": 660, "window_id": 39,
            "is_on_screen": True, "title": "Herme_core", "z_index": 0,
        }],
    }
    completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")
    with patch.object(telegram_smoke.subprocess, "run", return_value=completed):
        result = telegram_smoke._CuaDriverCliSession("/tmp/cua-driver").call_tool(
            "list_windows", {"on_screen_only": True}
        )

    assert result["structuredContent"]["windows"] == payload["windows"]
    assert result["data"]["current_space_id"] == 1
    assert result["isError"] is False
