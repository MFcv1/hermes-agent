#!/usr/bin/env python3
"""Telegram Desktop CUA smoke helper.

This script is intentionally operator-driven. By default it only targets and
captures Telegram Desktop through the Hermes CUA backend, then writes evidence
under ``~/.hermes/telegram-gui-smoke``. It types/sends only when ``--send`` is
passed, and assumes the operator already opened the intended Telegram chat.
"""

from __future__ import annotations

import argparse
import base64
import json
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "gateway").is_dir() and (parent / "tools").is_dir():
            return parent
    return Path.cwd()


def _default_evidence_dir() -> Path:
    return Path.home() / ".hermes" / "telegram-gui-smoke"


def _safe_slug(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    return "-".join(part for part in cleaned.split("-") if part)[:64] or "telegram-smoke"


def _message_from_args(args: argparse.Namespace) -> str:
    command = (args.command or "").strip()
    message = (args.message or "").strip()
    if command and message:
        raise ValueError("use only one of --command or --message")
    if command:
        return command
    if message:
        return message
    raise ValueError("provide --command or --message")


def _capture_summary(capture: Any) -> dict[str, Any]:
    return {
        "mode": getattr(capture, "mode", ""),
        "app": getattr(capture, "app", ""),
        "window_title": getattr(capture, "window_title", ""),
        "width": getattr(capture, "width", 0),
        "height": getattr(capture, "height", 0),
        "png_bytes_len": getattr(capture, "png_bytes_len", 0),
        "elements": len(getattr(capture, "elements", []) or []),
    }


def _candidate_app_names(apps: list[dict[str, Any]], desired: str) -> list[str]:
    """Return CUA-reported app names that plausibly match *desired*.

    Some cua-driver text fallbacks include the bullet prefix in parsed names
    (for example ``"- Telegram"``). Keep this script forgiving so the operator
    can pass the normal macOS app name.
    """
    desired_norm = desired.strip().lower().lstrip("- ").strip()
    candidates: list[str] = []
    for item in apps:
        raw = str(item.get("name") or "").strip()
        norm = raw.lower().lstrip("- ").strip()
        if norm == desired_norm or desired_norm in norm:
            if raw and raw not in candidates:
                candidates.append(raw)
            cleaned = raw.lstrip("- ").strip()
            if cleaned and cleaned not in candidates:
                candidates.append(cleaned)
    return candidates


def _diagnose_app_windows(backend: Any, desired: str) -> list[dict[str, Any]]:
    session = getattr(backend, "_session", None)
    if session is None or not hasattr(session, "call_tool"):
        return []
    try:
        out = session.call_tool("list_windows", {"on_screen_only": False})
    except Exception:
        return []
    windows = (out.get("structuredContent") or {}).get("windows") or []
    desired_norm = desired.strip().lower().lstrip("- ").strip()
    matches = []
    for window in windows:
        app_name = str(window.get("app_name") or "")
        norm = app_name.lower().lstrip("- ").strip()
        if norm == desired_norm or desired_norm in norm:
            matches.append({
                "app_name": app_name,
                "title": str(window.get("title") or ""),
                "is_on_screen": bool(window.get("is_on_screen")),
                "bounds": window.get("bounds") or {},
                "pid": window.get("pid"),
                "window_id": window.get("window_id"),
            })
    return matches


def _write_evidence(report: dict[str, Any], capture: Any, evidence_dir: Path) -> dict[str, str]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{_safe_slug(report['intent'])}"
    paths: dict[str, str] = {}

    png_b64 = getattr(capture, "png_b64", None)
    if png_b64:
        image_path = evidence_dir / f"{stem}.jpg"
        image_path.write_bytes(base64.b64decode(png_b64, validate=False))
        paths["image"] = str(image_path)

    json_path = evidence_dir / f"{stem}.json"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    paths["json"] = str(json_path)
    return paths


def _load_backend_factory():
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from tools.computer_use.cua_backend import CuaDriverBackend

    return CuaDriverBackend


def _resolve_cua_driver_binary() -> str | None:
    """Resolve the native CLI without requiring operator configuration."""
    discovered = shutil.which("cua-driver")
    if discovered:
        return discovered
    local = Path.home() / ".local" / "bin" / "cua-driver"
    return str(local) if shutil.which(str(local)) else None


class _CuaDriverCliSession:
    """Strict subprocess adapter for ``cua-driver call TOOL JSON``."""

    def __init__(self, binary: str, *, timeout: float = 30.0) -> None:
        self.binary = binary
        self.timeout = timeout

    def start(self) -> None:
        if not self.binary:
            raise RuntimeError("cua-driver CLI executable is unavailable")

    def stop(self) -> None:
        return None

    def call_tool(self, name: str, args: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        argv = [self.binary, "call", name, json.dumps(args, separators=(",", ":"))]
        call_timeout = self.timeout if timeout is None else timeout
        try:
            completed = subprocess.run(
                argv, capture_output=True, text=True, check=False, timeout=call_timeout,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"cua-driver CLI executable not found: {self.binary}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"cua-driver CLI {name} timed out after {call_timeout:g} seconds") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
            raise RuntimeError(
                f"cua-driver CLI {name} exited {completed.returncode}: {detail[:1000]}"
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"cua-driver CLI {name} returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"cua-driver CLI {name} returned non-object JSON")
        if {"data", "images", "isError"}.issubset(payload):
            return payload

        # ``cua-driver call`` returns the tool payload directly (not an MCP
        # CallToolResult envelope). Normalize that real CLI shape to the
        # facade contract consumed by CuaDriverBackend.
        if name == "list_windows":
            windows = payload.get("windows") or []
            if not isinstance(windows, list):
                raise RuntimeError("cua-driver CLI list_windows returned invalid windows")
            return {
                "data": payload,
                "images": [],
                "structuredContent": {"windows": windows},
                "isError": False,
            }
        if name == "list_apps":
            apps = payload.get("apps") or payload.get("applications") or []
            if apps and not isinstance(apps, list):
                raise RuntimeError("cua-driver CLI list_apps returned invalid apps")
            normalized = dict(payload)
            normalized["apps"] = apps
            return {
                "data": normalized,
                "images": [],
                "structuredContent": {"apps": apps},
                "isError": False,
            }

        text_chunks: list[str] = []
        images: list[str] = []
        for part in payload.get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                text_chunks.append(str(part.get("text") or ""))
            elif part.get("type") == "image" and part.get("data"):
                images.append(str(part["data"]))
        if name == "get_window_state":
            for key in ("text", "tree_markdown", "accessibility_tree", "ax_tree", "tree", "markdown"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    text_chunks.append(value)
            for key in ("screenshot_png_b64", "image", "screenshot"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    images.append(value)
                elif isinstance(value, dict) and value.get("data"):
                    images.append(str(value["data"]))
            raw_images = payload.get("images") or []
            if isinstance(raw_images, list):
                images.extend(
                    str(item.get("data") if isinstance(item, dict) else item)
                    for item in raw_images
                    if item
                )
        joined = "\n".join(chunk for chunk in text_chunks if chunk)
        data: Any = payload
        if joined:
            data = joined
        is_error = bool(payload.get("isError", False))
        if payload.get("success") is False or payload.get("ok") is False:
            is_error = True
        return {
            "data": data,
            "images": images,
            # Native ``cua-driver call get_window_state`` returns its schema at
            # the JSON root, unlike the MCP envelope. Preserve it for the
            # shared backend while retaining the legacy data/images contract.
            "structuredContent": (
                payload.get("structuredContent")
                or (payload if name == "get_window_state" else None)
            ),
            "isError": is_error,
        }


def _load_cli_backend(binary: str):
    """Reuse the existing CUA facade with a native CLI transport."""
    CuaDriverBackend = _load_backend_factory()

    class CuaDriverCliBackend(CuaDriverBackend):
        def __init__(self) -> None:
            self._session = _CuaDriverCliSession(binary)
            self._active_pid = None
            self._active_window_id = None
            self._last_app = None

        def start(self) -> None:
            self._session.start()

        def stop(self) -> None:
            self._session.stop()

        def is_available(self) -> bool:
            return True

    return CuaDriverCliBackend()


def _run_external_bridge(command: str, request: dict[str, Any]) -> dict[str, Any]:
    """Run an explicit JSON-over-stdio bridge to an in-process CUA runtime."""
    argv = shlex.split(command)
    if not argv:
        raise RuntimeError("CUA bridge command is empty")
    try:
        completed = subprocess.run(
            argv, input=json.dumps(request), capture_output=True, text=True,
            check=False, timeout=120,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"CUA bridge executable not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("CUA bridge timed out after 120 seconds") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
        raise RuntimeError(f"CUA bridge exited {completed.returncode}: {detail[:1000]}")
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("CUA bridge returned invalid JSON") from exc
    if not isinstance(response, dict) or not str(response.get("status") or ""):
        raise RuntimeError("CUA bridge response must be an object with a non-empty status")
    return response


def _is_missing_mcp(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ModuleNotFoundError) and getattr(current, "name", None) == "mcp":
            return True
        current = current.__cause__ or current.__context__
    return False


def _bridge_request(args: argparse.Namespace, intent: str) -> dict[str, Any]:
    return {
        "schema": 1,
        "operation": "telegram_desktop_cua_smoke",
        "intent": intent,
        "app": args.app,
        "mode": args.mode,
        "send": bool(args.send),
        "no_enter": bool(args.no_enter),
        "evidence_dir": str(Path(args.evidence_dir).expanduser()),
    }


def _run_bridge_fallback(
    args: argparse.Namespace,
    intent: str,
    *,
    backend_error: BaseException,
    bridge_runner: Callable[[str, dict[str, Any]], dict[str, Any]],
    cli_error: str = "",
) -> dict[str, Any]:
    command = str(getattr(args, "cua_bridge_command", "") or "").strip()
    backend_diag = {
        "selected": None,
        "fallback_used": False,
        "mcp_error_type": type(backend_error).__name__,
        "mcp_error": str(backend_error),
    }
    if cli_error:
        backend_diag["cli_error"] = cli_error
    if not command:
        return {
            "schema": 1,
            "status": "cua_bridge_required",
            "intent": intent,
            "app": args.app,
            "send": bool(args.send),
            "error": (
                "Python MCP CUA backend is unavailable; pass --cua-bridge-command "
                "with a JSON-over-stdio bridge to an in-process CUA runtime"
            ),
            "backend": backend_diag,
            "fallback": {"available": False, "selected": False},
        }

    backend_diag.update({"selected": "external_bridge", "fallback_used": True})
    try:
        bridged = bridge_runner(command, _bridge_request(args, intent))
        if not isinstance(bridged, dict) or not str(bridged.get("status") or ""):
            raise RuntimeError("CUA bridge response must contain a non-empty status")
    except Exception as exc:
        return {
            "schema": 1,
            "status": "cua_bridge_failed",
            "intent": intent,
            "app": args.app,
            "send": bool(args.send),
            "error": str(exc),
            "backend": backend_diag,
            "fallback": {"available": True, "selected": True, "ok": False, "error": str(exc)},
        }
    return {
        **bridged,
        "schema": 1,
        "intent": intent,
        "app": args.app,
        "send": bool(args.send),
        "backend": backend_diag,
        "fallback": {"available": True, "selected": True, "ok": True},
    }


def _run_fallback_chain(
    args: argparse.Namespace,
    intent: str,
    *,
    backend_error: BaseException,
    bridge_runner: Callable[[str, dict[str, Any]], dict[str, Any]],
    cli_binary_resolver: Callable[[], str | None],
) -> dict[str, Any]:
    """Try the native CLI first; retain the configured bridge as last resort."""
    binary = cli_binary_resolver()
    cli_error = ""
    if binary:
        cli_report = run_smoke(
            args,
            backend_factory=lambda: _load_cli_backend(binary),
            bridge_runner=bridge_runner,
            cli_binary_resolver=cli_binary_resolver,
            _backend_label="cua_driver_cli",
            _allow_fallback=False,
        )
        if cli_report.get("status") != "blocked":
            cli_report["backend"].update({
                "mcp_error_type": type(backend_error).__name__,
                "mcp_error": str(backend_error),
            })
            return cli_report
        cli_error = str(cli_report.get("error") or "native CLI smoke failed")

    command = str(getattr(args, "cua_bridge_command", "") or "").strip()
    if command:
        return _run_bridge_fallback(
            args, intent, backend_error=backend_error, bridge_runner=bridge_runner,
            cli_error=cli_error or "cua-driver CLI executable is unavailable",
        )

    status = "cua_cli_failed" if binary else "cua_cli_unavailable"
    error = cli_error or (
        "cua-driver CLI not found via PATH or ~/.local/bin/cua-driver"
    )
    return {
        "schema": 1,
        "status": status,
        "intent": intent,
        "app": args.app,
        "send": bool(args.send),
        "error": error,
        "backend": {
            "selected": "cua_driver_cli" if binary else None,
            "fallback_used": bool(binary),
            "mcp_error_type": type(backend_error).__name__,
            "mcp_error": str(backend_error),
            **({"cli_error": cli_error} if cli_error else {}),
        },
        "fallback": {
            "available": bool(binary), "selected": bool(binary), "ok": False,
        },
    }


def run_smoke(
    args: argparse.Namespace,
    *,
    backend_factory: Optional[Callable[[], Any]] = None,
    bridge_runner: Optional[Callable[[str, dict[str, Any]], dict[str, Any]]] = None,
    cli_binary_resolver: Optional[Callable[[], str | None]] = None,
    _backend_label: str = "mcp",
    _allow_fallback: bool = True,
) -> dict[str, Any]:
    intent = _message_from_args(args)
    evidence_dir = Path(args.evidence_dir).expanduser()
    backend_factory = backend_factory or _load_backend_factory()
    bridge_runner = bridge_runner or _run_external_bridge
    cli_binary_resolver = cli_binary_resolver or _resolve_cua_driver_binary
    backend = backend_factory()

    report: dict[str, Any] = {
        "schema": 1,
        "status": "blocked",
        "intent": intent,
        "app": args.app,
        "send": bool(args.send),
        "assumption": "operator has opened the intended Telegram chat before running with --send",
    }

    if not backend.is_available():
        unavailable = RuntimeError(f"{_backend_label} CUA backend is not available on this host")
        if _allow_fallback:
            return _run_fallback_chain(
                args, intent, backend_error=unavailable, bridge_runner=bridge_runner,
                cli_binary_resolver=cli_binary_resolver,
            )
        report["error"] = str(unavailable)
        return report

    capture = None
    try:
        backend.start()
        report["backend"] = {
            "selected": _backend_label,
            "fallback_used": _backend_label != "mcp",
        }
        target_app = args.app
        focus = backend.focus_app(target_app, raise_window=False)
        report["focus"] = {
            "ok": bool(getattr(focus, "ok", False)),
            "message": getattr(focus, "message", ""),
        }
        if not getattr(focus, "ok", False):
            try:
                report["apps"] = backend.list_apps()
            except Exception as exc:
                report["apps_error"] = str(exc)
                report["status"] = "telegram_not_found"
                return report
            for candidate in _candidate_app_names(report.get("apps") or [], args.app):
                retry = backend.focus_app(candidate, raise_window=False)
                if getattr(retry, "ok", False):
                    target_app = candidate
                    focus = retry
                    report["focus"] = {
                        "ok": True,
                        "message": getattr(retry, "message", ""),
                        "retry_app": candidate,
                    }
                    break
        if not getattr(focus, "ok", False):
            windows = _diagnose_app_windows(backend, args.app)
            if windows:
                report["windows"] = windows
                report["status"] = "telegram_window_not_on_current_space"
                return report
            report["status"] = "telegram_not_found"
            return report

        capture = backend.capture(mode=args.mode, app=target_app)
        report["capture"] = _capture_summary(capture)
        if not getattr(capture, "app", ""):
            report["status"] = "telegram_capture_empty"
            return report

        if args.send:
            typed = backend.type_text(intent)
            report["type"] = {
                "ok": bool(getattr(typed, "ok", False)),
                "message": getattr(typed, "message", ""),
            }
            if not getattr(typed, "ok", False):
                report["status"] = "type_failed"
                return report
            if not args.no_enter:
                pressed = backend.key("return")
                report["enter"] = {
                    "ok": bool(getattr(pressed, "ok", False)),
                    "message": getattr(pressed, "message", ""),
                }
                if not getattr(pressed, "ok", False):
                    report["status"] = "enter_failed"
                    return report
            report["status"] = "sent_review_required"
        else:
            report["status"] = "screenshot_review_required"
    except Exception as exc:
        if _allow_fallback and _is_missing_mcp(exc):
            return _run_fallback_chain(
                args, intent, backend_error=exc, bridge_runner=bridge_runner,
                cli_binary_resolver=cli_binary_resolver,
            )
        report["status"] = "blocked"
        report["error"] = str(exc)
    finally:
        try:
            if capture is not None:
                report["evidence"] = _write_evidence(report, capture, evidence_dir)
        finally:
            try:
                backend.stop()
            except Exception:
                pass

    return report


def format_report(report: dict[str, Any]) -> str:
    lines = [f"Telegram CUA smoke: {str(report.get('status')).upper()}"]
    lines.append(f"Intent: {report.get('intent')}")
    lines.append(f"App: {report.get('app')}")
    if report.get("send"):
        lines.append("Mode: send")
    else:
        lines.append("Mode: dry-run capture only")
    capture = report.get("capture") or {}
    if capture:
        lines.append(
            "Capture: "
            f"{capture.get('app') or '?'} "
            f"{capture.get('width')}x{capture.get('height')} "
            f"elements={capture.get('elements')}"
        )
    evidence = report.get("evidence") or {}
    if evidence:
        lines.append("Evidence:")
        for key, path in sorted(evidence.items()):
            lines.append(f"- {key}: {path}")
    if report.get("error"):
        lines.append(f"Error: {report.get('error')}")
    if report.get("status") == "screenshot_review_required":
        lines.append("Review the screenshot, then rerun with --send if the intended chat/composer is ready.")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--message", help="Plain text to type into the currently open Telegram chat.")
    group.add_argument("--command", help="Slash command to type into the currently open Telegram chat.")
    parser.add_argument("--send", action="store_true", help="Actually type and press Return.")
    parser.add_argument("--no-enter", action="store_true", help="With --send, type but do not press Return.")
    parser.add_argument("--app", default="Telegram", help="macOS app name reported by CUA.")
    parser.add_argument("--mode", choices=("som", "ax", "vision"), default="som")
    parser.add_argument("--evidence-dir", default=str(_default_evidence_dir()))
    parser.add_argument(
        "--cua-bridge-command",
        default="",
        help=(
            "Optional last-resort external CUA bridge after the Python MCP backend "
            "and local cua-driver CLI fallback fail."
        ),
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = run_smoke(args)
    except ValueError as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_report(report))

    return 0 if str(report.get("status")) in {"screenshot_review_required", "sent_review_required"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
