#!/usr/bin/env python3
"""Supervise Hermes through Telegram/CUA and machine-readable control planes.

This is a read-only first layer for the Codex Supervisor Mode. It can send one
instruction to Telegram through the existing CUA helper, then collect evidence
from Cockpit, GitHub, and an optional deploy URL. It never approves, deploys,
merges, or deletes anything by itself.
"""

from __future__ import annotations

import argparse
import enum
import importlib.util
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any


DEFAULT_COCKPIT_ENDPOINTS = [
    "/health",
    "/api/hosting/capabilities",
    "/api/internal/selfops/recommendations",
    "/api/internal/ops/weekly-report?formatted=1",
]

TASK_TERMINAL_STATUSES = {
    "blocked",
    "blocked_auth",
    "blocked_policy",
    "blocked_runtime_repair",
    "cancelled",
    "completed",
    "deployed_preview",
    "done",
    "failed",
    "needs_approval",
    "needs_merge_approval",
    "needs_review",
    "pilot_questions_required",
}

TASK_TERMINAL_PREFIXES = ("blocked_",)
TASK_SUCCESS_STATUSES = {"completed", "deployed_preview", "done"}

CHECK_NAMES = ("telegram", "cockpit", "github", "deploy", "task_watch")
MAX_REPORT_STRING_CHARS = 4000
MAX_REPORT_LIST_ITEMS = 50
MAX_REPORT_DEPTH = 10
SENSITIVE_REPORT_KEYS = {
    "access_token",
    "apikey",
    "api_key",
    "auth",
    "authorization",
    "client_secret",
    "cookie",
    "id_token",
    "jwt",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "set_cookie",
    "token",
}


class CheckOutcome(str, enum.Enum):
    """Machine-readable outcome for one independent evidence check."""

    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "gateway").is_dir() and (parent / "scripts").is_dir():
            return parent
    return Path.cwd()


def _default_report_dir() -> Path:
    return _repo_root() / "docs" / "project" / "supervisor-runs"


def _safe_slug(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    return "-".join(part for part in cleaned.split("-") if part)[:72] or "supervisor-run"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _load_script_module(name: str):
    script_path = _repo_root() / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def _load_redactor():
    """Load the repo redactor when this file is executed as a script."""

    module_path = _repo_root() / "agent" / "redact.py"
    spec = importlib.util.spec_from_file_location("codex_supervisor_redact", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.redact_sensitive_text


class CommandResult:
    def __init__(self, *, ok: bool, returncode: int | None, stdout: str, stderr: str) -> None:
        self.ok = ok
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _run_command(cmd: list[str], *, timeout: float = 30) -> CommandResult:
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return CommandResult(
            ok=completed.returncode == 0,
            returncode=completed.returncode,
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
        )
    except FileNotFoundError as exc:
        return CommandResult(ok=False, returncode=None, stdout="", stderr=str(exc))
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            ok=False,
            returncode=None,
            stdout=(exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            stderr=f"timeout after {timeout}s",
        )


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _load_cockpit_token(args: argparse.Namespace) -> str:
    if args.cockpit_token:
        return args.cockpit_token
    if os.environ.get(args.cockpit_token_env):
        return os.environ[args.cockpit_token_env]
    env_file = Path(args.local_env_file).expanduser()
    return _read_env_file(env_file).get(args.cockpit_token_env, "")


def _http_json(url: str, *, token: str = "", timeout: float = 20) -> dict[str, Any]:
    headers = {"User-Agent": "Hermes-Codex-Supervisor/1"}
    if token:
        headers["X-Internal-Token"] = token
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            parsed: Any
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = {"text": body[:4000]}
            return {
                "ok": 200 <= response.status < 300,
                "status_code": response.status,
                "body": parsed,
            }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "status_code": exc.code, "body": body[:4000]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _collect_cockpit_local(args: argparse.Namespace) -> dict[str, Any]:
    base_url = args.cockpit_base_url.rstrip("/")
    token = _load_cockpit_token(args)
    endpoints = list(DEFAULT_COCKPIT_ENDPOINTS)
    if args.task_id:
        endpoints.extend(
            [
                f"/api/internal/tasks/{args.task_id}/autonomy",
            ]
        )
    results = {}
    for endpoint in endpoints:
        results[endpoint] = _http_json(f"{base_url}{endpoint}", token=token, timeout=args.timeout)
    return {
        "mode": "http",
        "base_url": base_url,
        "token_present": bool(token),
        "endpoints": results,
    }


def _collect_cockpit_ssh(args: argparse.Namespace) -> dict[str, Any]:
    endpoints = list(DEFAULT_COCKPIT_ENDPOINTS)
    if args.task_id:
        endpoints.extend(
            [
                f"/api/internal/tasks/{args.task_id}/autonomy",
            ]
        )
    payload = json.dumps({"endpoints": endpoints, "base_url": args.cockpit_base_url.rstrip("/")})
    remote = (
        "python3 - <<'PY'\n"
        "import json, os, urllib.error, urllib.request\n"
        f"payload = json.loads({payload!r})\n"
        f"env_file = {args.vps_env_file!r}\n"
        "values = {}\n"
        "try:\n"
        "    for line in open(env_file, encoding='utf-8'):\n"
        "        line = line.strip()\n"
        "        if line and not line.startswith('#') and '=' in line:\n"
        "            k, v = line.split('=', 1)\n"
        "            values[k.strip()] = v.strip().strip(\"'\\\"\")\n"
        "except FileNotFoundError:\n"
        "    pass\n"
        f"token = values.get({args.cockpit_token_env!r}, os.environ.get({args.cockpit_token_env!r}, ''))\n"
        "out = {'token_present': bool(token), 'endpoints': {}}\n"
        "for endpoint in payload['endpoints']:\n"
        "    url = payload['base_url'].rstrip('/') + endpoint\n"
        "    req = urllib.request.Request(url, headers={'User-Agent': 'Hermes-Codex-Supervisor/1', 'X-Internal-Token': token})\n"
        "    try:\n"
        "        with urllib.request.urlopen(req, timeout=20) as resp:\n"
        "            body = resp.read().decode('utf-8', errors='replace')\n"
        "            try:\n"
        "                parsed = json.loads(body)\n"
        "            except Exception:\n"
        "                parsed = {'text': body[:4000]}\n"
        "            out['endpoints'][endpoint] = {'ok': 200 <= resp.status < 300, 'status_code': resp.status, 'body': parsed}\n"
        "    except urllib.error.HTTPError as exc:\n"
        "        out['endpoints'][endpoint] = {'ok': False, 'status_code': exc.code, 'body': exc.read().decode('utf-8', errors='replace')[:4000]}\n"
        "    except Exception as exc:\n"
        "        out['endpoints'][endpoint] = {'ok': False, 'error': str(exc)}\n"
        "print(json.dumps(out, sort_keys=True))\n"
        "PY"
    )
    result = _run_command(["ssh", args.vps_ssh, remote], timeout=args.timeout + 25)
    parsed: dict[str, Any]
    if result.ok and result.stdout:
        try:
            parsed = json.loads(result.stdout)
        except json.JSONDecodeError:
            parsed = {"parse_error": result.stdout}
    else:
        parsed = {}
    return {
        "mode": "ssh",
        "host": args.vps_ssh,
        "ok": result.ok,
        "returncode": result.returncode,
        "stderr": result.stderr,
        **parsed,
    }


def collect_cockpit(args: argparse.Namespace) -> dict[str, Any]:
    if args.skip_cockpit:
        return {"skipped": True}
    if args.vps_ssh:
        return _collect_cockpit_ssh(args)
    return _collect_cockpit_local(args)


def _task_endpoint(task_id: str) -> str:
    return f"/api/internal/tasks/{task_id}/autonomy"


def _task_payload_from_cockpit(cockpit: dict[str, Any], task_id: str) -> dict[str, Any]:
    endpoint = _task_endpoint(task_id)
    data = (cockpit.get("endpoints") or {}).get(endpoint) or {}
    body = data.get("body") if isinstance(data, dict) else None
    return body if isinstance(body, dict) else {}


def _status_is_terminal(status: str) -> bool:
    clean = status.strip().lower()
    return clean in TASK_TERMINAL_STATUSES or clean.startswith(TASK_TERMINAL_PREFIXES)


def _extract_task_snapshot(payload: dict[str, Any], task_id: str) -> dict[str, Any]:
    task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    runs = payload.get("runs") if isinstance(payload.get("runs"), list) else []
    approvals = payload.get("approvals") if isinstance(payload.get("approvals"), list) else []
    observations = payload.get("runtime_observations")
    if not isinstance(observations, list):
        observations = payload.get("observations") if isinstance(payload.get("observations"), list) else []
    latest_run = runs[0] if runs and isinstance(runs[0], dict) else {}
    status = str(task.get("status") or payload.get("status") or "unknown")
    phase = str(task.get("current_phase") or latest_run.get("phase") or "")
    deployment_url = task.get("deployment_url") or task.get("preview_url") or payload.get("deployment_url") or payload.get("preview_url")
    return {
        "task_id": str(task.get("id") or payload.get("task_id") or task_id),
        "ok": bool(payload.get("ok", True)),
        "status": status,
        "terminal": _status_is_terminal(status),
        "phase": phase,
        "repo": task.get("repo"),
        "mode": task.get("mode"),
        "project_id": task.get("project_id"),
        "thread_id": task.get("thread_id"),
        "updated_at": task.get("updated_at"),
        "blocked_reason": task.get("blocked_reason"),
        "deployment_url": deployment_url,
        "runs_count": len(runs),
        "approvals_count": len(approvals),
        "pending_approvals_count": sum(1 for item in approvals if str((item or {}).get("status") or "") == "pending"),
        "observations_count": len(observations),
        "latest_run": latest_run,
    }


def _collect_task_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    cockpit = collect_cockpit(args)
    if cockpit.get("skipped"):
        return {
            "task_id": args.task_id,
            "ok": False,
            "status": "cockpit_skipped",
            "terminal": True,
            "error": "Cockpit collection is skipped",
        }
    payload = _task_payload_from_cockpit(cockpit, args.task_id)
    if not payload:
        return {
            "task_id": args.task_id,
            "ok": False,
            "status": "task_payload_missing",
            "terminal": False,
            "cockpit": cockpit,
        }
    snapshot = _extract_task_snapshot(payload, args.task_id)
    snapshot["endpoint"] = _task_endpoint(args.task_id)
    cockpit_outcome, _ = _cockpit_outcome(cockpit)
    snapshot["cockpit_ok"] = cockpit_outcome is CheckOutcome.PASS
    return snapshot


def watch_task(args: argparse.Namespace) -> dict[str, Any]:
    if not args.watch_task:
        return {"skipped": True}
    if not args.task_id:
        return {
            "ok": False,
            "status": "missing_task_id",
            "terminal": True,
            "samples": [],
            "error": "--watch-task requires --task-id",
        }

    deadline = time.monotonic() + max(0, args.watch_timeout)
    samples: list[dict[str, Any]] = []
    poll_interval = max(0.5, args.poll_interval)
    final: dict[str, Any] = {}
    while True:
        sample = _collect_task_snapshot(args)
        sample["sampled_at"] = datetime.now(timezone.utc).isoformat()
        samples.append(sample)
        final = sample
        if sample.get("terminal"):
            break
        if time.monotonic() >= deadline:
            final = {
                **sample,
                "terminal": True,
                "timeout": True,
                "status": "watch_timeout",
                "last_task_status": sample.get("status"),
            }
            samples[-1] = final
            break
        time.sleep(poll_interval)

    return {
        "ok": bool(final.get("ok")) and not bool(final.get("timeout")),
        "task_id": args.task_id,
        "status": final.get("status"),
        "terminal": bool(final.get("terminal")),
        "timeout": bool(final.get("timeout")),
        "samples": samples,
        "final": final,
    }


def collect_github(args: argparse.Namespace) -> dict[str, Any]:
    if not args.github_repo:
        return {"skipped": True}
    fields = "nameWithOwner,url,isPrivate,defaultBranchRef,pushedAt"
    view = _run_command(["gh", "repo", "view", args.github_repo, "--json", fields], timeout=args.timeout)
    report: dict[str, Any] = {
        "repo": args.github_repo,
        "view_ok": view.ok,
        "view_returncode": view.returncode,
    }
    if view.ok and view.stdout:
        try:
            report["repo_info"] = json.loads(view.stdout)
        except json.JSONDecodeError:
            report["repo_info_parse_error"] = view.stdout
    else:
        report["stderr"] = view.stderr

    if args.github_branch:
        branch = _run_command(
            [
                "gh",
                "api",
                f"repos/{args.github_repo}/branches/{args.github_branch}",
                "--jq",
                "{name: .name, sha: .commit.sha, protected: .protected}",
            ],
            timeout=args.timeout,
        )
        report["branch"] = {
            "name": args.github_branch,
            "ok": branch.ok,
            "returncode": branch.returncode,
        }
        if branch.ok and branch.stdout:
            try:
                report["branch"]["info"] = json.loads(branch.stdout)
            except json.JSONDecodeError:
                report["branch"]["parse_error"] = branch.stdout
        elif branch.stderr:
            report["branch"]["stderr"] = branch.stderr
    return report


def smoke_deploy_url(args: argparse.Namespace) -> dict[str, Any]:
    if not args.deploy_url:
        return {"skipped": True}
    started = time.monotonic()
    request = urllib.request.Request(args.deploy_url, headers={"User-Agent": "Hermes-Codex-Supervisor/1"})
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            body = response.read(512).decode("utf-8", errors="replace")
            return {
                "url": args.deploy_url,
                "ok": 200 <= response.status < 400,
                "status_code": response.status,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "body_prefix": body,
            }
    except urllib.error.HTTPError as exc:
        return {"url": args.deploy_url, "ok": False, "status_code": exc.code}
    except Exception as exc:
        return {"url": args.deploy_url, "ok": False, "error": str(exc)}


def run_telegram(args: argparse.Namespace) -> dict[str, Any]:
    if args.skip_telegram:
        return {"skipped": True}
    telegram_smoke = _load_script_module("telegram_desktop_cua_smoke")
    smoke_args = argparse.Namespace(
        message=args.message,
        command=args.command,
        send=args.send,
        no_enter=args.no_enter,
        app=args.telegram_app,
        mode=args.cua_mode,
        evidence_dir=args.evidence_dir,
        json=False,
    )
    return telegram_smoke.run_smoke(smoke_args)


def _cockpit_outcome(cockpit: dict[str, Any]) -> tuple[CheckOutcome, str]:
    if cockpit.get("skipped"):
        return CheckOutcome.SKIPPED, "Cockpit collection was skipped"
    endpoints = cockpit.get("endpoints") or {}
    required = ["/health", "/api/hosting/capabilities"]
    if not endpoints or any(endpoint not in endpoints for endpoint in required):
        return CheckOutcome.UNKNOWN, "Required Cockpit endpoint evidence is missing"
    if all((endpoints.get(endpoint) or {}).get("ok") is True for endpoint in required):
        return CheckOutcome.PASS, "Required Cockpit endpoints passed"
    return CheckOutcome.FAIL, "At least one required Cockpit endpoint failed"


def _github_outcome(github: dict[str, Any]) -> tuple[CheckOutcome, str]:
    if github.get("skipped"):
        return CheckOutcome.SKIPPED, "GitHub collection was skipped"
    if "view_ok" not in github:
        return CheckOutcome.UNKNOWN, "GitHub repository evidence is missing"
    if github.get("view_ok") is not True:
        return CheckOutcome.FAIL, "GitHub repository lookup failed"
    branch = github.get("branch") or {}
    if branch and "ok" not in branch:
        return CheckOutcome.UNKNOWN, "GitHub branch evidence is incomplete"
    if branch.get("ok", True) is not True:
        return CheckOutcome.FAIL, "GitHub branch lookup failed"
    return CheckOutcome.PASS, "Requested GitHub evidence passed"


def _deploy_outcome(deploy: dict[str, Any]) -> tuple[CheckOutcome, str]:
    if deploy.get("skipped"):
        return CheckOutcome.SKIPPED, "Deploy smoke was skipped"
    if "ok" not in deploy:
        return CheckOutcome.UNKNOWN, "Deploy smoke evidence is missing"
    if deploy.get("ok") is True:
        return CheckOutcome.PASS, "Deploy smoke passed"
    return CheckOutcome.FAIL, "Deploy smoke failed"


def _telegram_outcome(telegram: dict[str, Any]) -> tuple[CheckOutcome, str]:
    if telegram.get("skipped"):
        return CheckOutcome.SKIPPED, "Telegram/CUA check was skipped"
    status = str(telegram.get("status") or "")
    if not status:
        return CheckOutcome.UNKNOWN, "Telegram/CUA status is missing"
    if status in {"screenshot_review_required", "sent_review_required"}:
        return CheckOutcome.PASS, f"Telegram/CUA reached {status}"
    return CheckOutcome.FAIL, f"Telegram/CUA ended with {status}"


def _task_watch_outcome(task_watch: dict[str, Any]) -> tuple[CheckOutcome, str]:
    if task_watch.get("skipped"):
        return CheckOutcome.SKIPPED, "Task watch was skipped"
    if task_watch.get("status") in {"missing_task_id", "cockpit_skipped", "task_payload_missing"}:
        return CheckOutcome.UNKNOWN, f"Task evidence unavailable: {task_watch.get('status')}"
    if not {"terminal", "timeout"}.issubset(task_watch):
        return CheckOutcome.UNKNOWN, "Task watch evidence is incomplete"
    if task_watch.get("timeout") or task_watch.get("ok") is False:
        return CheckOutcome.FAIL, "Task watch failed or timed out"
    status = str(task_watch.get("status") or "").strip().lower()
    if task_watch.get("terminal") and status in TASK_SUCCESS_STATUSES:
        return CheckOutcome.PASS, f"Task completed with {status}"
    if task_watch.get("terminal") and _status_is_terminal(status):
        return CheckOutcome.FAIL, f"Task requires attention with terminal status {status}"
    return CheckOutcome.UNKNOWN, "Task terminal outcome is not recognized"


CHECK_EVALUATORS = {
    "telegram": _telegram_outcome,
    "cockpit": _cockpit_outcome,
    "github": _github_outcome,
    "deploy": _deploy_outcome,
    "task_watch": _task_watch_outcome,
}


def _required_checks(report: dict[str, Any]) -> dict[str, bool]:
    """Derive evidence obligations from the requested run, not its result."""

    task_requested = bool(report.get("task_id") or report.get("watch_task"))
    return {
        "telegram": not bool(report.get("skip_telegram")),
        "cockpit": not bool(report.get("skip_cockpit")) or task_requested,
        "github": bool(report.get("github_repo") or report.get("github_branch")),
        "deploy": bool(report.get("deploy_url")),
        "task_watch": task_requested,
    }


def summarize_status(report: dict[str, Any]) -> str:
    requirements = _required_checks(report)
    checks: dict[str, dict[str, Any]] = {}
    for name in CHECK_NAMES:
        outcome, reason = CHECK_EVALUATORS[name](report.get(name) or {})
        checks[name] = {
            "outcome": outcome.value,
            "required": requirements[name],
            "reason": reason,
        }
    report["checks"] = checks
    # Temporary, lossy schema-v1 bridge. It remains boolean for simple
    # consumers but is fail-closed: only an explicit pass maps to true.
    report["legacy_checks"] = {
        name: check["outcome"] == CheckOutcome.PASS.value for name, check in checks.items()
    }
    if any(check["outcome"] == CheckOutcome.FAIL.value for check in checks.values()):
        return "attention_required"
    if any(
        check["required"] and check["outcome"] in {CheckOutcome.SKIPPED.value, CheckOutcome.UNKNOWN.value}
        for check in checks.values()
    ):
        return "incomplete_evidence"
    return "ready_for_human_review"


def write_reports(report: dict[str, Any], report_dir: Path) -> dict[str, str]:
    report = sanitize_report(report)
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{report['started_at'].replace(':', '').replace('-', '')}-{_safe_slug(report['intent'])}"
    json_path = report_dir / f"{stem}.json"
    md_path = report_dir / f"{stem}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(format_markdown(report), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def _sanitize_report_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    """Redact secrets and bound untrusted evidence before any report/output."""

    if depth >= MAX_REPORT_DEPTH:
        return "[TRUNCATED: maximum report depth reached]"
    sensitive_key = key.lower().replace("-", "_")
    if sensitive_key in SENSITIVE_REPORT_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        items = list(value.items())
        result = {
            str(item_key): _sanitize_report_value(item_value, key=str(item_key), depth=depth + 1)
            for item_key, item_value in items[:MAX_REPORT_LIST_ITEMS]
        }
        if len(items) > MAX_REPORT_LIST_ITEMS:
            result["_truncated_keys"] = len(items) - MAX_REPORT_LIST_ITEMS
        return result
    if isinstance(value, (list, tuple)):
        result = [_sanitize_report_value(item, depth=depth + 1) for item in value[:MAX_REPORT_LIST_ITEMS]]
        if len(value) > MAX_REPORT_LIST_ITEMS:
            result.append(f"[TRUNCATED: {len(value) - MAX_REPORT_LIST_ITEMS} additional items]")
        return result
    if isinstance(value, str):
        redacted = _load_redactor()(value, force=True)
        if len(redacted) > MAX_REPORT_STRING_CHARS:
            omitted = len(redacted) - MAX_REPORT_STRING_CHARS
            return f"{redacted[:MAX_REPORT_STRING_CHARS]}\n[TRUNCATED: {omitted} characters omitted]"
        return redacted
    return value


def sanitize_report(report: dict[str, Any]) -> dict[str, Any]:
    sanitized = _sanitize_report_value(report)
    return sanitized if isinstance(sanitized, dict) else {}


def format_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Codex Supervisor Run",
        "",
        f"- Started: `{report.get('started_at')}`",
        f"- Status: `{report.get('status')}`",
        f"- Intent: `{report.get('intent')}`",
        "",
        "## Checks",
        "",
    ]
    for name, check in sorted((report.get("checks") or {}).items()):
        lines.append(
            f"- `{name}`: `{check.get('outcome', 'unknown')}` "
            f"(required: `{bool(check.get('required'))}`) — {check.get('reason', '')}"
        )

    telegram = report.get("telegram") or {}
    lines.extend(["", "## Telegram/CUA", ""])
    if telegram.get("skipped"):
        lines.append("- Skipped.")
    else:
        lines.append(f"- Status: `{telegram.get('status')}`")
        evidence = telegram.get("evidence") or {}
        for key, path in sorted(evidence.items()):
            lines.append(f"- Evidence {key}: `{path}`")

    cockpit = report.get("cockpit") or {}
    lines.extend(["", "## Cockpit", ""])
    if cockpit.get("skipped"):
        lines.append("- Skipped.")
    else:
        lines.append(f"- Mode: `{cockpit.get('mode')}`")
        lines.append(f"- Token present: `{bool(cockpit.get('token_present'))}`")
        for endpoint, data in sorted((cockpit.get("endpoints") or {}).items()):
            status = data.get("status_code", data.get("error", "?"))
            marker = "OK" if data.get("ok") else "ATTENTION"
            lines.append(f"- {marker} `{endpoint}` -> `{status}`")

    task_watch = report.get("task_watch") or {}
    lines.extend(["", "## Task Watch", ""])
    if task_watch.get("skipped"):
        lines.append("- Skipped.")
    else:
        final = task_watch.get("final") or {}
        lines.append(f"- Task: `{task_watch.get('task_id')}`")
        lines.append(f"- Status: `{task_watch.get('status')}`")
        lines.append(f"- Samples: `{len(task_watch.get('samples') or [])}`")
        lines.append(f"- Timeout: `{bool(task_watch.get('timeout'))}`")
        if final.get("phase"):
            lines.append(f"- Phase: `{final.get('phase')}`")
        if final.get("repo"):
            lines.append(f"- Repo: `{final.get('repo')}`")
        if final.get("deployment_url"):
            lines.append(f"- Deployment URL: `{final.get('deployment_url')}`")

    github = report.get("github") or {}
    lines.extend(["", "## GitHub", ""])
    if github.get("skipped"):
        lines.append("- Skipped.")
    else:
        lines.append(f"- Repo: `{github.get('repo')}`")
        lines.append(f"- View OK: `{github.get('view_ok')}`")
        branch = github.get("branch") or {}
        if branch:
            lines.append(f"- Branch `{branch.get('name')}` OK: `{branch.get('ok')}`")

    deploy = report.get("deploy") or {}
    lines.extend(["", "## Deploy URL", ""])
    if deploy.get("skipped"):
        lines.append("- Skipped.")
    else:
        lines.append(f"- URL: `{deploy.get('url')}`")
        lines.append(f"- OK: `{deploy.get('ok')}`")
        if deploy.get("status_code"):
            lines.append(f"- HTTP: `{deploy.get('status_code')}`")

    lines.extend(
        [
            "",
            "## Guardrail",
            "",
            "This supervisor run did not approve, merge, deploy, delete, or change DNS by itself.",
        ]
    )
    return "\n".join(lines) + "\n"


def run_supervisor(args: argparse.Namespace) -> dict[str, Any]:
    intent = (args.command or args.message or "").strip()
    if not intent:
        raise ValueError("provide --message or --command")
    if args.command and args.message:
        raise ValueError("use only one of --message or --command")

    report: dict[str, Any] = {
        "schema": 2,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "intent": intent,
        "send": bool(args.send),
        "task_id": args.task_id or None,
        "github_repo": args.github_repo or None,
        "github_branch": args.github_branch or None,
        "deploy_url": args.deploy_url or None,
        "skip_telegram": bool(args.skip_telegram),
        "skip_cockpit": bool(args.skip_cockpit),
        "watch_task": bool(args.watch_task),
    }

    report["telegram"] = run_telegram(args)
    if args.wait_after_send > 0:
        time.sleep(args.wait_after_send)
    report["task_watch"] = watch_task(args)
    report["cockpit"] = collect_cockpit(args)
    report["github"] = collect_github(args)
    report["deploy"] = smoke_deploy_url(args)
    report["status"] = summarize_status(report)
    report = sanitize_report(report)
    report["reports"] = write_reports(report, Path(args.report_dir).expanduser())
    return report


def format_console(report: dict[str, Any]) -> str:
    lines = [f"Codex supervisor: {str(report.get('status')).upper()}"]
    for name, check in sorted((report.get("checks") or {}).items()):
        outcome = str(check.get("outcome", CheckOutcome.UNKNOWN.value)).upper()
        required = " required" if check.get("required") else " optional"
        lines.append(f"- {outcome} {name} ({required.strip()})")
    reports = report.get("reports") or {}
    if reports:
        lines.append("Reports:")
        for key, path in sorted(reports.items()):
            lines.append(f"- {key}: {path}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--message", help="Natural-language instruction for Hermes.")
    group.add_argument("--command", help="Slash command for Hermes, e.g. /status.")
    parser.add_argument("--send", action="store_true", help="Actually type/send in Telegram through CUA.")
    parser.add_argument("--no-enter", action="store_true", help="With --send, type but do not press Return.")
    parser.add_argument("--skip-telegram", action="store_true")
    parser.add_argument("--telegram-app", default="Telegram")
    parser.add_argument("--cua-mode", choices=("som", "ax", "vision"), default="som")
    parser.add_argument("--evidence-dir", default=str(Path.home() / ".hermes" / "telegram-gui-smoke"))
    parser.add_argument("--wait-after-send", type=float, default=0)

    parser.add_argument("--skip-cockpit", action="store_true")
    parser.add_argument("--cockpit-base-url", default=os.environ.get("REPO_COCKPIT_BASE_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--cockpit-token", default="")
    parser.add_argument("--cockpit-token-env", default="REPO_COCKPIT_INTERNAL_TOKEN")
    parser.add_argument("--local-env-file", default=str(Path.home() / ".hermes" / ".env"))
    parser.add_argument("--vps-ssh", default="", help="Example: root@134.122.73.242. If set, query Cockpit from the VPS.")
    parser.add_argument("--vps-env-file", default="/home/hermes/.hermes/.env")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--watch-task", action="store_true", help="Poll Cockpit until the task reaches a terminal status.")
    parser.add_argument("--watch-timeout", type=float, default=900)
    parser.add_argument("--poll-interval", type=float, default=15)

    parser.add_argument("--github-repo", default="", help="Example: MFcv1/portfolio-v2-hermes-test.")
    parser.add_argument("--github-branch", default="")
    parser.add_argument("--deploy-url", default="")
    parser.add_argument("--timeout", type=float, default=25)
    parser.add_argument("--report-dir", default=str(_default_report_dir()))
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = run_supervisor(args)
    except ValueError as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_console(report))
    return 0 if report.get("status") == "ready_for_human_review" else 2


if __name__ == "__main__":
    raise SystemExit(main())
