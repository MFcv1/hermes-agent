"""Repo Cockpit Telegram command mixin.

Mechanical extraction from ``gateway.platforms.telegram.TelegramAdapter`` for
Autonomie V2 Phase 1. Keep behavior-neutral: methods still operate on ``self``
and delegate to the same lower-level adapter helpers.
"""

from __future__ import annotations

import asyncio
import html as _html
import json
import logging
import os
import re
import shlex
import time
from datetime import datetime
from urllib import request as _urlrequest
from typing import Any

from gateway.human_heartbeat import progress_from_autonomy, render_progress_view
from gateway.repo_cockpit_client import cockpit_webapp_url
from gateway.repo_cockpit_formatting import (
    format_autonomy_status,
    format_pending_prs,
    format_pr_summary,
    format_runs_status,
    latest_items,
    pending_pr_label,
    preview_is_blocked,
    status_badge,
    status_is_problem,
)
from gateway.repo_cockpit_keyboards import (
    autonomy_keyboard,
    new_chat_keyboard,
    pending_prs_keyboard,
    pilot_existing_intent_keyboard,
    repo_button_label,
    repo_new_chat_keyboard,
    repo_selected_keyboard,
)
from gateway.repo_cockpit_text import (
    audit_task_text,
    format_audit_blocked,
    format_audit_completed,
    format_audit_started,
    mode_note,
    mode_title,
    new_chat_text,
    normalize_cockpit_mode,
    pilot_intent_title,
    pilot_waiting_prompt_text,
    project_created_text,
    repo_selected_text,
    tasks_list_text,
)
from gateway.observation_reporter import post_runtime_observations

logger = logging.getLogger("gateway.platforms.telegram")
REPO_COCKPIT_MODES = {"ask_review", "pilote", "autopilot"}


def InlineKeyboardButton(*args: Any, **kwargs: Any) -> Any:
    from gateway.platforms import telegram as telegram_mod

    return telegram_mod.InlineKeyboardButton(*args, **kwargs)


def InlineKeyboardMarkup(*args: Any, **kwargs: Any) -> Any:
    from gateway.platforms import telegram as telegram_mod

    return telegram_mod.InlineKeyboardMarkup(*args, **kwargs)


def _web_app_info():
    from gateway.platforms import telegram as telegram_mod

    return telegram_mod.WebAppInfo


class _ParseModeProxy:
    @property
    def HTML(self) -> str:
        from gateway.platforms import telegram as telegram_mod

        return getattr(telegram_mod.ParseMode, "HTML", "HTML")


ParseMode = _ParseModeProxy()
Message = Any


class RepoCockpitTelegramMixin:
    def _cockpit_api_sync(self, method: str, path: str, payload: dict | None = None, timeout: int = 20) -> dict:
        """Call local Repo Cockpit backend without consuming LLM quota."""
        return self._repo_cockpit_client.api_sync(method, path, payload, timeout)

    async def _log_cockpit_message(self, msg: Message | None, *, direction: str, role: str, command: str | None = None, sent: Message | None = None) -> None:
        """Persist Telegram message ids in Repo Cockpit so /clean can delete known recent noise."""
        target = sent or msg
        if not target:
            return
        chat_id = getattr(target, "chat_id", None) or getattr(getattr(target, "chat", None), "id", None)
        message_id = getattr(target, "message_id", None)
        if chat_id is None or message_id is None:
            return
        payload = {"chat_type": getattr(getattr(target, "chat", None), "type", None)}
        body = {"chat_id": str(chat_id), "message_id": int(message_id), "direction": direction, "message_role": role, "command": command, "payload": payload}
        try:
            await asyncio.to_thread(self._cockpit_api_sync, "POST", "/api/telegram/messages/log", body, 10)
        except Exception:
            pass

    async def _send_cockpit_text(self, msg: Message, text: str, *, parse_mode: str | None = "HTML", role: str = "bot_reply") -> None:
        try:
            sent = await msg.reply_text(text[:3900], parse_mode=parse_mode, disable_web_page_preview=True)
        except Exception:
            sent = await msg.reply_text(re.sub(r"<[^>]+>", "", text)[:3900])
        await self._log_cockpit_message(msg, direction="outgoing", role=role, sent=sent)

    async def _send_cockpit_panel(self, msg: Message, text: str, keyboard: InlineKeyboardMarkup, *, role: str = "preview") -> Message | None:
        try:
            sent = await msg.reply_text(
                text[:3900],
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        except Exception:
            sent = await msg.reply_text(re.sub(r"<[^>]+>", "", text)[:3900], reply_markup=keyboard)
        await self._log_cockpit_message(msg, direction="outgoing", role=role, sent=sent)
        return sent

    async def _edit_cockpit_panel(
        self,
        chat_id: str | int,
        message_id: str | int,
        text: str,
        keyboard: InlineKeyboardMarkup,
    ) -> bool:
        if not self._bot:
            return False
        try:
            await self._bot.edit_message_text(
                chat_id=int(chat_id),
                message_id=int(message_id),
                text=text[:3900],
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
            return True
        except Exception as exc:
            if "not modified" in str(exc).lower():
                return True
            try:
                await self._bot.edit_message_text(
                    chat_id=int(chat_id),
                    message_id=int(message_id),
                    text=re.sub(r"<[^>]+>", "", text)[:3900],
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
                return True
            except Exception as retry_exc:
                logger.warning("[%s] Cockpit panel edit failed: %s", self.name, retry_exc)
                return False

    def _mode_title(self, mode: str) -> str:
        return mode_title(mode)

    def _mode_note(self, mode: str) -> str:
        return mode_note(mode)

    async def _get_cockpit_state(self, telegram_user_id: str) -> dict:
        return await asyncio.to_thread(self._cockpit_api_sync, "GET", f"/api/internal/state/{telegram_user_id}", None, 10)

    async def _set_cockpit_mode(self, telegram_user_id: str, mode: str, chat_id: str | None = None) -> dict:
        mode = normalize_cockpit_mode(mode)
        return await asyncio.to_thread(
            self._cockpit_api_sync,
            "POST",
            "/api/internal/state",
            {"telegram_user_id": str(telegram_user_id), "mode": mode, "chat_id": str(chat_id or "")},
            10,
        )

    def _repo_cockpit_url(self, path: str = "/", **params: str) -> str:
        return cockpit_webapp_url(path, **params)

    def _new_chat_keyboard(self, mode: str) -> InlineKeyboardMarkup:
        return new_chat_keyboard(mode, button=InlineKeyboardButton, markup=InlineKeyboardMarkup)


    def _pilot_default_reasoning(self, user_id: str, origin: str | None = None, intent: str | None = None) -> str:
        prefs = self._get_cockpit_llm_prefs(user_id)
        selected = str(prefs.get("reasoning_effort") or "medium").lower()
        if selected in {"high", "xhigh"}:
            return selected
        if origin == "from_scratch" or intent in {"deploy", "review_harden"}:
            return "high"
        return selected if selected in {"low", "medium"} else "medium"

    def _pilot_intent_title(self, intent: str | None) -> str:
        return pilot_intent_title(intent)

    def _pilot_existing_intent_keyboard(self, mode: str = "pilote") -> InlineKeyboardMarkup:
        return pilot_existing_intent_keyboard(mode, button=InlineKeyboardButton, markup=InlineKeyboardMarkup)

    def _pilot_waiting_prompt_text(self, *, origin: str, intent: str, repo: str | None = None, user_id: str = "") -> str:
        reasoning = self._pilot_default_reasoning(user_id, origin, intent) if user_id else "high"
        return pilot_waiting_prompt_text(origin=origin, intent=intent, reasoning=reasoning, repo=repo)

    def _pilot_slug_from_text(self, text: str) -> str:
        raw = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").lower()).strip("-")
        raw = re.sub(r"-+", "-", raw)[:42].strip("-")
        return raw or f"pilot-project-{int(time.time())}"

    async def _pilot_create_scratch_and_task(self, msg: Message, task_text: str, state: dict) -> bool:
        user_id = str(getattr(getattr(msg, "from_user", None), "id", "") or getattr(msg, "chat_id", ""))
        title = self._pilot_slug_from_text(task_text)
        prefs = self._get_cockpit_llm_prefs(user_id)
        payload = {
            "telegram_user_id": user_id,
            "chat_id": str(getattr(msg, "chat_id", "")),
            "title": title,
            "mode": "pilote",
            "visibility": "private",
            "description": task_text[:600],
            "create_repo": True,
            "chat_model": prefs.get("model"),
            "chat_provider": prefs.get("provider"),
            "reasoning_effort": self._pilot_default_reasoning(user_id, "from_scratch", state.get("intent") or "architect"),
        }
        data = await asyncio.to_thread(self._cockpit_api_sync, "POST", "/api/internal/projects", payload, 90)
        if not data.get("ok"):
            await self._send_cockpit_text(
                msg,
                "<b>❌ Création projet impossible</b>\n\n<code>" + _html.escape(str(data.get("description") or data))[:1200] + "</code>",
                role="preview",
            )
            return True
        await self._send_cockpit_text(
            msg,
            "<b>✅ Projet Pilote créé</b>\n\n"
            f"Repo : <code>{_html.escape(data.get('repo') or '')}</code>\n"
            "Je crée maintenant la tâche autonome avec ton prompt.",
            role="sticky",
        )
        thread_id = str(data.get("thread_id") or "")
        if thread_id:
            self._cockpit_register_thread_llm_prefs(
                chat_id=str(getattr(msg, "chat_id", "")),
                thread_id=thread_id,
                telegram_user_id=user_id,
            )
        await self._create_task_from_thread_command(msg, task_text)
        return True

    async def _maybe_handle_pilot_intake_text(self, msg: Message, text: str) -> bool:
        clean = (text or "").strip()
        if not clean or clean.startswith("/"):
            return False
        user_id = str(getattr(getattr(msg, "from_user", None), "id", "") or getattr(msg, "chat_id", ""))
        state = self._pilot_intake_states.get(user_id) or {}
        if state.get("awaiting") == "prompt":
            self._pilot_intake_states.pop(user_id, None)
            origin = str(state.get("origin") or "existing_repo")
            if origin == "from_scratch":
                return await self._pilot_create_scratch_and_task(msg, clean, state)
            await self._create_task_from_thread_command(msg, clean)
            return True

        # If a Pilote task is waiting for context, a natural message is the answer.
        pending = await asyncio.to_thread(
            self._cockpit_api_sync,
            "GET",
            f"/api/internal/tasks/pilot-pending/{user_id}",
            None,
            15,
        )
        if pending.get("ok") and pending.get("pending") and (pending.get("task") or {}).get("id"):
            pilot_task_id = str((pending.get("task") or {}).get("id"))
            result = await asyncio.to_thread(
                self._cockpit_api_sync,
                "POST",
                f"/api/internal/tasks/{pilot_task_id}/pilot-answer",
                {"telegram_user_id": user_id, "answer": clean},
                20,
            )
            if result.get("ok"):
                await self._send_cockpit_text(
                    msg,
                    "<b>🧭 Réponse Pilote reçue</b>\n\n"
                    f"Tâche : <code>{_html.escape(pilot_task_id)}</code>\n"
                    f"Statut : <code>{_html.escape(str(result.get('status') or 'queued_plan'))}</code>\n\n"
                    "Je relance le worker avec ce contexte.",
                    role="sticky",
                )
                asyncio.create_task(self._run_autopilot_worker_after_task_create(msg, pilot_task_id))
                return True
        return False

    def _repo_button_label(self, repo: dict) -> str:
        return repo_button_label(repo)

    def _repo_new_chat_keyboard(self, user_id: str, mode: str, repos: list[dict], cockpit_url: str) -> InlineKeyboardMarkup:
        return repo_new_chat_keyboard(
            user_id,
            mode,
            repos,
            cockpit_url,
            button=InlineKeyboardButton,
            markup=InlineKeyboardMarkup,
            web_app_info=_web_app_info(),
        )

    def _repo_selected_text(self, repo: str, mode: str, thread_id: str | None = None) -> str:
        return repo_selected_text(repo, mode, thread_id)

    def _repo_selected_keyboard(self, mode: str) -> InlineKeyboardMarkup:
        return repo_selected_keyboard(mode, button=InlineKeyboardButton, markup=InlineKeyboardMarkup)

    def _new_chat_text(self, mode: str, selected_repo: str | None = None) -> str:
        return new_chat_text(mode, selected_repo)

    async def _send_new_command(self, msg: Message, args: str = "") -> None:
        args = (args or "").strip()
        if args.lower().startswith("scratch "):
            return await self._create_scratch_project_from_command(msg, args[len("scratch "):])
        user_id = str(getattr(getattr(msg, "from_user", None), "id", "") or getattr(msg, "chat_id", ""))
        state = await self._get_cockpit_state(user_id)
        mode = normalize_cockpit_mode(state.get("mode"))
        if args.lower() in REPO_COCKPIT_MODES:
            mode = normalize_cockpit_mode(args)
            await asyncio.to_thread(
                self._cockpit_api_sync,
                "POST",
                "/api/internal/state",
                {
                    "telegram_user_id": user_id,
                    "mode": mode,
                    "chat_id": str(getattr(msg, "chat_id", "")),
                },
                10,
            )
            state["mode"] = mode
        await self._send_cockpit_panel(
            msg,
            self._new_chat_text_with_prefs(mode, user_id, state.get("selected_repo")),
            self._new_chat_keyboard_with_prefs(mode, user_id),
            role="preview",
        )

    async def _create_scratch_project_from_command(self, msg: Message, raw: str) -> None:
        # Format: /new scratch repo-name | description | private|public | ask_review|pilote|autopilot
        parts = [p.strip() for p in raw.split("|")]
        title = parts[0] if parts else ""
        if not title:
            return await self._send_cockpit_text(
                msg,
                "<b>Start from scratch</b>\n\nUsage : <code>/new scratch nom-projet | description | private | pilote</code>",
                role="preview",
            )
        description = parts[1] if len(parts) > 1 and parts[1] else title
        visibility = parts[2].lower() if len(parts) > 2 and parts[2].lower() in {"private", "public"} else "private"
        mode = normalize_cockpit_mode(parts[3] if len(parts) > 3 else None)
        user_id = str(getattr(getattr(msg, "from_user", None), "id", "") or getattr(msg, "chat_id", ""))
        prefs = self._get_cockpit_llm_prefs(user_id)
        payload = {
            "telegram_user_id": user_id,
            "chat_id": str(getattr(msg, "chat_id", "")),
            "title": title,
            "mode": mode,
            "visibility": visibility,
            "description": description,
            "create_repo": True,
            "chat_model": prefs.get("model"),
            "chat_provider": prefs.get("provider"),
            "reasoning_effort": prefs.get("reasoning_effort"),
        }
        data = await asyncio.to_thread(self._cockpit_api_sync, "POST", "/api/internal/projects", payload, 90)
        if not data.get("ok"):
            return await self._send_cockpit_text(
                msg,
                "<b>❌ Création projet impossible</b>\n\n<code>" + _html.escape(str(data.get("description") or data))[:1200] + "</code>",
                role="preview",
            )
        await self._send_cockpit_text(msg, project_created_text(data), role="sticky")
        thread_id = str(data.get("thread_id") or "")
        if thread_id:
            self._cockpit_register_thread_llm_prefs(
                chat_id=str(getattr(msg, "chat_id", "")),
                thread_id=thread_id,
                telegram_user_id=user_id,
            )

    async def _send_tasks_command(self, msg: Message, args: str = "") -> None:
        data = await asyncio.to_thread(self._cockpit_api_sync, "GET", "/api/tasks?limit=12", None, 20)
        tasks = data.get("tasks") or []
        await self._send_cockpit_text(msg, tasks_list_text(tasks))

    def _pending_pr_label(self, item: dict) -> str:
        return pending_pr_label(item)

    def _format_pending_prs(self, data: dict) -> str:
        return format_pending_prs(data)

    def _pending_prs_keyboard(self, data: dict) -> InlineKeyboardMarkup:
        return pending_prs_keyboard(data, button=InlineKeyboardButton, markup=InlineKeyboardMarkup)

    async def _send_pending_prs_command(self, msg: Message, args: str = "") -> None:
        limit = 10
        raw = (args or "").strip()
        if raw:
            try:
                limit = max(1, min(int(raw.split()[0]), 30))
            except Exception:
                limit = 10
        data = await asyncio.to_thread(self._cockpit_api_sync, "GET", f"/api/internal/prs/pending?limit={limit}", None, 20)
        if not data or not data.get("ok"):
            return await self._send_cockpit_text(
                msg,
                "<b>🔀 PRs en attente</b>\n\nImpossible : <code>"
                + _html.escape(str((data or {}).get("description") or data))[:1000]
                + "</code>",
                role="preview",
            )
        await self._send_cockpit_panel(msg, self._format_pending_prs(data), self._pending_prs_keyboard(data), role="preview")

    def _format_pr_summary(self, data: dict) -> str:
        return format_pr_summary(data)

    def _audit_task_text(self, active: dict, args: str = "") -> str:
        return audit_task_text(active, args)

    def _format_audit_started(self, *, job_id: str, task: dict, active: dict) -> str:
        return format_audit_started(job_id=job_id, task=task, active=active)

    async def _send_audit_command(self, msg: Message, args: str = "") -> None:
        user_id, data, active = await self._get_active_cockpit_thread(msg)
        if not data or not data.get("ok"):
            return await self._send_cockpit_text(
                msg,
                "<b>🔎 Audit Repo Cockpit</b>\n\nImpossible de lire le thread actif : <code>"
                + _html.escape(str((data or {}).get("description") or data))[:800]
                + "</code>",
                role="preview",
            )
        if not active:
            return await self._send_cockpit_text(
                msg,
                "<b>🔎 Audit Repo Cockpit</b>\n\nAucun thread actif. Lance <code>/conv</code> puis choisis un repo.",
                role="preview",
            )

        payload = {
            "telegram_user_id": user_id,
            "chat_id": str(getattr(msg, "chat_id", "")),
            "task": self._audit_task_text(active, args),
            "source": "telegram_audit_command",
        }
        task = await asyncio.to_thread(self._cockpit_api_sync, "POST", "/api/internal/tasks/from-thread", payload, 30)
        if not task or not task.get("ok"):
            return await self._send_cockpit_text(
                msg,
                "<b>🔎 Audit non lancé</b>\n\n<code>"
                + _html.escape(str((task or {}).get("description") or task))[:900]
                + "</code>",
                role="preview",
            )

        job_id = f"audit_{datetime.now().strftime('%H%M%S')}_{os.urandom(2).hex()}"
        await self._send_cockpit_text(
            msg,
            self._format_audit_started(job_id=job_id, task=task, active=active),
            role="progress",
        )

        task_id = str(task.get("id") or "")
        worker_task = asyncio.create_task(self._run_cockpit_audit_background(msg, task_id, job_id))
        self._cockpit_background_tasks.add(worker_task)
        worker_task.add_done_callback(self._cockpit_background_tasks.discard)

    async def _run_cockpit_audit_background(self, msg: Message, task_id: str, job_id: str) -> None:
        try:
            worker = await asyncio.to_thread(
                self._cockpit_api_sync,
                "POST",
                "/api/worker/run-once",
                {"status": "queued_plan", "execute": False},
                1800,
            )
            worker = worker or {}
            worker_result = worker.get("result") if isinstance(worker.get("result"), dict) else {}
            status = str(worker.get("status") or worker_result.get("status") or "done")
            await self._send_cockpit_text(
                msg,
                format_audit_completed(job_id=job_id, task_id=task_id, status=status),
                role="progress",
            )
        except Exception as exc:
            await self._send_cockpit_text(
                msg,
                format_audit_blocked(job_id=job_id, task_id=task_id, error=str(exc)),
                role="progress",
            )

    async def _resolve_status_task_id(self, msg: Message, args: str = "") -> tuple[str | None, str | None]:
        raw = (args or "").strip()
        if raw:
            token = raw.split()[0]
            if token.startswith("op_"):
                return token, None
            return None, "Usage : <code>/status op_xxx</code> ou <code>/runs op_xxx</code>."

        user_id, data, active = await self._get_active_cockpit_thread(msg)
        if not data or not data.get("ok"):
            return None, "Impossible de lire le thread actif : <code>" + _html.escape(str((data or {}).get("description") or data))[:800] + "</code>"
        if not active:
            return None, "Aucune conversation active. Lance <code>/conv</code> ou passe un id : <code>/status op_xxx</code>."

        threads = await self._fetch_threads_for_user(user_id, "all")
        active_thread_id = active.get("thread_id")
        for thread in threads.get("threads") or []:
            if thread.get("thread_id") == active_thread_id and thread.get("last_task_id"):
                return str(thread.get("last_task_id")), None
        return None, "La conversation active n'a pas encore de task. Crée une tâche avec <code>/task ...</code> ou utilise <code>/conv</code>."

    def _status_badge(self, status: str | None) -> str:
        return status_badge(status)

    def _latest_items(self, data: dict, key: str, limit: int = 3) -> list[dict]:
        return latest_items(data, key, limit)

    def _format_autonomy_status(self, data: dict) -> str:
        return format_autonomy_status(data)

    def _format_runs_status(self, data: dict) -> str:
        return format_runs_status(data)

    def _format_autopilot_live_card(self, data: dict, *, elapsed_seconds: int) -> str:
        progress = progress_from_autonomy(data, elapsed_seconds=elapsed_seconds)
        rendered = render_progress_view(progress)
        lines = ["<b>🚀 Autopilot Hermes</b>", ""]
        for line in rendered.splitlines():
            if line.startswith("⏳ "):
                lines.append(f"<b>{_html.escape(line)}</b>")
            elif line.startswith("Etat :"):
                lines.append(_html.escape(line))
            elif " : " in line:
                key, value = line.split(" : ", 1)
                lines.append(f"<b>{_html.escape(key)} :</b> {_html.escape(value)}")
            else:
                lines.append(_html.escape(line))
        return "\n".join(lines)

    def _autopilot_smoke_ok(self, data: dict, task: dict | None = None) -> bool:
        task = task or (data.get("task") or {})
        status = str(task.get("status") or "")
        if status in {"deployed_preview", "completed", "done", "ready"}:
            return True
        smokes = data.get("smoke_tests") or []
        if isinstance(smokes, list) and smokes:
            latest = smokes[0]
            return str(latest.get("status") or "").lower() in {"passed", "ready", "ok", "success"}
        provider_checks = data.get("provider_checks") or []
        if isinstance(provider_checks, list):
            deploy_checks = [
                item
                for item in provider_checks
                if str(item.get("provider") or "").lower() in {"vercel", "hosting"}
                or "deploy" in str(item.get("check_name") or "").lower()
            ]
            if deploy_checks:
                return str(deploy_checks[0].get("status") or "").lower() in {"passed", "ready", "ok", "success"}
        return False

    def _format_autopilot_final(self, task_id: str, worker: dict, task: dict, autonomy: dict) -> str:
        result = worker.get("result") if isinstance(worker, dict) else {}
        result = result if isinstance(result, dict) else {}
        status = str(result.get("status") or task.get("status") or (autonomy.get("task") or {}).get("status") or "unknown")
        preview = str(task.get("preview_url") or task.get("deployment_url") or (autonomy.get("task") or {}).get("preview_url") or (autonomy.get("task") or {}).get("deployment_url") or "")
        smoke_ok = bool(preview) and self._autopilot_smoke_ok(autonomy, task)
        lines = ["<b>✅ Autopilot terminé</b>" if smoke_ok else "<b>⚠️ Autopilot à reprendre</b>", ""]
        lines.append(f"Tâche : <code>{_html.escape(task_id or '?')}</code>")
        lines.append(f"Statut : <code>{_html.escape(status)}</code>")
        repo = task.get("repo") or (autonomy.get("task") or {}).get("repo")
        if repo:
            lines.append(f"Repo : <code>{_html.escape(str(repo))}</code>")
        if smoke_ok:
            lines.extend(["", "Preview validée :", _html.escape(preview)])
            lines.append("\nJ'ai gardé les boutons de suivi sur la carte au-dessus pour consulter les runs et le status.")
            return "\n".join(lines)

        lines.extend([
            "",
            "Je ne donne pas de lien final validé tant que deploy/smoke n'est pas OK.",
            "Consulte <code>/runs " + _html.escape(task_id or "") + "</code> pour voir le point de blocage exact.",
        ])
        latest_error = autonomy.get("latest_error")
        if not latest_error and isinstance(autonomy.get("error_events"), list) and autonomy.get("error_events"):
            latest_error = autonomy.get("error_events")[0]
        if isinstance(latest_error, dict):
            category = latest_error.get("category")
            runbook = latest_error.get("runbook")
            if category or runbook:
                lines.extend([
                    "",
                    "<b>Diagnostic</b>",
                    f"Catégorie : <code>{_html.escape(str(category or ''))}</code>",
                    f"Runbook : <code>{_html.escape(str(runbook or ''))}</code>",
                ])
        return "\n".join(lines)

    def _preview_is_blocked(self, status: str) -> bool:
        return preview_is_blocked(status)

    def _status_is_problem(self, status: str) -> bool:
        return status_is_problem(status)

    def _autonomy_keyboard(self, data: dict, view: str = "status") -> InlineKeyboardMarkup:
        return autonomy_keyboard(data, view, button=InlineKeyboardButton, markup=InlineKeyboardMarkup)

    async def _send_status_command(self, msg: Message, args: str = "") -> None:
        task_id, error = await self._resolve_status_task_id(msg, args)
        if error:
            return await self._send_cockpit_text(msg, "<b>🛰️ Status autonomie</b>\n\n" + error, role="preview")
        data = await asyncio.to_thread(self._cockpit_api_sync, "GET", f"/api/internal/tasks/{task_id}/autonomy", None, 20)
        if not data or not data.get("ok"):
            return await self._send_cockpit_text(msg, "<b>🛰️ Status autonomie</b>\n\nImpossible : <code>" + _html.escape(str((data or {}).get("description") or data))[:1000] + "</code>", role="preview")
        await self._send_cockpit_panel(msg, self._format_autonomy_status(data), self._autonomy_keyboard(data, "status"), role="progress")

    async def _send_runs_command(self, msg: Message, args: str = "") -> None:
        task_id, error = await self._resolve_status_task_id(msg, args)
        if error:
            return await self._send_cockpit_text(msg, "<b>🧪 Runs / gates</b>\n\n" + error, role="preview")
        data = await asyncio.to_thread(self._cockpit_api_sync, "GET", f"/api/internal/tasks/{task_id}/autonomy", None, 20)
        if not data or not data.get("ok"):
            return await self._send_cockpit_text(msg, "<b>🧪 Runs / gates</b>\n\nImpossible : <code>" + _html.escape(str((data or {}).get("description") or data))[:1000] + "</code>", role="preview")
        await self._send_cockpit_panel(msg, self._format_runs_status(data), self._autonomy_keyboard(data, "runs"), role="progress")

    def _dev_menu_text(self, section: str = "home") -> str:
        if section == "github":
            return (
                "<b>🧑‍💻 GitHub sans friction</b>\n\n"
                "<code>/new</code> : choisir un repo GitHub ou créer un nouveau projet.\n"
                "Ensuite tu écris normalement : “corrige ce bug”, “ajoute cette page”, "
                "“crée une branche et ouvre une PR”.\n\n"
                "<code>/task ...</code> : lance un travail suivi sur le repo sélectionné.\n"
                "<code>/runs</code> : montre où en est ce travail : tests, erreurs, blocage, deploy.\n"
                "<code>/audit</code> : inspecte le repo ou la PR sans modifier le code.\n"
                "<code>/prs</code> : liste les PR à relire ou merger.\n\n"
                "Exemple : <code>/task crée une branche, corrige le bug de login, lance les tests, ouvre une PR et explique-moi le diff</code>"
            )
        if section == "ops":
            return (
                "<b>🛠️ Déploiement / maintenance</b>\n\n"
                "<code>/vps</code> : état rapide du VPS.\n"
                "<code>/updatecheck</code> : peut-on mettre Hermes à jour ?\n"
                "<code>/watch vps</code> : alerte seulement si le VPS se dégrade.\n"
                "<code>/watch releases owner/repo</code> : alerte nouvelle release.\n"
                "<code>/watch list</code> : watchers actifs.\n"
                "<code>/jobs</code> : toutes les tâches planifiées et leur livraison.\n\n"
                "Règle simple : check d'abord, déploie ensuite. Pas de mutation automatique sans approbation."
            )
        if section == "learn":
            return (
                "<b>📚 Mode apprentissage</b>\n\n"
                "Tu peux parler normalement : “explique-moi ce fichier”, “pourquoi ce test fail”, "
                "“fais-moi un plan avant de modifier”, “résume-moi la PR”.\n\n"
                "Commandes utiles : <code>/dev</code>, <code>/chat</code>, <code>/runs</code>, "
                "<code>/audit</code>, <code>/clean</code>.\n\n"
                "Objectif : comprendre ce que fait Hermes, pas subir une boîte noire."
            )
        return (
            "<b>🧑‍💻 Dev cockpit simple</b>\n\n"
            "<b>Créer / reprendre</b>\n"
            "• <code>/new</code> : choisir un repo ou créer un projet.\n"
            "• <code>/conv</code> : reprendre une conversation projet.\n\n"
            "<b>Coder avec GitHub</b>\n"
            "• <code>/new</code> sélectionne le repo GitHub.\n"
            "• Message normal : petite question ou petite modif.\n"
            "• <code>/task ...</code> : gros travail suivi, branche, tests, PR.\n"
            "• <code>/runs</code> : statut du travail en cours.\n"
            "• <code>/audit</code> : analyse repo/PR sans toucher au code.\n\n"
            "<b>Ops utiles</b>\n"
            "• <code>/vps</code>, <code>/updatecheck</code>, <code>/watch</code>, <code>/jobs</code>.\n\n"
            "Si tu es perdu : lance <code>/dev</code>, puis choisis un bouton."
        )

    def _dev_menu_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Nouveau projet", callback_data="rcn:mode:ask_review"),
                InlineKeyboardButton("Conversations", callback_data="rct:list:all"),
            ],
            [
                InlineKeyboardButton("GitHub flow", callback_data="dev:github"),
                InlineKeyboardButton("Ops / deploy", callback_data="dev:ops"),
            ],
            [
                InlineKeyboardButton("Apprendre", callback_data="dev:learn"),
                InlineKeyboardButton("Accueil", callback_data="dev:home"),
            ],
        ])

    async def _send_dev_command(self, msg: Message, args: str = "") -> None:
        section = (args or "home").strip().lower()
        if section not in {"home", "github", "ops", "learn"}:
            section = "home"
        await self._send_cockpit_panel(
            msg,
            self._dev_menu_text(section),
            self._dev_menu_keyboard(),
            role="preview",
        )

    async def _handle_dev_callback(self, query, data: str) -> None:
        section = data.split(":", 1)[1] if ":" in data else "home"
        if section not in {"home", "github", "ops", "learn"}:
            section = "home"
        await query.answer(text="Menu dev")
        text = self._dev_menu_text(section)
        keyboard = self._dev_menu_keyboard()
        try:
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        except Exception as exc:
            logger.warning("[%s] /dev callback edit failed; sending fallback: %s", self.name, exc)
            query_message = getattr(query, "message", None)
            if query_message is not None:
                try:
                    await query_message.reply_text(
                        text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=keyboard,
                        **self._link_preview_kwargs(),
                    )
                except Exception:
                    logger.exception("[%s] /dev callback fallback send failed", self.name)

    def _telegram_origin(self, msg: Message) -> dict:
        thread_id = getattr(msg, "message_thread_id", None)
        return {
            "platform": "telegram",
            "chat_id": str(getattr(msg, "chat_id", "")),
            "chat_name": str(getattr(getattr(msg, "chat", None), "title", "") or ""),
            "thread_id": str(thread_id) if thread_id is not None else None,
        }

    def _watch_job_kind(self, job: dict) -> str | None:
        script = str(job.get("script") or "")
        if script == "github_release_watch.py":
            return "releases"
        if script == "vps_healthcheck.py":
            return "vps"
        return None

    def _watch_repo_from_job(self, job: dict) -> str:
        args = [str(item) for item in (job.get("script_args") or [])]
        for index, item in enumerate(args):
            if item == "--repo" and index + 1 < len(args):
                return args[index + 1]
        return ""

    def _format_watch_jobs(self, jobs: list[dict]) -> str:
        lines = ["<b>👀 Watchers</b>", ""]
        watch_jobs = [job for job in jobs if self._watch_job_kind(job)]
        if not watch_jobs:
            lines.extend([
                "Aucun watcher actif.",
                "",
                "Créer : <code>/watch releases owner/repo</code>",
                "VPS : <code>/watch vps</code>",
            ])
            return "\n".join(lines)

        for job in watch_jobs:
            kind = self._watch_job_kind(job) or "watch"
            repo = self._watch_repo_from_job(job)
            label = repo if repo else kind
            lines.append(
                f"• <code>{_html.escape(str(job.get('id') or ''))}</code> · "
                f"<b>{_html.escape(kind)}</b> · {_html.escape(label)} · "
                f"<code>{_html.escape(str(job.get('schedule_display') or '?'))}</code>"
            )
        lines.extend([
            "",
            "Retirer : <code>/watch remove job_id</code>",
        ])
        return "\n".join(lines)

    def _job_delivery_label(self, job: dict) -> str:
        deliver = str(job.get("deliver") or "local")
        origin = job.get("origin") if isinstance(job.get("origin"), dict) else {}
        if deliver == "origin" and origin:
            platform = str(origin.get("platform") or "origin")
            chat_id = str(origin.get("chat_id") or "")
            if platform == "telegram":
                return "Telegram" + (f" {chat_id}" if chat_id else "")
            return platform
        return deliver

    def _job_status_label(self, job: dict) -> str:
        if not job.get("enabled", True):
            return "paused"
        return str(job.get("last_status") or job.get("state") or "scheduled")

    def _format_scheduled_jobs(self, jobs: list[dict], *, limit: int = 12) -> str:
        lines = ["<b>🗓️ Jobs planifiés</b>", ""]
        if not jobs:
            lines.extend([
                "Aucun job planifié.",
                "",
                "Watchers simples : <code>/watch releases owner/repo</code> ou <code>/watch vps</code>",
            ])
            return "\n".join(lines)

        for job in jobs[:limit]:
            name = str(job.get("name") or "job")
            job_id = str(job.get("id") or "")
            schedule = str(job.get("schedule_display") or "?")
            next_run = str(job.get("next_run_at") or "?")
            status = self._job_status_label(job)
            delivery = self._job_delivery_label(job)
            no_agent = "no-agent" if job.get("no_agent") else "agent"
            lines.append(
                f"• <code>{_html.escape(job_id)}</code> · <b>{_html.escape(name)[:80]}</b>\n"
                f"  {_html.escape(status)} · {_html.escape(no_agent)} · <code>{_html.escape(schedule)}</code>\n"
                f"  next: <code>{_html.escape(next_run)[:32]}</code> · vers: {_html.escape(delivery)}"
            )

        if len(jobs) > limit:
            lines.append(f"\n... {len(jobs) - limit} autre(s) job(s).")
        lines.extend([
            "",
            "Gestion : <code>/jobs pause id</code> · <code>/jobs resume id</code> · <code>/jobs remove id</code>",
            "Watchers seuls : <code>/watch list</code>",
        ])
        return "\n".join(lines)

    def _jobs_keyboard(self, jobs: list[dict], *, limit: int = 8) -> InlineKeyboardMarkup | None:
        if not jobs:
            return None
        rows: list[list[InlineKeyboardButton]] = []
        for job in jobs[:limit]:
            job_id = str(job.get("id") or "")
            if not job_id:
                continue
            label = str(job.get("name") or job_id)[:22]
            if job.get("enabled", True):
                rows.append([
                    InlineKeyboardButton(f"Pause {label}", callback_data=f"job:pause:{job_id}"),
                    InlineKeyboardButton("Supprimer", callback_data=f"job:remove:{job_id}"),
                ])
            else:
                rows.append([
                    InlineKeyboardButton(f"Reprendre {label}", callback_data=f"job:resume:{job_id}"),
                    InlineKeyboardButton("Supprimer", callback_data=f"job:remove:{job_id}"),
                ])
        rows.append([InlineKeyboardButton("Rafraîchir", callback_data="job:list")])
        return InlineKeyboardMarkup(rows)

    async def _send_jobs_panel(self, msg: Message) -> None:
        from cron.jobs import list_jobs

        jobs = await asyncio.to_thread(list_jobs, True)
        keyboard = self._jobs_keyboard(jobs)
        text = self._format_scheduled_jobs(jobs)
        if keyboard is None:
            await self._send_cockpit_text(msg, text, role="preview")
        else:
            await self._send_cockpit_panel(msg, text, keyboard, role="preview")

    async def _send_jobs_command(self, msg: Message, args: str = "") -> None:
        try:
            tokens = shlex.split(args or "")
        except ValueError:
            tokens = (args or "").split()
        verb = (tokens[0].lower() if tokens else "list")
        if verb in {"list", "ls", "all", ""}:
            return await self._send_jobs_panel(msg)

        if verb in {"pause", "resume", "remove", "rm", "delete"}:
            target = tokens[1] if len(tokens) > 1 else ""
            if not target:
                return await self._send_cockpit_text(
                    msg,
                    "Usage : <code>/jobs pause id</code>, <code>/jobs resume id</code> ou <code>/jobs remove id</code>",
                    role="preview",
                )
            from cron.jobs import get_job, pause_job, remove_job, resume_job

            before = await asyncio.to_thread(get_job, target)
            if not before:
                return await self._send_cockpit_text(
                    msg,
                    "Job introuvable. Fais <code>/jobs</code> pour voir les IDs.",
                    role="preview",
                )
            if verb == "pause":
                updated = await asyncio.to_thread(pause_job, target, "paused from Telegram /jobs")
                action = "mis en pause"
            elif verb == "resume":
                updated = await asyncio.to_thread(resume_job, target)
                action = "repris"
            else:
                ok = await asyncio.to_thread(remove_job, target)
                updated = before if ok else None
                action = "supprimé" if ok else "non supprimé"
            title = str(before.get("name") or target)
            status = self._job_status_label(updated or before)
            return await self._send_cockpit_text(
                msg,
                f"<b>Job { _html.escape(action) }</b>\n\n"
                f"<code>{_html.escape(target)}</code> · {_html.escape(title)}\n"
                f"Statut : <code>{_html.escape(status)}</code>",
                role="preview",
            )

        return await self._send_cockpit_text(
            msg,
            "Usage : <code>/jobs</code>, <code>/jobs pause id</code>, <code>/jobs resume id</code>, <code>/jobs remove id</code>",
            role="preview",
        )

    async def _handle_jobs_callback(self, query, data: str) -> None:
        parts = data.split(":", 2)
        verb = parts[1] if len(parts) > 1 else "list"
        job_id = parts[2] if len(parts) > 2 else ""
        from cron.jobs import get_job, list_jobs, pause_job, remove_job, resume_job

        if verb == "list":
            await query.answer(text="Jobs")
        elif verb in {"pause", "resume", "remove"}:
            if not job_id:
                await query.answer(text="Job invalide")
                return
            before = await asyncio.to_thread(get_job, job_id)
            if not before:
                await query.answer(text="Job introuvable")
            elif verb == "pause":
                await asyncio.to_thread(pause_job, job_id, "paused from Telegram UI")
                await query.answer(text="Job en pause")
            elif verb == "resume":
                await asyncio.to_thread(resume_job, job_id)
                await query.answer(text="Job repris")
            else:
                await asyncio.to_thread(remove_job, job_id)
                await query.answer(text="Job supprimé")
        else:
            await query.answer(text="Action inconnue")
            return

        jobs = await asyncio.to_thread(list_jobs, True)
        text = self._format_scheduled_jobs(jobs)
        keyboard = self._jobs_keyboard(jobs)
        try:
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        except Exception as exc:
            logger.warning("[%s] /jobs callback edit failed; sending fallback: %s", self.name, exc)
            query_message = getattr(query, "message", None)
            if query_message is not None:
                try:
                    await query_message.reply_text(
                        text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=keyboard,
                        **self._link_preview_kwargs(),
                    )
                except Exception:
                    logger.exception("[%s] /jobs callback fallback send failed", self.name)

    async def _send_watch_command(self, msg: Message, args: str = "") -> None:
        try:
            tokens = shlex.split(args or "")
        except ValueError:
            tokens = (args or "").split()
        verb = (tokens[0].lower() if tokens else "help")

        if verb in {"help", ""}:
            return await self._send_cockpit_text(
                msg,
                "<b>👀 Watchers simples</b>\n\n"
                "Releases : <code>/watch releases owner/repo</code>\n"
                "VPS : <code>/watch vps</code>\n"
                "Liste : <code>/watch list</code>\n"
                "Retirer : <code>/watch remove job_id</code>",
                role="preview",
            )

        if verb in {"list", "ls"}:
            from cron.jobs import list_jobs

            jobs = await asyncio.to_thread(list_jobs, True)
            return await self._send_cockpit_text(msg, self._format_watch_jobs(jobs), role="preview")

        if verb in {"remove", "rm", "delete"}:
            target = tokens[1] if len(tokens) > 1 else ""
            if not target:
                return await self._send_cockpit_text(msg, "Usage : <code>/watch remove job_id</code>", role="preview")
            from cron.jobs import list_jobs, remove_job

            jobs = await asyncio.to_thread(list_jobs, True)
            matches = [
                job for job in jobs
                if str(job.get("id") or "") == target
                or self._watch_repo_from_job(job).lower() == target.lower()
            ]
            matches = [job for job in matches if self._watch_job_kind(job)]
            if not matches:
                return await self._send_cockpit_text(
                    msg,
                    "Watcher introuvable. Fais <code>/watch list</code> pour voir les IDs.",
                    role="preview",
                )
            if len(matches) > 1:
                return await self._send_cockpit_text(
                    msg,
                    "Plusieurs watchers correspondent. Retire par ID :\n"
                    + "\n".join(f"<code>{_html.escape(str(job.get('id')))}</code>" for job in matches),
                    role="preview",
                )
            job = matches[0]
            ok = await asyncio.to_thread(remove_job, str(job.get("id")))
            title = "Watcher retiré" if ok else "Suppression impossible"
            return await self._send_cockpit_text(
                msg,
                f"<b>{_html.escape(title)}</b>\n\n<code>{_html.escape(str(job.get('id') or ''))}</code>",
                role="preview",
            )

        if verb in {"release", "releases", "github"}:
            repo = tokens[1] if len(tokens) > 1 else ""
            if "/" not in repo.strip("/"):
                return await self._send_cockpit_text(
                    msg,
                    "Usage : <code>/watch releases owner/repo</code>",
                    role="preview",
                )
            interval = "6"
            include_prereleases = "false"
            for token in tokens[2:]:
                lower = token.lower()
                if lower in {"1", "3", "6", "12", "24"}:
                    interval = lower
                elif lower in {"prerelease", "prereleases", "--prerelease", "--prereleases"}:
                    include_prereleases = "true"
            from hermes_cli.blueprint_cmd import handle_blueprint_command

            result = await asyncio.to_thread(
                handle_blueprint_command,
                (
                    f"github-release-watch repo={shlex.quote(repo.strip('/'))} "
                    f"interval_hours={interval} include_prereleases={include_prereleases} "
                    "max_items=5 deliver=origin"
                ),
                origin=self._telegram_origin(msg),
                surface="gateway",
            )
            return await self._send_cockpit_text(msg, _html.escape(result.text), role="preview")

        if verb in {"vps", "health", "healthcheck"}:
            interval = tokens[1] if len(tokens) > 1 and tokens[1] in {"1", "3", "6", "12", "24"} else "6"
            from hermes_cli.blueprint_cmd import handle_blueprint_command

            result = await asyncio.to_thread(
                handle_blueprint_command,
                f"vps-healthcheck interval_hours={interval} deliver=origin",
                origin=self._telegram_origin(msg),
                surface="gateway",
            )
            return await self._send_cockpit_text(msg, _html.escape(result.text), role="preview")

        return await self._send_cockpit_text(
            msg,
            "Commande inconnue. Essaie <code>/watch releases owner/repo</code>, <code>/watch vps</code> ou <code>/watch list</code>.",
            role="preview",
        )

    async def _send_vps_command(self, msg: Message) -> None:
        from hermes_cli.vps_status import collect_vps_overview, format_vps_overview_html

        report = await asyncio.to_thread(collect_vps_overview)
        await self._send_cockpit_text(msg, format_vps_overview_html(report), role="preview")

    async def _send_updatecheck_command(self, msg: Message, args: str = "") -> None:
        cached = "--cached" in (args or "")
        timeout = 12 if "--slow" not in (args or "") else 25
        from hermes_cli.updatecheck import collect_updatecheck, format_updatecheck_short
        from hermes_constants import get_hermes_home

        report = await asyncio.to_thread(
            collect_updatecheck,
            hermes_home=get_hermes_home(),
            fresh=not cached,
            fetch_timeout=timeout,
        )
        text = "<pre><code>" + _html.escape(format_updatecheck_short(report)) + "</code></pre>"
        await self._send_cockpit_text(msg, text, role="preview")

    async def _handle_autonomy_callback(self, query, data: str) -> None:
        if data == "rca:prs":
            payload = await asyncio.to_thread(self._cockpit_api_sync, "GET", "/api/internal/prs/pending?limit=10", None, 20)
            if not payload or not payload.get("ok"):
                await query.answer(text="Lecture PR impossible")
                return
            await query.answer(text="Mis à jour")
            try:
                await query.edit_message_text(
                    self._format_pending_prs(payload)[:3900],
                    parse_mode=ParseMode.HTML,
                    reply_markup=self._pending_prs_keyboard(payload),
                    disable_web_page_preview=True,
                )
            except Exception:
                pass
            return

        parts = data.split(":", 2)
        verb = parts[1] if len(parts) > 1 else "status"
        task_id = parts[2] if len(parts) > 2 else ""
        if verb not in {"status", "runs", "prsum"} or not task_id.startswith("op_"):
            await query.answer(text="Action inconnue")
            return
        payload = await asyncio.to_thread(self._cockpit_api_sync, "GET", f"/api/internal/tasks/{task_id}/autonomy", None, 20)
        if not payload or not payload.get("ok"):
            await query.answer(text="Lecture impossible")
            try:
                await query.edit_message_text(
                    "<b>Repo Cockpit Autonomy</b>\n\nImpossible : <code>"
                    + _html.escape(str((payload or {}).get("description") or payload))[:900]
                    + "</code>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=None,
                )
            except Exception:
                pass
            return
        await query.answer(text="Mis à jour")
        if verb == "runs":
            text = self._format_runs_status(payload)
            keyboard = self._autonomy_keyboard(payload, verb)
        elif verb == "prsum":
            text = self._format_pr_summary(payload)
            keyboard = self._autonomy_keyboard(payload, "status")
        else:
            text = self._format_autonomy_status(payload)
            keyboard = self._autonomy_keyboard(payload, verb)
        try:
            await query.edit_message_text(
                text[:3900],
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        except Exception:
            pass


    async def _create_task_from_thread_command(
        self,
        msg: Message,
        task_text: str,
        *,
        mode: str | None = None,
        intent: str | None = None,
        source: str = "telegram_task_command",
    ) -> None:
        user_id = str(getattr(getattr(msg, "from_user", None), "id", "") or getattr(msg, "chat_id", ""))
        payload = {
            "telegram_user_id": user_id,
            "chat_id": str(getattr(msg, "chat_id", "")),
            "task": task_text,
            "source": source,
        }
        if mode:
            payload["mode"] = normalize_cockpit_mode(mode)
        if intent:
            payload["intent"] = str(intent)
        data = await asyncio.to_thread(self._cockpit_api_sync, "POST", "/api/internal/tasks/from-thread", payload, 30)
        if not data.get("ok"):
            return await self._send_cockpit_text(
                msg,
                "<b>📌 Tâche non créée</b>\n\n"
                "Ouvre d'abord un chat projet avec <code>/new</code>, puis sélectionne un repo ou crée un projet.\n\n"
                "<code>" + _html.escape(str(data.get("description") or data))[:900] + "</code>",
                role="preview",
            )
        mode = normalize_cockpit_mode(data.get("mode"))
        is_autonomous = mode in {"pilote", "autopilot"}
        next_action = (
            "Pilote lancé automatiquement"
            if mode == "pilote"
            else ("Autopilot lancé automatiquement" if mode == "autopilot" else "/worker execute")
        )
        text = (
            "<b>📌 Tâche créée</b>\n\n"
            f"ID : <code>{_html.escape(data.get('id',''))}</code>\n"
            f"Repo : <code>{_html.escape(data.get('repo',''))}</code>\n"
            f"Mode : <b>{_html.escape(self._mode_title(data.get('mode','ask_review')))}</b>\n"
            f"Statut : <code>{_html.escape(data.get('status',''))}</code>\n"
            f"Approval : <code>{_html.escape(str(data.get('approval_status') or ''))}</code>\n\n"
            f"Prochaine étape : <code>{_html.escape(next_action)}</code>."
        )
        await self._send_cockpit_text(msg, text, role="sticky")
        if is_autonomous:
            asyncio.create_task(self._run_autopilot_worker_after_task_create(msg, data.get("id", "")))

    async def _run_autopilot_worker_after_task_create(self, msg: Message, task_id: str) -> None:
        started_at = time.monotonic()
        chat_id = getattr(msg, "chat_id", None) or getattr(getattr(msg, "chat", None), "id", None)
        live_message_id: int | None = None
        last_live_text = ""

        async def fetch_autonomy(timeout: int = 20) -> dict:
            if not task_id:
                return {"ok": False, "task": {"id": "", "status": "queued_plan", "mode": "autopilot"}}
            data = await asyncio.to_thread(
                self._cockpit_api_sync,
                "GET",
                f"/api/internal/tasks/{task_id}/autonomy",
                None,
                timeout,
            )
            if not data or not data.get("ok"):
                return {
                    "ok": False,
                    "task": {"id": task_id, "status": "queued_plan", "mode": "autopilot"},
                    "description": (data or {}).get("description") if isinstance(data, dict) else str(data),
                }
            return data

        async def publish_live(data: dict, *, force: bool = False) -> None:
            nonlocal live_message_id, last_live_text
            elapsed = int(time.monotonic() - started_at)
            text = self._format_autopilot_live_card(data, elapsed_seconds=elapsed)
            if not force and text == last_live_text:
                return
            keyboard = self._autonomy_keyboard(data, "status")
            if live_message_id and chat_id is not None:
                edited = await self._edit_cockpit_panel(chat_id, live_message_id, text, keyboard)
                if edited:
                    last_live_text = text
                    return
            sent = await self._send_cockpit_panel(msg, text, keyboard, role="progress")
            live_message_id = getattr(sent, "message_id", None) if sent else None
            last_live_text = text

        runtime_observer_state = {"signature": ""}

        async def observe_runtime_signal(*, force: bool = False) -> dict:
            """Attach fresh log errors to the active autonomous task while it is working."""
            if not task_id:
                return {}
            from gateway.libre_orchestrator import scan_watch_logs

            report = await asyncio.to_thread(scan_watch_logs, self._libre_watch_log_paths(), limit=30)
            if str(report.get("status") or "green") == "green" or not report.get("items"):
                return report
            signature = self._libre_watch_signature(report)
            if not force and (not signature or signature == runtime_observer_state.get("signature")):
                return report
            runtime_observer_state["signature"] = signature
            try:
                await asyncio.to_thread(
                    post_runtime_observations,
                    self._cockpit_api_sync,
                    task_id=task_id,
                    report=report,
                    timeout=10,
                    prefer_v2=False,
                )
            except Exception:
                logger.debug("[%s] Runtime observation attach failed for %s", self.name, task_id, exc_info=True)
            if not force:
                await self._send_cockpit_text(
                    msg,
                    self._format_runtime_observation_notice(report),
                    role="progress",
                )
            return report

        initial = await fetch_autonomy(timeout=10)
        await publish_live(initial, force=True)

        try:
            worker_task = asyncio.create_task(
                asyncio.to_thread(
                    self._cockpit_api_sync,
                    "POST",
                    "/api/worker/run-once",
                    {
                        "status": "queued_plan",
                        "execute": True,
                        "runtime_observer": {
                            "enabled": True,
                            "task_id": task_id,
                            "source": "telegram_autonomous_worker",
                            "mode": "during_work",
                        },
                    },
                    1800,
                )
            )
            last_phase = ""
            while not worker_task.done():
                elapsed = int(time.monotonic() - started_at)
                interval = 12 if elapsed < 120 else 30
                try:
                    await asyncio.wait_for(asyncio.shield(worker_task), timeout=interval)
                except asyncio.TimeoutError:
                    pass
                data = await fetch_autonomy(timeout=20)
                await observe_runtime_signal()
                task = data.get("task") or {}
                phase = str(task.get("status") or task.get("current_phase") or "")
                await publish_live(data, force=(phase != last_phase))
                last_phase = phase

            worker = await worker_task
            await observe_runtime_signal(force=True)
            result = worker.get("result") or {}
            status = result.get("status") or worker.get("status")
            task = await asyncio.to_thread(self._cockpit_api_sync, "GET", f"/api/tasks/{task_id}", None, 20) if task_id else {}
            if isinstance(task, dict) and status and not task.get("status"):
                task["status"] = status
            autonomy = await fetch_autonomy(timeout=20)
            await publish_live(autonomy, force=True)
            await self._send_cockpit_text(
                msg,
                self._format_autopilot_final(task_id, worker, task if isinstance(task, dict) else {}, autonomy),
                role="bot_reply",
            )
        except Exception as exc:
            fallback = {
                "ok": False,
                "task": {"id": task_id, "status": "blocked_worker", "mode": "autopilot"},
                "latest_error": {"category": "worker_exception", "runbook": "read_runs_and_logs"},
            }
            await publish_live(fallback, force=True)
            await self._send_cockpit_text(
                msg,
                "<b>🚨 Autopilot bloqué</b>\n\n<code>" + _html.escape(str(exc))[:1200] + "</code>",
                role="progress",
            )

    async def _send_approve_command(self, msg: Message, args: str = "") -> None:
        task_id = (args or "").strip().split()[0] if args.strip() else ""
        note = (args or "").strip()[len(task_id):].strip() if task_id else ""
        if not task_id:
            return await self._send_cockpit_text(msg, "Usage : <code>/approve op_xxx</code>", role="preview")
        user_id = str(getattr(getattr(msg, "from_user", None), "id", "") or getattr(msg, "chat_id", ""))
        data = await asyncio.to_thread(self._cockpit_api_sync, "POST", f"/api/internal/tasks/{task_id}/approve", {"actor": user_id, "note": note}, 20)
        if not data.get("ok"):
            return await self._send_cockpit_text(msg, "<b>Approval impossible</b>\n\n<code>" + _html.escape(str(data.get("description") or data))[:1000] + "</code>", role="progress")
        await self._send_cockpit_text(
            msg,
            "<b>✅ Plan approuvé</b>\n\n"
            f"Tâche : <code>{_html.escape(task_id)}</code>\n"
            f"Statut : <code>{_html.escape(data.get('status',''))}</code>\n\n"
            "Lance <code>/worker implementation</code> pour démarrer l'implémentation.",
            role="sticky",
        )

    async def _send_worker_command(self, msg: Message, args: str = "") -> None:
        words = (args or "").strip().split()
        if words and words[0] in {"run", "run-once", "execute"}:
            execute = words[0] == "execute" or "--execute" in words
            data = await asyncio.to_thread(self._cockpit_api_sync, "POST", "/api/worker/run-once", {"status": "queued_plan", "execute": execute}, 1800)
            return await self._send_cockpit_text(msg, "<b>⚙️ Worker run-once</b>\n\n<pre><code>" + _html.escape(json.dumps(data, ensure_ascii=False, indent=2)[-3000:]) + "</code></pre>")
        if words and words[0] in {"implementation", "impl"}:
            execute = "--dry-run" not in words
            data = await asyncio.to_thread(self._cockpit_api_sync, "POST", "/api/worker/run-once", {"status": "queued_implementation", "execute": execute}, 1800)
            return await self._send_cockpit_text(msg, "<b>⚙️ Worker run-once</b>\n\n<pre><code>" + _html.escape(json.dumps(data, ensure_ascii=False, indent=2)[-3000:]) + "</code></pre>")
        data = await asyncio.to_thread(self._cockpit_api_sync, "GET", "/api/worker/status", None, 20)
        lines = ["<b>⚙️ Worker Repo Cockpit</b>", "", f"Timer : <code>{_html.escape(str(data.get('timer_active','?')))}</code>", "Queue :"]
        for k,v in (data.get("queue_counts") or {}).items(): lines.append(f"- {_html.escape(str(k))}: <b>{v}</b>")
        lines.append("\nActions : <code>/worker run</code> dry-run · <code>/worker execute</code> réel")
        await self._send_cockpit_text(msg, "\n".join(lines))

    async def _send_quota_command(self, msg: Message) -> None:
        q = await asyncio.to_thread(self._cockpit_api_sync, "GET", "/api/quota", None, 20)
        main = (q.get("main") or {}).get("primary_remaining_percent") or q.get("main_primary_remaining")
        spark = ((q.get("additional") or {}).get("gpt-5.3-codex-spark") or {}).get("primary_remaining_percent") or q.get("spark_primary_remaining")
        await self._send_cockpit_text(msg, f"<b>📊 Quota</b>\n\nGPT-5.5/main : <b>{_html.escape(str(main))}%</b>\nSpark : <b>{_html.escape(str(spark))}%</b>\nSource : <code>{_html.escape(str(q.get('source','unknown')))}</code>")

    async def _send_logs_command(self, msg: Message, args: str = "") -> None:
        n = 60
        try:
            if args.strip(): n = max(10, min(int(args.strip().split()[0]), 200))
        except Exception:
            pass
        data = await asyncio.to_thread(self._cockpit_api_sync, "GET", f"/api/logs?lines={n}", None, 20)
        chunks = []
        for item in (data.get("logs") or [])[:3]:
            chunks.append(f"# {item.get('file')}\n" + "\n".join(item.get("lines") or [])[-2500:])
        text = "\n\n".join(chunks) or "Aucun log worker."
        await self._send_cockpit_text(msg, "<b>📜 Logs worker</b>\n\n<pre><code>" + _html.escape(text[-3300:]) + "</code></pre>")
