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


def run_smoke(
    args: argparse.Namespace,
    *,
    backend_factory: Optional[Callable[[], Any]] = None,
) -> dict[str, Any]:
    intent = _message_from_args(args)
    evidence_dir = Path(args.evidence_dir).expanduser()
    backend_factory = backend_factory or _load_backend_factory()
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
        report["status"] = "missing_cua_driver"
        report["error"] = "CUA backend is not available on this host"
        return report

    capture = None
    try:
        backend.start()
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
