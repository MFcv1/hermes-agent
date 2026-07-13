"""Pure Repo Cockpit formatting helpers for gateway panels.

Extracted from the Telegram adapter for Autonomie V2 Phase 1. Keep this module
side-effect free: no bot objects, no network, no persistence.
"""

from __future__ import annotations

import html as _html
import json
import re
from datetime import datetime, timezone
from typing import Any


def pending_pr_label(item: dict[str, Any]) -> str:
    repo = str(item.get("repo") or "")
    task_id = str(item.get("task_id") or "")
    title = str(item.get("title") or "")
    blob = f"{repo} {title}".lower()
    if "tennis" in blob:
        project = "tennis"
    else:
        project = repo.rsplit("/", 1)[-1] if repo else "projet"
    project = re.sub(r"[^a-zA-Z0-9_-]+", "-", project).strip("-") or "projet"
    if len(project) > 18:
        project = project[:18].rstrip("-")
    suffix = task_id[-6:] if task_id else ""
    return f"{project} · {suffix}" if suffix else project


def format_pending_prs(data: dict[str, Any]) -> str:
    prs = data.get("prs") or []
    lines = ["<b>🔀 PRs en attente</b>", ""]
    if not prs:
        lines.append("Aucune PR en attente côté Repo Cockpit.")
        return "\n".join(lines)
    for idx, item in enumerate(prs[:10], 1):
        task_id = str(item.get("task_id") or "")
        repo = str(item.get("repo") or "")
        status = str(item.get("status") or "")
        title = str(item.get("title") or "Tâche Hermes")
        branch = str(item.get("branch") or "")
        updated = item.get("updated_at")
        updated_txt = ""
        try:
            updated_txt = datetime.fromtimestamp(int(updated), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            updated_txt = str(updated or "")
        lines.extend([
            f"<b>{idx}. {_html.escape(repo)}</b>",
            f"{_html.escape(title[:120])}",
            f"Status : <code>{_html.escape(status)}</code>",
            f"Task : <code>{_html.escape(task_id)}</code>",
        ])
        if branch:
            lines.append(f"Branche : <code>{_html.escape(branch)}</code>")
        smoke = item.get("smoke_status")
        if smoke is not None:
            lines.append(f"Smoke : <code>{_html.escape(str(smoke))}</code>")
        if updated_txt:
            lines.append(f"Maj : <code>{_html.escape(updated_txt)}</code>")
        lines.append("")
    lines.append("Détail : <code>/status op_xxx</code> ou <code>/runs op_xxx</code>")
    return "\n".join(lines).strip()


def status_badge(status: str | None) -> str:
    value = str(status or "unknown")
    if value in {"passed", "ready", "done", "completed", "fixed", "ok", "success", "approved"} or value.startswith("running"):
        return "✅"
    if value.startswith("blocked") or value in {"failed", "error", "worsened", "rolled_back", "denied"}:
        return "🚨"
    if value in {"queued", "pending"} or value.startswith("queued"):
        return "⏳"
    return "•"


def latest_items(data: dict[str, Any], key: str, limit: int = 3) -> list[dict[str, Any]]:
    items = data.get(key) or []
    if not isinstance(items, list):
        return []
    return items[:limit]


def _status_counts(items: list[dict[str, Any]], key: str = "status") -> str:
    counts: dict[str, int] = {}
    for item in items:
        status = str(item.get(key) or "unknown")
        counts[status] = counts.get(status, 0) + 1
    if not counts:
        return "0"
    ordered = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    return ", ".join(f"{count} {status}" for status, count in ordered[:4])


def _evaluation_summary_line(data: dict[str, Any]) -> str:
    summary = data.get("evaluation_summary") or {}
    suites = summary.get("suites") if isinstance(summary, dict) else {}
    if not isinstance(suites, dict) or not suites:
        return "not_run"
    parts = []
    for suite, item in sorted(suites.items()):
        if not isinstance(item, dict):
            continue
        total = int(item.get("total") or 0)
        passed = int(item.get("passed") or 0)
        if total:
            parts.append(f"{suite} {passed}/{total}")
        else:
            parts.append(f"{suite} not_run")
    return ", ".join(parts[:3]) or "not_run"


def _short_observation_label(item: dict[str, Any]) -> str:
    signature = str(item.get("signature") or "")
    source = str(item.get("source") or "")
    status = str(item.get("status") or "")
    label = signature or source or item.get("id") or "observation"
    return f"{status} · {label}" if status else str(label)


def preview_is_blocked(status: str) -> bool:
    return status in {
        "blocked_deploy",
        "blocked_smoke",
        "blocked_release_gate",
        "blocked_pr_required",
        "blocked_review_required",
        "blocked_tests",
    }


def status_is_problem(status: str) -> bool:
    value = str(status or "")
    return value.startswith("blocked") or value in {"failed", "error"}


def format_pr_summary(data: dict[str, Any]) -> str:
    task = data.get("task") or {}
    task_id = str(task.get("id") or data.get("task_id") or "")
    result = task.get("result_json")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except Exception:
            result = {}
    result = result if isinstance(result, dict) else {}
    pr = result.get("pr") if isinstance(result.get("pr"), dict) else {}
    pr_url = pr.get("pr_url") or pr.get("url") or result.get("pr_url")
    preview = task.get("preview_url") or task.get("deployment_url") or result.get("preview_url") or result.get("deployment_url")
    branch_result = result.get("branch_result")
    if not isinstance(branch_result, dict):
        branch_result = {}
    branch = (
        pr.get("branch")
        or pr.get("head")
        or result.get("branch")
        or branch_result.get("effective_branch")
    )
    lines = [
        "<b>🧾 Résumé PR</b>",
        "",
        f"Task : <code>{_html.escape(task_id)}</code>",
        f"Repo : <code>{_html.escape(str(task.get('repo') or ''))}</code>",
        f"Statut : <b>{_html.escape(str(task.get('status') or ''))}</b>",
        f"Mode : <code>{_html.escape(str(task.get('mode') or ''))}</code>",
    ]
    if branch:
        lines.append(f"Branche : <code>{_html.escape(str(branch))}</code>")
    if pr_url:
        lines.append(f"PR : {_html.escape(str(pr_url))}")
    if preview:
        lines.append(f"Preview : {_html.escape(str(preview))}")
    smokes = data.get("smoke_tests") or []
    if smokes:
        latest = smokes[0]
        lines.append(f"Smoke : <code>{_html.escape(str(latest.get('status') or ''))}</code>")
    checks = data.get("provider_checks") or []
    if checks:
        ok = sum(1 for item in checks if str(item.get("status") or "").lower() in {"passed", "ok", "ready"})
        lines.append(f"Provider checks : <code>{ok}/{len(checks)} OK</code>")
    runs = data.get("task_runs") or []
    if runs:
        lines.extend(["", "<b>Dernières étapes</b>"])
        for item in runs[:5]:
            phase = str(item.get("phase") or item.get("id") or "")
            status = str(item.get("status") or "")
            lines.append(f"{status_badge(status)} <code>{_html.escape(phase[:70])}</code> · {_html.escape(status)}")
    lines.extend([
        "",
        "Pour continuer dans ce chat : écris une nouvelle demande. Hermes utilisera le projet/thread actif.",
        "Pour changer de mode ou de projet : <code>/new</code> ou <code>/conv</code>.",
    ])
    return "\n".join(lines)


def format_autonomy_status(data: dict[str, Any]) -> str:
    task = data.get("task") or {}
    status = str(task.get("status") or "")
    error_events = data.get("error_events") or []
    task_runs = data.get("task_runs") if isinstance(data.get("task_runs"), list) else []
    repairs = data.get("repair_attempts") if isinstance(data.get("repair_attempts"), list) else []
    observations = data.get("runtime_observations") if isinstance(data.get("runtime_observations"), list) else []
    approvals = data.get("approvals") if isinstance(data.get("approvals"), list) else []
    latest_error = {}
    if status_is_problem(status):
        latest_error = (data.get("latest_error") or (error_events[0] if isinstance(error_events, list) and error_events else {}))
    lines = [
        "<b>🛰️ Status autonomie</b>",
        "",
        f"Task : <code>{_html.escape(str(task.get('id') or data.get('task_id') or ''))}</code>",
        f"Repo : <code>{_html.escape(str(task.get('repo') or ''))}</code>",
        f"Statut : <b>{_html.escape(status)}</b>",
        f"Phase : <code>{_html.escape(str(task.get('current_phase') or ''))}</code>",
        f"Mode : <code>{_html.escape(str(task.get('mode') or ''))}</code>",
    ]
    parent = task.get("parent_task_id")
    if parent:
        lines.append(f"Reprise : <code>{_html.escape(str(parent))}</code>")
    preview = task.get("preview_url") or task.get("deployment_url")
    if preview:
        label = "Preview non validée" if preview_is_blocked(status) else "Preview"
        lines.append(f"{label} : {_html.escape(str(preview))}")
    lines.extend([
        "",
        "<b>Vue rapide</b>",
        f"Runs : <code>{_html.escape(_status_counts(task_runs))}</code>",
        f"Repairs : <code>{_html.escape(_status_counts(repairs))}</code>",
        f"Observations : <code>{len(observations)}</code>",
        f"Approvals : <code>{_html.escape(_status_counts(approvals))}</code>",
    ])
    eval_line = _evaluation_summary_line(data)
    if eval_line:
        lines.append(f"Evals : <code>{_html.escape(eval_line)}</code>")
    if latest_error:
        lines.extend([
            "",
            "<b>Dernière erreur classée</b>",
            f"Catégorie : <code>{_html.escape(str(latest_error.get('category') or ''))}</code>",
            f"Runbook : <code>{_html.escape(str(latest_error.get('runbook') or ''))}</code>",
            f"Humain requis : <code>{_html.escape(str(latest_error.get('human_action_required') or False))}</code>",
        ])
    provider_checks = latest_items(data, "provider_checks")
    if provider_checks:
        lines.extend(["", "<b>Provider checks</b>"])
        for item in provider_checks:
            lines.append(
                f"{status_badge(item.get('status'))} {_html.escape(str(item.get('provider') or ''))}/"
                f"{_html.escape(str(item.get('check_name') or ''))} : <code>{_html.escape(str(item.get('status') or ''))}</code>"
            )
    smokes = latest_items(data, "smoke_tests")
    if smokes:
        lines.extend(["", "<b>Smoke tests</b>"])
        for item in smokes:
            lines.append(f"{status_badge(item.get('status'))} <code>{_html.escape(str(item.get('status') or ''))}</code> · {_html.escape(str(item.get('url') or ''))[:120]}")
    if task_runs:
        lines.extend(["", "<b>Runs récents</b>"])
        for item in latest_items(data, "task_runs"):
            phase = str(item.get("phase") or item.get("id") or "")
            run_status = str(item.get("status") or "")
            lines.append(f"{status_badge(run_status)} <code>{_html.escape(phase[:70])}</code> · {_html.escape(run_status)}")
    if repairs:
        lines.extend(["", "<b>Réparations</b>"])
        for item in latest_items(data, "repair_attempts"):
            runbook = str(item.get("runbook") or item.get("id") or "")
            repair_status = str(item.get("status") or "")
            attempt = item.get("attempt")
            suffix = f" · tentative {attempt}" if attempt else ""
            lines.append(f"{status_badge(repair_status)} <code>{_html.escape(runbook[:70])}</code> · {_html.escape(repair_status)}{_html.escape(suffix)}")
    if observations:
        lines.extend(["", "<b>Observations runtime</b>"])
        for item in latest_items(data, "runtime_observations"):
            label = _short_observation_label(item)
            lines.append(f"{status_badge(item.get('status'))} <code>{_html.escape(label[:90])}</code>")
    if approvals:
        pending = [item for item in approvals if str(item.get("status") or "") == "pending"]
        shown = pending or approvals
        lines.extend(["", "<b>Approvals</b>"])
        for item in shown[:3]:
            label = str(item.get("approval_type") or item.get("id") or "")
            approval_status = str(item.get("status") or "")
            lines.append(f"{status_badge(approval_status)} <code>{_html.escape(label[:70])}</code> · {_html.escape(approval_status)}")
    runbooks = latest_items(data, "runbooks_applied")
    if runbooks:
        lines.extend(["", "<b>Runbooks</b>"])
        for item in runbooks:
            lines.append(f"{status_badge(item.get('status'))} <code>{_html.escape(str(item.get('runbook') or ''))}</code> · {_html.escape(str(item.get('status') or ''))}")
    lines.append("\nDétail technique : <code>/runs " + _html.escape(str(task.get("id") or data.get("task_id") or "")) + "</code>")
    return "\n".join(lines)


def format_runs_status(data: dict[str, Any]) -> str:
    task = data.get("task") or {}
    task_id = str(task.get("id") or data.get("task_id") or "")
    lines = [
        "<b>🧪 Runs / gates</b>",
        "",
        f"Task : <code>{_html.escape(task_id)}</code>",
        f"Statut : <b>{_html.escape(str(task.get('status') or ''))}</b>",
    ]
    for section, title, name_key in [
        ("task_runs", "Runs worker", "phase"),
        ("repair_attempts", "Réparations", "runbook"),
        ("smoke_tests", "Smoke tests", "url"),
        ("runbooks_applied", "Runbooks appliqués", "runbook"),
        ("deployments", "Deployments", "provider"),
    ]:
        items = latest_items(data, section, 6)
        if not items:
            continue
        lines.extend(["", f"<b>{_html.escape(title)}</b>"])
        for item in items:
            label = str(item.get(name_key) or item.get("check_name") or item.get("id") or "")
            status = str(item.get("status") or "")
            lines.append(f"{status_badge(status)} <code>{_html.escape(label)[:80]}</code> · <b>{_html.escape(status)}</b>")
    return "\n".join(lines)


# Backward-compatible aliases matching old TelegramAdapter method names.
_pending_pr_label = pending_pr_label
_format_pending_prs = format_pending_prs
_status_badge = status_badge
_latest_items = latest_items
_format_pr_summary = format_pr_summary
_format_autonomy_status = format_autonomy_status
_format_runs_status = format_runs_status
_preview_is_blocked = preview_is_blocked
_status_is_problem = status_is_problem
