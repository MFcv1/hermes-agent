"""Telegram Libre and Repo Cockpit conversation-thread mixin."""

from __future__ import annotations

import asyncio
import html as _html
import json
import logging
import os
import re
import subprocess
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict

from gateway.libre_orchestrator import (
    ActiveWorkStore,
    classify_libre_message,
    extract_learning_policy,
    scan_watch_logs,
)
from gateway.repo_cockpit_text import mode_title, normalize_cockpit_mode

logger = logging.getLogger("gateway.platforms.telegram")
Message = Any


def InlineKeyboardButton(*args: Any, **kwargs: Any) -> Any:
    from gateway.platforms import telegram as telegram_mod

    return telegram_mod.InlineKeyboardButton(*args, **kwargs)


def InlineKeyboardMarkup(*args: Any, **kwargs: Any) -> Any:
    from gateway.platforms import telegram as telegram_mod

    return telegram_mod.InlineKeyboardMarkup(*args, **kwargs)


class _ParseModeProxy:
    @property
    def HTML(self) -> str:
        from gateway.platforms import telegram as telegram_mod

        return getattr(telegram_mod.ParseMode, "HTML", "HTML")


ParseMode = _ParseModeProxy()


class TelegramConversationsMixin:
    def _libre_context_key(self, msg: Message, user_id: str | None = None) -> str:
        chat_id = str(getattr(msg, "chat_id", "") or getattr(getattr(msg, "chat", None), "id", ""))
        thread_id = str(getattr(msg, "message_thread_id", "") or "")
        uid = str(user_id or getattr(getattr(msg, "from_user", None), "id", "") or chat_id)
        return f"telegram:{chat_id}:{thread_id}:{uid}"

    def _libre_store(self):
        from hermes_constants import get_hermes_home
        from gateway.libre_orchestrator import ActiveWorkStore

        return ActiveWorkStore(get_hermes_home() / "libre" / "state.json")

    def _libre_watch_log_paths(self) -> list:
        from hermes_constants import get_hermes_home

        home = get_hermes_home()
        return [
            home / "logs" / "gateway.log",
            home / "logs" / "repo-cockpit.log",
            home / "logs" / "errors.log",
        ]

    def _libre_watch_target(self):
        return getattr(self.config, "home_channel", None)

    def _libre_watch_signature(self, report: dict) -> str:
        items = report.get("items") or []
        return "\n".join(
            f"{item.get('file','')}::{item.get('line','')}"
            for item in items[:8]
            if isinstance(item, dict)
        )

    def _format_runtime_observation_notice(self, report: dict) -> str:
        items = report.get("items") or []
        lines = [
            "<b>🛠️ Erreur captée pendant le travail</b>",
            "",
            "Je ne fais pas un checkup séparé : je rattache ce signal à la tâche autonome en cours pour que l'agent le traite dans son contexte.",
        ]
        if items:
            lines.append("")
            for item in items[:3]:
                path = str(item.get("file") or "").rsplit("/", 1)[-1]
                line = str(item.get("line") or "")[-220:]
                lines.append(f"- <code>{_html.escape(path)}</code> · <code>{_html.escape(line)}</code>")
        return "\n".join(lines)

    def _format_libre_watch_alert(self, report: dict) -> str:
        lines = [
            "<b>👁️ Watch Libre autonome</b>",
            "",
            "J'ai détecté un nouveau signal dans les logs sans attendre de commande.",
            f"Statut : <b>{_html.escape(str(report.get('status') or 'attention'))}</b>",
            f"Erreurs récentes : <b>{int(report.get('error_count') or 0)}</b>",
        ]
        items = report.get("items") or []
        if items:
            lines.extend(["", "<b>Derniers signaux</b>"])
            for item in items[:5]:
                path = str(item.get("file") or "").rsplit("/", 1)[-1]
                line = str(item.get("line") or "")[-240:]
                lines.append(f"- <code>{_html.escape(path)}</code> · <code>{_html.escape(line)}</code>")
        lines.extend([
            "",
            "Action safe pour l'instant : alerte + déduplication. Prochaine couche : ouvrir automatiquement une mission de réparation en branche/worktree.",
        ])
        return "\n".join(lines)

    async def _libre_watch_tick(self) -> bool:
        from gateway.libre_orchestrator import scan_watch_logs

        target = self._libre_watch_target()
        if not target or not getattr(target, "chat_id", None):
            return False
        report = await asyncio.to_thread(scan_watch_logs, self._libre_watch_log_paths(), limit=30)
        if str(report.get("status") or "green") == "green" or not report.get("items"):
            return False
        signature = self._libre_watch_signature(report)
        if not signature or signature == getattr(self, "_libre_watch_last_signature", ""):
            return False
        self._libre_watch_last_signature = signature
        metadata: Dict[str, Any] = {"notify": True, "non_conversational": True}
        if getattr(target, "thread_id", None):
            metadata["thread_id"] = str(target.thread_id)
        result = await self.send(str(target.chat_id), self._format_libre_watch_alert(report), metadata=metadata)
        return bool(result is None or getattr(result, "success", True))

    async def _libre_watch_loop(self) -> None:
        if self._libre_watch_initial_delay_seconds:
            await asyncio.sleep(self._libre_watch_initial_delay_seconds)
        while True:
            try:
                await self._libre_watch_tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("[%s] Libre Watch autonomous tick failed: %s", self.name, exc, exc_info=True)
            await asyncio.sleep(self._libre_watch_interval_seconds)

    def _start_libre_watch_daemon(self) -> None:
        if not getattr(self, "_libre_watch_enabled", True):
            logger.info("[%s] Libre Watch autonomous daemon disabled by config", self.name)
            return
        if self._libre_watch_task and not self._libre_watch_task.done():
            return
        if not self._libre_watch_target() or not getattr(self._libre_watch_target(), "chat_id", None):
            logger.info("[%s] Libre Watch autonomous daemon not started: no Telegram home channel", self.name)
            return
        self._libre_watch_task = asyncio.create_task(self._libre_watch_loop())
        logger.info("[%s] Libre Watch autonomous daemon started", self.name)

    async def _stop_libre_watch_daemon(self) -> None:
        task = getattr(self, "_libre_watch_task", None)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._libre_watch_task = None

    async def _send_libre_watch_command(self, msg: Message) -> None:
        from gateway.libre_orchestrator import scan_watch_logs

        report = await asyncio.to_thread(scan_watch_logs, self._libre_watch_log_paths(), limit=20)
        lines = [
            "<b>👁️ Watch Libre</b>",
            "",
            f"Statut : <b>{_html.escape(str(report.get('status') or 'green'))}</b>",
            f"Erreurs récentes : <b>{int(report.get('error_count') or 0)}</b>",
        ]
        items = report.get("items") or []
        if items:
            lines.extend(["", "<b>Derniers signaux</b>"])
            for item in items[:5]:
                path = str(item.get("file") or "").rsplit("/", 1)[-1]
                line = str(item.get("line") or "")[-240:]
                lines.append(f"- <code>{_html.escape(path)}</code> · <code>{_html.escape(line)}</code>")
        else:
            lines.append("\nAucun signal bloquant dans les logs surveillés.")
        await self._send_cockpit_text(msg, "\n".join(lines), role="preview")

    def _libre_handoff_from_active(self, active: dict | None) -> dict:
        active = active or {}
        return {
            "repo": str(active.get("repo") or ""),
            "mode": normalize_cockpit_mode(active.get("thread_mode") or active.get("project_mode") or "ask_review"),
            "task": str(active.get("last_task_title") or active.get("last_task_id") or active.get("thread_title") or ""),
            "task_id": str(active.get("last_task_id") or ""),
            "parent_task_id": str(active.get("parent_task_id") or ""),
            "thread_id": str(active.get("thread_id") or ""),
        }

    async def _persist_libre_handoff_to_cockpit(self, handoff: dict, key: str) -> dict | None:
        task_id = str(handoff.get("task_id") or "")
        if not task_id:
            return None
        payload = {
            "reason": str(handoff.get("reason") or "/libre"),
            "summary": str(handoff.get("summary") or "Handoff Libre"),
            "resume_hints": handoff.get("resume_hints") if isinstance(handoff.get("resume_hints"), dict) else {
                "repo": handoff.get("repo", ""),
                "mode": handoff.get("mode", ""),
                "task": handoff.get("task", ""),
                "task_id": task_id,
                "thread_id": handoff.get("thread_id", ""),
            },
            "conversation_key": key,
            "source": "telegram_libre",
            "payload": {"local_handoff_id": handoff.get("id")},
        }
        data = await asyncio.to_thread(self._cockpit_api_sync, "POST", f"/api/internal/tasks/{task_id}/handoff", payload, 20)
        if data and data.get("ok") and isinstance(data.get("handoff"), dict):
            try:
                self._libre_store().cache_handoff(key, {**handoff, **data["handoff"], "source": "repo_cockpit"})
            except Exception:
                logger.debug("[%s] Failed to cache Repo Cockpit handoff", self.name, exc_info=True)
        return data

    async def _fetch_libre_handoff_from_cockpit(self, key: str, task_id: str | None = None) -> dict | None:
        if task_id:
            path = f"/api/internal/tasks/{task_id}/handoff"
        else:
            path = "/api/internal/handoffs/latest?conversation_key=" + urllib.parse.quote(key, safe="")
        data = await asyncio.to_thread(self._cockpit_api_sync, "GET", path, None, 20)
        if data and data.get("ok") and isinstance(data.get("handoff"), dict):
            handoff = data["handoff"]
            self._libre_store().cache_handoff(key, handoff)
            return handoff
        return None

    async def _send_libre_command(self, msg: Message, args: str = "") -> None:
        raw = (args or "").strip().lower()
        if raw in {"watch", "logs", "selfcheck"}:
            return await self._send_libre_watch_command(msg)

        user_id = str(getattr(getattr(msg, "from_user", None), "id", "") or getattr(msg, "chat_id", ""))
        key = self._libre_context_key(msg, user_id)
        self._pilot_intake_states.pop(user_id, None)
        self._repo_new_chat_choices.pop(user_id, None)

        active = None
        try:
            _uid, data, active = await self._get_active_cockpit_thread(msg)
        except Exception:
            data = None
            active = None

        handoff_seed = self._libre_handoff_from_active(active)
        store = self._libre_store()
        if handoff_seed.get("repo") or handoff_seed.get("thread_id"):
            store.set_active(key, **handoff_seed)
        handoff = store.soft_close(key, reason="/libre")
        cockpit_handoff = await self._persist_libre_handoff_to_cockpit(handoff, key)
        if cockpit_handoff and cockpit_handoff.get("ok") and isinstance(cockpit_handoff.get("handoff"), dict):
            handoff = {**handoff, **cockpit_handoff["handoff"]}
        self._libre_chat_states[user_id] = {
            "mode": "libre",
            "key": key,
            "last_handoff": handoff,
            "active_repo": handoff_seed.get("repo", ""),
            "ts": time.time(),
        }

        repo = handoff_seed.get("repo") or "aucun repo actif"
        thread_id = handoff_seed.get("thread_id") or "—"
        task_id = handoff.get("task_id") or "—"
        previous_mode = self._mode_title(handoff_seed.get("mode") or "ask_review")
        lines = [
            "<b>✅ Mode libre activé</b>",
            "",
            "Je quitte le flow chantier/wizard actif sans hard reset.",
            "Mémoire durable conservée. Historique utile conservé côté Hermes.",
            "",
            f"Repo précédent : <code>{_html.escape(repo)}</code>",
            f"Mode précédent : <b>{_html.escape(previous_mode)}</b>",
            f"Thread : <code>{_html.escape(thread_id)}</code>",
            f"Task : <code>{_html.escape(str(task_id))}</code>",
            "",
            "Orchestration auto : je peux rester en chat normal, ou router vers Ask review / Pilote / Autopilot si ton message parle clairement d'un repo.",
            "Observation runtime : pendant un travail autonome, je rattache les erreurs logs à la tâche en cours au lieu de faire des checkups hors contexte.",
        ]
        await self._send_cockpit_text(msg, "\n".join(lines), role="sticky")

    async def _maybe_handle_libre_text(self, msg: Message, text: str) -> bool:
        clean = (text or "").strip()
        if not clean or clean.startswith("/"):
            return False
        user_id = str(getattr(getattr(msg, "from_user", None), "id", "") or getattr(msg, "chat_id", ""))
        state = self._libre_chat_states.get(user_id) or {}
        if state.get("mode") != "libre":
            return False

        from gateway.libre_orchestrator import classify_libre_message, extract_learning_policy

        decision = classify_libre_message(clean)
        if decision.action == "learn_policy":
            policy = extract_learning_policy(clean) or {}
            stored = self._libre_store().remember_policy(state.get("key") or self._libre_context_key(msg, user_id), policy, source="telegram_libre")
            details = " · ".join(
                f"{_html.escape(str(k))}=<code>{_html.escape(str(v))}</code>"
                for k, v in stored.items()
                if k in {"scope", "model", "reasoning_effort"}
            )
            await self._send_cockpit_text(
                msg,
                "<b>✅ Règle apprise</b>\n\n" + (details or "Préférence enregistrée."),
                role="sticky",
            )
            return True

        if decision.action == "switch_repo":
            await self._send_cockpit_text(
                msg,
                "<b>🔁 Switch repo détecté</b>\n\n"
                "Je garde la note de reprise du chantier précédent et j'ouvre le sélecteur de projet.\n"
                "Choisis le repo, puis ton prochain message naturel deviendra la tâche.",
                role="preview",
            )
            await self._send_new_command(msg, "pilote")
            return True

        if decision.action == "resume":
            key = state.get("key") or self._libre_context_key(msg, user_id)
            store = self._libre_store()
            local_handoff = store.latest_handoff(key)
            task_id = str((local_handoff or {}).get("task_id") or "")
            handoff = await self._fetch_libre_handoff_from_cockpit(key, task_id=task_id or None)
            if not handoff:
                handoff = local_handoff
            if not handoff:
                await self._send_cockpit_text(
                    msg,
                    "<b>Reprise</b>\n\nJe n'ai pas encore de handoff fiable pour ce chat. Ouvre un chantier avec <code>/new</code>, ou donne-moi le repo explicitement.",
                    role="preview",
                )
                return True
            resume_hints = handoff.get("resume_hints") if isinstance(handoff.get("resume_hints"), dict) else {}
            parent_task_id = str(handoff.get("task_id") or resume_hints.get("task_id") or "")
            store.set_active(
                key,
                repo=handoff.get("repo") or resume_hints.get("repo") or "",
                mode=handoff.get("mode") or resume_hints.get("mode") or "pilote",
                task=handoff.get("summary") or resume_hints.get("task") or "",
                task_id=parent_task_id,
                parent_task_id=parent_task_id,
                thread_id=handoff.get("thread_id") or resume_hints.get("thread_id") or "",
            )
            self._libre_chat_states[user_id] = {**state, "mode": "libre", "key": key, "last_handoff": handoff, "resume_parent_task_id": parent_task_id}
            await self._send_cockpit_text(
                msg,
                "<b>🔁 Reprise retrouvée</b>\n\n"
                f"Task source : <code>{_html.escape(parent_task_id or '—')}</code>\n"
                f"Repo : <code>{_html.escape(str(handoff.get('repo') or resume_hints.get('repo') or '—'))}</code>\n"
                f"Résumé : {_html.escape(str(handoff.get('summary') or 'Handoff retrouvé'))}\n\n"
                "Ton prochain message de travail repo sera rattaché à ce lineage.",
                role="sticky",
            )
            return True

        if decision.action != "repo_task":
            return False

        user_id, data, active = await self._get_active_cockpit_thread(msg)
        if not data or not data.get("ok") or not active:
            await self._send_cockpit_text(
                msg,
                "<b>Mode libre</b>\n\nJe détecte une demande de travail repo, mais aucun repo actif n'est attaché.\n"
                "Ouvre un chantier avec <code>/new</code> puis choisis un repo, ou donne-moi le repo explicitement.",
                role="preview",
            )
            return True

        await self._create_task_from_thread_command(
            msg,
            clean,
            mode=decision.mode,
            intent=decision.intent,
            parent_task_id=str(state.get("resume_parent_task_id") or (state.get("last_handoff") or {}).get("task_id") or ""),
            source="telegram_libre_router",
        )
        return True

    async def _send_clean_command(self, msg: Message, args: str = "") -> None:
        chat_id = str(getattr(msg, "chat_id", "") or getattr(getattr(msg, "chat", None), "id", ""))
        cleanup_payload = {"chat_id": chat_id, "limit": 80, "roles": ["command_echo", "progress", "debug", "status", "preview"]}
        data = await asyncio.to_thread(self._cockpit_api_sync, "POST", "/api/telegram/cleanup/execute", cleanup_payload, 60)
        if not data.get("ok"):
            return await self._send_cockpit_text(msg, "<b>🧹 Clear</b>\n\n❌ Nettoyage impossible : <code>" + _html.escape(str(data.get("description") or data))[:800] + "</code>", role="progress")
        deleted = data.get("deleted") or []
        failed = data.get("failed") or []
        txt = (
            "<b>🧹 Clear terminé</b>\n\n"
            f"Bruit supprimé : <b>{len(deleted)}</b>\n"
            f"Échecs : <b>{len(failed)}</b>\n\n"
            "Réponses IA conservées."
        )
        if failed:
            txt += "\n\n<details><summary>Échecs</summary><pre><code>" + _html.escape(json.dumps(failed[:10], ensure_ascii=False, indent=2)) + "</code></pre></details>"
        await self._send_cockpit_text(msg, txt, role="progress")

    async def _send_cleanchat_command(self, msg: Message, args: str = "") -> None:
        await self._send_clean_command(msg, args)

    async def _get_active_cockpit_thread(self, msg: Message) -> tuple[str, dict | None, dict | None]:
        user_id = str(getattr(getattr(msg, "from_user", None), "id", "") or getattr(msg, "chat_id", ""))
        data = await asyncio.to_thread(self._cockpit_api_sync, "GET", f"/api/internal/threads/active/{user_id}", None, 10)
        return user_id, data, data.get("active") if data.get("ok") else None

    async def _send_chat_status_command(self, msg: Message) -> None:
        _user_id, data, active = await self._get_active_cockpit_thread(msg)
        if not data or not data.get("ok"):
            return await self._send_cockpit_text(
                msg,
                "<b>💬 Chat actif</b>\n\nImpossible de lire l'état Repo Cockpit : <code>"
                + _html.escape(str((data or {}).get("description") or data))[:800]
                + "</code>",
                role="preview",
            )
        if not active:
            return await self._send_cockpit_text(
                msg,
                "<b>💬 Chat actif</b>\n\nAucun thread actif. Lance <code>/new</code> pour choisir un projet ou créer un nouveau clavardage.",
                role="preview",
            )
        lines = [
            "<b>💬 Chat actif</b>",
            "",
            f"Projet : <code>{_html.escape(active.get('project_title') or active.get('project_id') or '')}</code>",
            f"Repo : <code>{_html.escape(active.get('repo') or '')}</code>",
            f"Mode : <b>{_html.escape(self._mode_title(active.get('thread_mode') or active.get('project_mode') or 'ask_review'))}</b>",
            f"Thread : <code>{_html.escape(active.get('thread_id') or '')}</code>",
            f"Statut : <b>{_html.escape(active.get('thread_status') or '')}</b>",
            "",
            "Actions : <code>/archive</code> range ce chat · <code>/delete</code> le supprime côté Cockpit.",
        ]
        await self._send_cockpit_text(msg, "\n".join(lines), role="preview")

    async def _send_thread_action_command(self, msg: Message, action: str) -> None:
        user_id, data, active = await self._get_active_cockpit_thread(msg)
        title = "Archive" if action == "archive" else "Delete"
        if not data or not data.get("ok"):
            return await self._send_cockpit_text(
                msg,
                f"<b>{_html.escape(title)}</b>\n\nImpossible de lire le thread actif : <code>"
                + _html.escape(str((data or {}).get("description") or data))[:800]
                + "</code>",
                role="progress",
            )
        if not active:
            return await self._send_cockpit_text(
                msg,
                f"<b>{_html.escape(title)}</b>\n\nAucun thread actif à traiter. Lance <code>/new</code> pour ouvrir un clavardage.",
                role="progress",
            )
        thread_id = active.get("thread_id")
        payload = {"telegram_user_id": user_id, "note": f"telegram /{action}"}
        result = await asyncio.to_thread(self._cockpit_api_sync, "POST", f"/api/internal/threads/{thread_id}/{action}", payload, 20)
        if not result.get("ok"):
            return await self._send_cockpit_text(
                msg,
                f"<b>{_html.escape(title)}</b>\n\nAction impossible : <code>"
                + _html.escape(str(result.get("description") or result))[:900]
                + "</code>",
                role="progress",
            )
        verb = "archivé" if action == "archive" else "supprimé côté Cockpit"
        text = (
            f"<b>✅ Thread {verb}</b>\n\n"
            f"Projet : <code>{_html.escape(active.get('project_title') or active.get('project_id') or '')}</code>\n"
            f"Repo : <code>{_html.escape(active.get('repo') or '')}</code>\n"
            f"Thread : <code>{_html.escape(thread_id or '')}</code>\n\n"
            "Repo GitHub conservé. Messages Telegram conservés. Ce chat n'est plus le thread actif."
        )
        await self._send_cockpit_text(msg, text, role="sticky")

    def _thread_status_title(self, status: str) -> str:
        return {
            "active": "Actifs",
            "archived": "Archives",
            "deleted": "Supprimés",
            "all": "Tous",
        }.get(status, "Actifs")

    def _thread_mode_label(self, mode: str | None) -> str:
        return self._mode_title(normalize_cockpit_mode(mode))

    def _format_thread_activity_time(self, value: Any) -> str:
        if value in (None, ""):
            return ""
        try:
            timestamp = int(value)
        except (TypeError, ValueError):
            return str(value)
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        except (OverflowError, OSError, ValueError):
            return str(value)

    def _thread_list_text(self, data: dict, status: str) -> str:
        threads = data.get("threads") or []
        active_thread_id = data.get("active_thread_id")
        lines = [
            f"<b>🧵 Conversations Repo Cockpit — {_html.escape(self._thread_status_title(status))}</b>",
            "",
        ]
        if not threads:
            lines.extend([
                "Aucune conversation dans cette vue.",
                "",
                "Lance <code>/new</code> pour créer un nouveau clavardage.",
            ])
            return "\n".join(lines)
        for index, thread in enumerate(threads[:8], 1):
            thread_id = str(thread.get("thread_id") or "")
            marker = "⭐ " if thread_id == active_thread_id else ""
            title = thread.get("thread_title") or thread.get("project_title") or thread.get("repo") or thread_id
            repo = thread.get("repo") or ""
            mode = self._thread_mode_label(thread.get("thread_mode") or thread.get("project_mode"))
            state = thread.get("thread_status") or ""
            task_state = thread.get("last_task_status") or "aucune tâche"
            task_phase = thread.get("last_task_phase") or ""
            preview = thread.get("preview_url") or thread.get("deployment_url") or ""
            last_activity = (
                thread.get("last_task_updated_at")
                or thread.get("thread_updated_at")
                or thread.get("thread_created_at")
            )
            last_activity_text = self._format_thread_activity_time(last_activity)
            lines.extend([
                f"{index}. {marker}<b>{_html.escape(str(title))}</b>",
                f"   Repo : <code>{_html.escape(str(repo))}</code>",
                f"   Mode : <b>{_html.escape(mode)}</b> · Statut : <b>{_html.escape(str(state))}</b>",
                f"   Dernière tâche : <code>{_html.escape(str(task_state))}</code>"
                + (f" · <code>{_html.escape(str(task_phase))}</code>" if task_phase else ""),
            ])
            if last_activity_text:
                lines.append(f"   Dernière activité : <code>{_html.escape(last_activity_text)}</code>")
            if preview:
                lines.append(f"   Preview : {_html.escape(str(preview))}")
            lines.append(f"   ID conversation : <code>{_html.escape(thread_id)}</code>")
            lines.append("")
        lines.append("Boutons : reprendre, archiver, supprimer ou restaurer côté Repo Cockpit.")
        return "\n".join(lines)

    def _thread_list_keyboard(self, threads: list[dict], status: str) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = [[
            InlineKeyboardButton("Actifs", callback_data="rct:list:active"),
            InlineKeyboardButton("Archives", callback_data="rct:list:archived"),
            InlineKeyboardButton("Tous", callback_data="rct:list:all"),
        ]]
        for thread in threads[:8]:
            thread_id = str(thread.get("thread_id") or "")
            if not thread_id:
                continue
            short = thread_id.replace("thread_", "")[:8]
            title = str(thread.get("thread_title") or thread.get("project_title") or thread.get("repo") or short)
            clean_title = re.sub(r"\s+", " ", title).strip()
            if len(clean_title) > 18:
                clean_title = clean_title[:18].rstrip() + "…"
            resume_label = f"Reprendre {clean_title}" if clean_title else f"Reprendre {short}"
            thread_status = str(thread.get("thread_status") or status)
            action_row = [InlineKeyboardButton(resume_label, callback_data=f"rct:activate:{thread_id}")]
            if thread_status == "archived":
                action_row.append(InlineKeyboardButton("Restaurer", callback_data=f"rct:restore:{thread_id}"))
            elif thread_status == "deleted":
                action_row.append(InlineKeyboardButton("Restaurer", callback_data=f"rct:restore:{thread_id}"))
            else:
                action_row.append(InlineKeyboardButton("Archiver", callback_data=f"rct:archive:{thread_id}"))
                action_row.append(InlineKeyboardButton("Supprimer", callback_data=f"rct:delete:{thread_id}"))
            rows.append(action_row)
        rows.append([InlineKeyboardButton("Nouveau chat", callback_data="rcn:mode:ask_review")])
        return InlineKeyboardMarkup(rows)

    def _thread_action_keyboard(self, thread: dict) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        task_id = str(thread.get("last_task_id") or "")
        preview = str(thread.get("preview_url") or thread.get("deployment_url") or "")
        if preview.startswith(("https://", "http://")):
            rows.append([InlineKeyboardButton("Ouvrir preview", url=preview)])
        if task_id.startswith("op_"):
            rows.append([
                InlineKeyboardButton("Status", callback_data=f"rca:status:{task_id}"),
                InlineKeyboardButton("Runs", callback_data=f"rca:runs:{task_id}"),
            ])
        rows.append([InlineKeyboardButton("Conversations", callback_data="rct:list:all")])
        return InlineKeyboardMarkup(rows)

    def _thread_resume_seed(self, thread: dict) -> str:
        return "\n".join([
            f"Conversation: {thread.get('thread_title') or thread.get('project_title') or thread.get('repo') or thread.get('thread_id')}",
            f"Repo: {thread.get('repo') or ''}",
            f"Mode: {self._thread_mode_label(thread.get('thread_mode') or thread.get('project_mode'))}",
            f"Statut conversation: {thread.get('thread_status') or ''}",
            f"Derniere task: {thread.get('last_task_id') or ''}",
            f"Statut derniere task: {thread.get('last_task_status') or 'aucune tâche'}",
            f"Phase derniere task: {thread.get('last_task_phase') or ''}",
            f"Derniere activite: {self._format_thread_activity_time(thread.get('last_task_updated_at') or thread.get('thread_updated_at') or thread.get('thread_created_at'))}",
            f"Preview: {thread.get('preview_url') or thread.get('deployment_url') or ''}",
            "",
            "Objectif: écrire un message de reprise personnalisé pour Telegram.",
            "Règles de sortie strictes:",
            "- Texte Telegram simple, sans Markdown, sans astérisques, sans gras, sans titre décoratif.",
            "- Ne répète pas les identifiants techniques déjà listés plus haut sauf si utile.",
            "- Sois concret: ce qui est prêt, ce qui bloque, quoi faire maintenant.",
            "- Si tu n'as pas de preuve de tests ou de validations, dis-le clairement.",
            "",
            "Format exact attendu:",
            "- Où on en est : ...",
            "- Ce qui marche : ...",
            "- Point bloquant : ...",
            "- Prochaine action : ...",
        ]).strip()

    @staticmethod
    def _clean_spark_resume_summary(content: str) -> str:
        text = str(content or "").strip()
        if not text:
            return ""
        # Spark may return Markdown even when asked not to. Telegram receives
        # escaped HTML below, so strip lightweight Markdown before display.
        text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
        text = re.sub(r"__(.*?)__", r"\1", text)
        text = re.sub(r"`([^`]+)`", r"\1", text)
        text = re.sub(r"(?m)^\s*[-*]\s+", "- ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _spark_thread_resume_summary_sync(self, thread: dict) -> str | None:
        script = "/home/hermes/repo-cockpit/scripts/spark_guard.py"
        if not os.path.exists(script):
            return None
        try:
            proc = subprocess.run(
                [script, "summarize", "--reasoning", "high"],
                input=self._thread_resume_seed(thread),
                text=True,
                capture_output=True,
                timeout=55,
                cwd="/home/hermes/repo-cockpit",
            )
        except Exception:
            return None
        if proc.returncode != 0:
            return None
        try:
            payload = json.loads(proc.stdout or "{}")
        except Exception:
            return None
        content = str(payload.get("content") or "").strip()
        content = self._clean_spark_resume_summary(content)
        return content[:1800] if content else None

    async def _spark_thread_resume_summary(self, thread: dict) -> str | None:
        return await asyncio.to_thread(self._spark_thread_resume_summary_sync, thread)

    def _thread_resume_text(self, thread: dict, spark_summary: str | None = None) -> str:
        title = thread.get("thread_title") or thread.get("project_title") or thread.get("repo") or thread.get("thread_id")
        repo = thread.get("repo") or ""
        mode = self._thread_mode_label(thread.get("thread_mode") or thread.get("project_mode"))
        task_id = thread.get("last_task_id") or ""
        task_state = thread.get("last_task_status") or "aucune tâche"
        task_phase = thread.get("last_task_phase") or ""
        last_activity = self._format_thread_activity_time(
            thread.get("last_task_updated_at")
            or thread.get("thread_updated_at")
            or thread.get("thread_created_at")
        )
        preview = thread.get("preview_url") or thread.get("deployment_url") or ""
        lines = [
            "<b>✅ Conversation reprise</b>",
            "",
            f"On reprend <b>{_html.escape(str(title))}</b>.",
            f"Repo : <code>{_html.escape(str(repo))}</code>",
            f"Mode : <b>{_html.escape(mode)}</b>",
        ]
        if task_id:
            lines.append(f"Dernière task : <code>{_html.escape(str(task_id))}</code>")
        lines.append(
            f"Point d'arrêt : <code>{_html.escape(str(task_state))}</code>"
            + (f" · <code>{_html.escape(str(task_phase))}</code>" if task_phase else "")
        )
        if last_activity:
            lines.append(f"Dernière activité : <code>{_html.escape(last_activity)}</code>")
        if preview:
            lines.append(f"Preview : {_html.escape(str(preview))}")
        lines.append("")
        if spark_summary:
            lines.extend([
                "<b>Résumé Spark</b>",
                _html.escape(spark_summary),
            ])
        else:
            lines.extend([
                "<b>Résumé</b>",
                "La conversation est active. Tu peux écrire directement la suite ici ; Hermes utilisera ce projet et ce contexte.",
                "Si tu veux changer de mode ou démarrer autre chose, utilise <code>/new</code>.",
            ])
        return "\n".join(lines)

    async def _send_thread_resume_message(self, query, thread: dict) -> None:
        spark_summary = await self._spark_thread_resume_summary(thread)
        try:
            await query.message.reply_text(
                self._thread_resume_text(thread, spark_summary),
                parse_mode=ParseMode.HTML,
                reply_markup=self._thread_action_keyboard(thread),
                **self._link_preview_kwargs(),
            )
        except Exception:
            pass

    def _normalize_thread_status_arg(self, args: str) -> str:
        token = (args or "").strip().split(maxsplit=1)[0].lower()
        aliases = {
            "active": "active",
            "actif": "active",
            "actifs": "active",
            "archive": "archived",
            "archives": "archived",
            "archived": "archived",
            "deleted": "deleted",
            "delete": "deleted",
            "supprime": "deleted",
            "supprimés": "deleted",
            "supprimes": "deleted",
            "all": "all",
            "tout": "all",
            "tous": "all",
        }
        return aliases.get(token, "active")

    async def _fetch_threads_for_user(self, user_id: str, status: str) -> dict:
        return await asyncio.to_thread(
            self._cockpit_api_sync,
            "GET",
            f"/api/internal/threads/{user_id}?status={status}&limit=8",
            None,
            12,
        )

    async def _send_threads_command(self, msg: Message, args: str = "") -> None:
        user_id = str(getattr(getattr(msg, "from_user", None), "id", "") or getattr(msg, "chat_id", ""))
        status = self._normalize_thread_status_arg(args)
        data = await self._fetch_threads_for_user(user_id, status)
        if not data or not data.get("ok"):
            return await self._send_cockpit_text(
                msg,
                "<b>🧵 Conversations Repo Cockpit</b>\n\nImpossible de lire les conversations : <code>"
                + _html.escape(str((data or {}).get("description") or data))[:900]
                + "</code>",
                role="preview",
            )
        await msg.reply_text(
            self._thread_list_text(data, status),
            parse_mode=ParseMode.HTML,
            reply_markup=self._thread_list_keyboard(data.get("threads") or [], status),
            **self._link_preview_kwargs(),
        )

    async def _send_conversations_command(self, msg: Message, args: str = "") -> None:
        # /conv answers "show me my recent conversations", so default to all
        # known Repo Cockpit conversation records.
        await self._send_threads_command(msg, args or "all")

    async def _send_rename_thread_command(self, msg: Message, args: str = "") -> None:
        title = re.sub(r"\s+", " ", (args or "").strip())[:120]
        if not title:
            return await self._send_cockpit_text(
                msg,
                "Usage : <code>/renamechat Tennis map France</code>",
                role="preview",
            )
        user_id, data, active = await self._get_active_cockpit_thread(msg)
        if not data or not data.get("ok"):
            return await self._send_cockpit_text(
                msg,
                "<b>✏️ Renommer chat</b>\n\nImpossible de lire le thread actif : <code>"
                + _html.escape(str((data or {}).get("description") or data))[:800]
                + "</code>",
                role="preview",
            )
        if not active or not active.get("thread_id"):
            return await self._send_cockpit_text(
                msg,
                "<b>✏️ Renommer chat</b>\n\nAucune conversation active. Lance <code>/conv</code> puis reprends un chat.",
                role="preview",
            )
        payload = {"telegram_user_id": user_id, "title": title, "note": "telegram /renamechat"}
        result = await asyncio.to_thread(
            self._cockpit_api_sync,
            "POST",
            f"/api/internal/threads/{active.get('thread_id')}/rename",
            payload,
            20,
        )
        if not result.get("ok"):
            return await self._send_cockpit_text(
                msg,
                "<b>✏️ Renommer chat</b>\n\nAction impossible : <code>"
                + _html.escape(str(result.get("description") or result))[:900]
                + "</code>",
                role="preview",
            )
        await self._send_cockpit_text(
            msg,
            "<b>✅ Chat renommé</b>\n\n"
            f"Ancien nom : <code>{_html.escape(str(result.get('old_title') or ''))}</code>\n"
            f"Nouveau nom : <b>{_html.escape(str(result.get('title') or title))}</b>\n"
            f"Thread : <code>{_html.escape(str(active.get('thread_id') or ''))}</code>",
            role="sticky",
        )

    async def _handle_thread_callback(self, query, data: str, caller_id: str) -> None:
        parts = data.split(":", 2)
        verb = parts[1] if len(parts) > 1 else "list"
        value = parts[2] if len(parts) > 2 else "active"
        status = "active"
        resumed_thread = None
        if verb == "list":
            status = self._normalize_thread_status_arg(value)
            result = await self._fetch_threads_for_user(caller_id, status)
            await query.answer(text=self._thread_status_title(status))
        else:
            if verb not in {"activate", "archive", "delete", "restore"}:
                await query.answer(text="Action inconnue")
                return
            payload = {"telegram_user_id": caller_id, "note": f"telegram /conv {verb}"}
            result = await asyncio.to_thread(
                self._cockpit_api_sync,
                "POST",
                f"/api/internal/threads/{value}/{verb}",
                payload,
                20,
            )
            if not result.get("ok"):
                await query.answer(text="Action impossible")
                try:
                    await query.edit_message_text(
                        "<b>🧵 Conversations Repo Cockpit</b>\n\nErreur : <code>"
                        + _html.escape(str(result.get("description") or result))[:900]
                        + "</code>",
                        parse_mode=ParseMode.HTML,
                        reply_markup=None,
                    )
                except Exception:
                    pass
                return
            labels = {
                "activate": "Thread repris",
                "archive": "Thread archivé",
                "delete": "Thread supprimé",
                "restore": "Thread restauré",
            }
            await query.answer(text=labels.get(verb, "OK"))
            status = "all" if verb == "restore" else "active"
            result = await self._fetch_threads_for_user(caller_id, status)
            if verb == "activate":
                for item in result.get("threads") or []:
                    if str(item.get("thread_id") or "") == value:
                        resumed_thread = item
                        break
        try:
            await query.edit_message_text(
                self._thread_list_text(result, status),
                parse_mode=ParseMode.HTML,
                reply_markup=self._thread_list_keyboard(result.get("threads") or [], status),
                **self._link_preview_kwargs(),
            )
        except Exception:
            pass
        if verb == "activate" and resumed_thread:
            await self._send_thread_resume_message(query, resumed_thread)
