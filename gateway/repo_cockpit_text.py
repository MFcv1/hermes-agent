"""Pure Repo Cockpit text builders for Telegram-facing gateway flows."""

from __future__ import annotations

import html as _html

REPO_COCKPIT_MODES = {"ask_review", "pilote", "autopilot"}


def normalize_cockpit_mode(mode: str | None) -> str:
    clean = str(mode or "").strip().lower()
    return clean if clean in REPO_COCKPIT_MODES else "ask_review"


def mode_title(mode: str) -> str:
    mode = normalize_cockpit_mode(mode)
    if mode == "autopilot":
        return "Autopilot"
    if mode == "pilote":
        return "Pilote"
    return "Ask review"


def mode_note(mode: str) -> str:
    mode = normalize_cockpit_mode(mode)
    if mode == "autopilot":
        return "peut merger automatiquement seulement après PR, gates, secret scan et review indépendante high"
    if mode == "pilote":
        return "cadre d'abord Architect/Deploy, pose les questions critiques, puis avance en autonomie avec PR et gates"
    return "prépare, teste et ouvre une PR, puis attend ta validation avant merge"


def pilot_intent_title(intent: str | None) -> str:
    titles = {
        "architect": "Architect / cadrage",
        "deploy": "Déployer / vérifier prod",
        "audit_repo": "Comprendre / auditer le repo",
        "feature_work": "Modifier / ajouter une feature",
        "debug_fix": "Corriger un bug",
        "review_harden": "Refactor / sécuriser",
        "pilot_discovery": "Je ne sais pas",
    }
    return titles.get(str(intent or ""), "Architect / cadrage")


def pilot_waiting_prompt_text(*, origin: str, intent: str, reasoning: str, repo: str | None = None) -> str:
    lines = [
        "<b>🧭 Pilote prêt</b>",
        "",
        f"Source : <b>{'Start from scratch' if origin == 'from_scratch' else 'Projet GitHub existant'}</b>",
        f"Route : <b>{_html.escape(pilot_intent_title(intent))}</b>",
        f"Plan : <b>{_html.escape(reasoning)}</b>",
    ]
    if repo:
        lines.append(f"Repo : <code>{_html.escape(repo)}</code>")
    lines.extend([
        "",
        "Écris maintenant ce que tu veux que je fasse.",
        "",
        "Pas besoin de <code>/task</code> : ton prochain message devient la tâche Pilote.",
    ])
    return "\n".join(lines)


def repo_selected_text(repo: str, mode: str, thread_id: str | None = None) -> str:
    mode = normalize_cockpit_mode(mode)
    lines = [
        "<b>✅ Repo sélectionné</b>",
        "",
        f"Repo : <code>{_html.escape(repo)}</code>",
        f"Mode : <b>{_html.escape(mode_title(mode))}</b>",
    ]
    if thread_id:
        lines.append(f"Conversation : <code>{_html.escape(str(thread_id))}</code>")
    lines.extend([
        "",
        "Prochaine étape : envoie ta tâche directement dans ce chat.",
    ])
    return "\n".join(lines)


def new_chat_text(mode: str, selected_repo: str | None = None) -> str:
    mode = normalize_cockpit_mode(mode)
    repo_line = (
        f"Repo actuel : <code>{_html.escape(selected_repo)}</code>"
        if selected_repo
        else "Repo actuel : <i>aucun repo sélectionné</i>"
    )
    return (
        "<b>🧭 Nouveau chat Hermes</b>\n\n"
        f"Mode : <b>{_html.escape(mode_title(mode))}</b>\n"
        f"Effet : {_html.escape(mode_note(mode))}.\n"
        f"{repo_line}\n\n"
        "Choisis si ce clavardage part d'un repo GitHub existant ou d'un nouveau projet."
    )


def project_created_text(data: dict) -> str:
    return (
        "<b>✅ Nouveau projet créé</b>\n\n"
        f"Projet : <code>{_html.escape(data.get('title',''))}</code>\n"
        f"Repo : <code>{_html.escape(data.get('repo') or '')}</code>\n"
        f"Mode : <b>{_html.escape(mode_title(data.get('mode','ask_review')))}</b>\n"
        f"Thread : <code>{_html.escape(data.get('thread_id',''))}</code>\n\n"
        "Tu peux maintenant écrire la tâche à réaliser dans ce chat."
    )


def tasks_list_text(tasks: list[dict]) -> str:
    if not tasks:
        return "<b>📋 Tâches</b>\n\nAucune tâche."
    lines = ["<b>📋 Tâches Repo Cockpit</b>", ""]
    for task in tasks:
        lines.append(
            f"<code>{_html.escape(task.get('id',''))}</code> · "
            f"<b>{_html.escape(task.get('status',''))}</b> · "
            f"{_html.escape(task.get('repo',''))}"
        )
    lines.append("\nDétail : <code>/task ID</code>")
    return "\n".join(lines)


def audit_task_text(active: dict, args: str = "") -> str:
    repo = str(active.get("repo") or "repo actif").strip() or "repo actif"
    thread_id = str(active.get("thread_id") or "").strip()
    user_focus = (args or "").strip()
    focus_line = f"\nFocus utilisateur : {user_focus}" if user_focus else ""
    return (
        f"Audit borné Repo Cockpit pour {repo}.\n"
        "Objectif : inspecter l'état courant sans modifier le repo, identifier "
        "les risques principaux, les tests/smokes utiles, et la prochaine "
        "action sûre.\n"
        f"Thread actif : {thread_id or 'inconnu'}."
        f"{focus_line}\n"
        "Contraintes : pas de déploiement, pas de restart service, pas de "
        "mutation destructive. Produire un résumé court avec statut, phase, "
        "preuves consultées et suite recommandée."
    )


def format_audit_started(*, job_id: str, task: dict, active: dict) -> str:
    task_id = str(task.get("id") or "")
    repo = str(task.get("repo") or active.get("repo") or "")
    phase = str(task.get("current_phase") or task.get("status") or "queued_plan")
    mode = str(task.get("mode") or active.get("thread_mode") or active.get("project_mode") or "ask_review")
    lines = [
        "<b>🔎 Audit Repo Cockpit lancé</b>",
        "",
        f"Job : <code>{_html.escape(job_id)}</code>",
        f"Tâche : <code>{_html.escape(task_id)}</code>",
        f"Repo : <code>{_html.escape(repo)}</code>",
        f"Mode : <b>{_html.escape(mode_title(mode))}</b>",
        f"Phase : <code>{_html.escape(phase)}</code>",
        "",
        "Je lance le worker en arrière-plan en dry-run. Le chat reste disponible.",
        f"Suivi : <code>/status {_html.escape(task_id)}</code> · <code>/runs {_html.escape(task_id)}</code>",
    ]
    return "\n".join(lines)


def format_audit_completed(*, job_id: str, task_id: str, status: str) -> str:
    lines = [
        "<b>🔎 Audit Repo Cockpit terminé</b>",
        "",
        f"Job : <code>{_html.escape(job_id)}</code>",
        f"Tâche : <code>{_html.escape(task_id)}</code>",
        f"Worker : <code>{_html.escape(status)}</code>",
        "",
        f"Suivi : <code>/status {_html.escape(task_id)}</code> · <code>/runs {_html.escape(task_id)}</code>",
    ]
    return "\n".join(lines)


def format_audit_blocked(*, job_id: str, task_id: str, error: str) -> str:
    return (
        "<b>🔎 Audit Repo Cockpit bloqué</b>\n\n"
        f"Job : <code>{_html.escape(job_id)}</code>\n"
        f"Tâche : <code>{_html.escape(task_id)}</code>\n\n"
        "<code>" + _html.escape(str(error))[:1000] + "</code>"
    )
