"""Repo Cockpit Telegram keyboard builders.

Extracted from the Telegram adapter for Autonomie V2 Phase 1. The helpers keep
Telegram SDK classes injectable so importing this module does not require the
optional ``python-telegram-bot`` dependency.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from gateway.repo_cockpit_formatting import pending_pr_label, preview_is_blocked

REPO_COCKPIT_MODES = {"ask_review", "pilote", "autopilot"}

ButtonFactory = Callable[..., Any]
MarkupFactory = Callable[[list[list[Any]]], Any]


def normalize_cockpit_mode(mode: str | None) -> str:
    clean = str(mode or "").strip().lower()
    return clean if clean in REPO_COCKPIT_MODES else "ask_review"


def new_chat_keyboard(mode: str, *, button: ButtonFactory, markup: MarkupFactory) -> Any:
    mode = normalize_cockpit_mode(mode)
    return markup([
        [
            button(("✓ Ask review" if mode == "ask_review" else "Ask review"), callback_data="rcn:mode:ask_review"),
            button(("✓ Pilote" if mode == "pilote" else "Pilote"), callback_data="rcn:mode:pilote"),
        ],
        [
            button(("✓ Autopilot" if mode == "autopilot" else "Autopilot"), callback_data="rcn:mode:autopilot"),
        ],
        [button("Projet GitHub existant", callback_data=f"rcn:existing:{mode}")],
        [button("Start from scratch", callback_data=f"rcn:scratch:{mode}")],
        [button("Annuler", callback_data="rcn:cancel")],
    ])


def pilot_existing_intent_keyboard(mode: str = "pilote", *, button: ButtonFactory, markup: MarkupFactory) -> Any:
    mode = normalize_cockpit_mode(mode)
    return markup([
        [button("Comprendre / auditer le repo", callback_data=f"rcn:intent:audit_repo:{mode}")],
        [button("Modifier / ajouter une feature", callback_data=f"rcn:intent:feature_work:{mode}")],
        [button("Corriger un bug", callback_data=f"rcn:intent:debug_fix:{mode}")],
        [button("Déployer / vérifier prod", callback_data=f"rcn:intent:deploy:{mode}")],
        [button("Refactor / sécuriser", callback_data=f"rcn:intent:review_harden:{mode}")],
        [button("Je ne sais pas", callback_data=f"rcn:intent:pilot_discovery:{mode}")],
        [button("Retour", callback_data=f"rcn:mode:{mode}"), button("Annuler", callback_data="rcn:cancel")],
    ])


def repo_button_label(repo: dict[str, Any]) -> str:
    full_name = str(repo.get("nameWithOwner") or repo.get("name") or "Repo")
    name = full_name.split("/", 1)[-1]
    clean = re.sub(r"\s+", " ", name).strip() or "Repo"
    if len(clean) > 30:
        clean = clean[:29].rstrip() + "…"
    visibility = "privé" if repo.get("isPrivate") else "public"
    return f"{clean} · {visibility}"


def repo_new_chat_keyboard(
    user_id: str,
    mode: str,
    repos: list[dict[str, Any]],
    cockpit_url: str,
    *,
    button: ButtonFactory,
    markup: MarkupFactory,
    web_app_info: Callable[..., Any] | None = None,
) -> Any:
    del user_id
    mode = normalize_cockpit_mode(mode)
    rows: list[list[Any]] = []
    for index, repo in enumerate(repos[:8]):
        if not isinstance(repo, dict) or not repo.get("nameWithOwner"):
            continue
        rows.append([
            button(
                repo_button_label(repo),
                callback_data=f"rcnr:{mode}:{index}",
            )
        ])
    if not rows:
        rows.append([button("Actualiser les repos", callback_data=f"rcn:existing:{mode}")])
    button_kwargs = (
        {"web_app": web_app_info(url=cockpit_url)}
        if web_app_info is not None
        else {"url": cockpit_url}
    )
    rows.append([button("Mini App liste complète", **button_kwargs)])
    rows.append([
        button("Actualiser", callback_data=f"rcn:existing:{mode}"),
        button("Annuler", callback_data="rcn:cancel"),
    ])
    return markup(rows)


def repo_selected_keyboard(mode: str, *, button: ButtonFactory, markup: MarkupFactory) -> Any:
    mode = normalize_cockpit_mode(mode)
    return markup([
        [
            button("Changer repo", callback_data=f"rcn:existing:{mode}"),
            button("Ask review", callback_data="rcn:mode:ask_review"),
        ],
        [
            button("Pilote", callback_data="rcn:mode:pilote"),
            button("Autopilot", callback_data="rcn:mode:autopilot"),
        ],
        [button("Annuler", callback_data="rcn:cancel")],
    ])


def pending_prs_keyboard(data: dict[str, Any], *, button: ButtonFactory, markup: MarkupFactory) -> Any:
    rows: list[list[Any]] = []
    for item in (data.get("prs") or [])[:5]:
        task_id = str(item.get("task_id") or "")
        pr_url = str(item.get("pr_url") or "")
        preview_url = str(item.get("preview_url") or "")
        label = pending_pr_label(item)
        if pr_url.startswith(("https://", "http://")):
            rows.append([button(f"PR {label}", url=pr_url)])
        if preview_url.startswith(("https://", "http://")):
            rows.append([button(f"Preview {label}", url=preview_url)])
        if task_id.startswith("op_"):
            rows.append([
                button(f"Status {label}", callback_data=f"rca:status:{task_id}"),
                button(f"Runs {label}", callback_data=f"rca:runs:{task_id}"),
            ])
            rows.append([button(f"Résumé {label}", callback_data=f"rca:prsum:{task_id}")])
    rows.append([button("Rafraîchir PRs", callback_data="rca:prs")])
    rows.append([button("Threads", callback_data="rct:list:all")])
    return markup(rows)


def autonomy_keyboard(data: dict[str, Any], view: str = "status", *, button: ButtonFactory, markup: MarkupFactory) -> Any:
    task = data.get("task") or {}
    task_id = str(task.get("id") or data.get("task_id") or "")
    status = str(task.get("status") or "")
    preview = str(task.get("preview_url") or task.get("deployment_url") or "")
    rows: list[list[Any]] = []
    if preview.startswith(("https://", "http://")) and not preview_is_blocked(status):
        rows.append([button("Ouvrir preview", url=preview)])
    if task_id:
        if view == "runs":
            rows.append([
                button("Status", callback_data=f"rca:status:{task_id}"),
                button("Rafraîchir", callback_data=f"rca:runs:{task_id}"),
            ])
        else:
            rows.append([
                button("Runs", callback_data=f"rca:runs:{task_id}"),
                button("Rafraîchir", callback_data=f"rca:status:{task_id}"),
            ])
    rows.append([button("Threads", callback_data="rct:list:all")])
    return markup(rows)
