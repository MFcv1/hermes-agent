#!/usr/bin/env python3
"""Supervise Hermes through Telegram/CUA and machine-readable control planes.

This is a read-only first layer for the Codex Supervisor Mode. It can send one
instruction to Telegram through the existing CUA helper, then collect evidence
from Cockpit, GitHub, and an optional deploy URL. It never approves, deploys,
merges, or deletes anything by itself.
"""

from __future__ import annotations

import argparse
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
    snapshot["cockpit_ok"] = _cockpit_ok(cockpit)
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


def _cockpit_ok(cockpit: dict[str, Any]) -> bool:
    if cockpit.get("skipped"):
        return True
    endpoints = cockpit.get("endpoints") or {}
    required = ["/health", "/api/hosting/capabilities"]
    return all((endpoints.get(endpoint) or {}).get("ok") for endpoint in required)


def _github_ok(github: dict[str, Any]) -> bool:
    if github.get("skipped"):
        return True
    if not github.get("view_ok"):
        return False
    branch = github.get("branch") or {}
    return bool(branch.get("ok", True))


def _deploy_ok(deploy: dict[str, Any]) -> bool:
    return bool(deploy.get("skipped") or deploy.get("ok"))


def _telegram_ok(telegram: dict[str, Any]) -> bool:
    if telegram.get("skipped"):
        return True
    return str(telegram.get("status")) in {"screenshot_review_required", "sent_review_required"}


def _task_watch_ok(task_watch: dict[str, Any]) -> bool:
    if task_watch.get("skipped"):
        return True
    return bool(task_watch.get("terminal")) and not bool(task_watch.get("timeout")) and bool(task_watch.get("ok", True))


def summarize_status(report: dict[str, Any]) -> str:
    checks = {
        "telegram": _telegram_ok(report.get("telegram") or {}),
        "cockpit": _cockpit_ok(report.get("cockpit") or {}),
        "github": _github_ok(report.get("github") or {}),
        "deploy": _deploy_ok(report.get("deploy") or {}),
        "task_watch": _task_watch_ok(report.get("task_watch") or {"skipped": True}),
    }
    report["checks"] = checks
    if all(checks.values()):
        return "ready_for_human_review"
    return "attention_required"


def write_reports(report: dict[str, Any], report_dir: Path) -> dict[str, str]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{report['started_at'].replace(':', '').replace('-', '')}-{_safe_slug(report['intent'])}"
    json_path = report_dir / f"{stem}.json"
    md_path = report_dir / f"{stem}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(format_markdown(report), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


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
    for name, ok in sorted((report.get("checks") or {}).items()):
        lines.append(f"- `{name}`: {'OK' if ok else 'ATTENTION'}")

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
        "schema": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "intent": intent,
        "send": bool(args.send),
        "task_id": args.task_id or None,
        "github_repo": args.github_repo or None,
        "github_branch": args.github_branch or None,
        "deploy_url": args.deploy_url or None,
    }

    report["telegram"] = run_telegram(args)
    if args.wait_after_send > 0:
        time.sleep(args.wait_after_send)
    report["task_watch"] = watch_task(args)
    report["cockpit"] = collect_cockpit(args)
    report["github"] = collect_github(args)
    report["deploy"] = smoke_deploy_url(args)
    report["status"] = summarize_status(report)
    report["reports"] = write_reports(report, Path(args.report_dir).expanduser())
    return report


def format_console(report: dict[str, Any]) -> str:
    lines = [f"Codex supervisor: {str(report.get('status')).upper()}"]
    for name, ok in sorted((report.get("checks") or {}).items()):
        lines.append(f"- {'OK' if ok else 'ATTENTION'} {name}")
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
