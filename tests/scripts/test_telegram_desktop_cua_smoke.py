"""Tests for the Telegram Desktop CUA smoke helper."""

from __future__ import annotations

import argparse
import base64
import importlib.util
from pathlib import Path
from types import SimpleNamespace


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
