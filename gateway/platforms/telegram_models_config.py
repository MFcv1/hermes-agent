"""Telegram UI for /models and Repo Cockpit model + reasoning picks."""

from __future__ import annotations

import asyncio
import html as _html
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from gateway.telegram_model_quick_picks import (
    TELEGRAM_QUICK_MODELS,
    TELEGRAM_REASONING_LEVELS,
)
from gateway.platforms.base import SendResult

logger = logging.getLogger(__name__)

REPO_COCKPIT_MODES = {"ask_review", "pilote", "autopilot"}


def normalize_cockpit_mode(mode: str | None) -> str:
    clean = str(mode or "").strip().lower()
    return clean if clean in REPO_COCKPIT_MODES else "ask_review"


OnModelsApply = Callable[[str, str, str, str], Awaitable[str]]


class TelegramModelsConfigMixin:
    """Mixin for TelegramAdapter — model/reasoning pickers."""

    def _ensure_models_config_state(self) -> None:
        if not hasattr(self, "_cockpit_new_chat_prefs"):
            self._cockpit_new_chat_prefs: Dict[str, dict] = {}
        if not hasattr(self, "_models_config_state"):
            self._models_config_state: Dict[str, dict] = {}

    def _load_default_llm_prefs(self) -> dict:
        try:
            from gateway.run import _load_gateway_config

            cfg = _load_gateway_config() or {}
            model_cfg = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
            if not isinstance(model_cfg, dict):
                model_cfg = {}
            return {
                "model": str(model_cfg.get("default") or "grok-composer-2.5-fast"),
                "provider": str(model_cfg.get("provider") or "xai-oauth"),
                "reasoning_effort": "medium",
            }
        except Exception:
            return {
                "model": "grok-composer-2.5-fast",
                "provider": "xai-oauth",
                "reasoning_effort": "medium",
            }

    def _get_cockpit_llm_prefs(self, user_id: str) -> dict:
        self._ensure_models_config_state()
        stored = self._cockpit_new_chat_prefs.get(str(user_id))
        base = self._load_default_llm_prefs()
        if isinstance(stored, dict):
            base.update({k: v for k, v in stored.items() if v})
        return base

    def _set_cockpit_llm_prefs(self, user_id: str, **updates: str) -> dict:
        self._ensure_models_config_state()
        prefs = self._get_cockpit_llm_prefs(user_id)
        for key, val in updates.items():
            if val is not None and str(val).strip():
                prefs[key] = str(val).strip()
        self._cockpit_new_chat_prefs[str(user_id)] = dict(prefs)
        return prefs

    def _llm_prefs_summary_html(self, prefs: dict) -> str:
        model = _html.escape(str(prefs.get("model") or "?"))
        provider = _html.escape(str(prefs.get("provider") or "?"))
        effort = _html.escape(str(prefs.get("reasoning_effort") or "medium"))
        return (
            f"Modèle tâche : <code>{model}</code> · <code>{provider}</code>\n"
            f"Réflexion : <b>{effort}</b>"
        )

    def _quick_model_label(self, index: int) -> str:
        if 0 <= index < len(TELEGRAM_QUICK_MODELS):
            return TELEGRAM_QUICK_MODELS[index][0]
        return "?"

    def _build_cockpit_model_keyboard(self, mode: str):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        mode = normalize_cockpit_mode(mode)
        rows = []
        row: list = []
        for idx, (label, _mid, _prov) in enumerate(TELEGRAM_QUICK_MODELS):
            row.append(
                InlineKeyboardButton(label, callback_data=f"rcp:qm:{idx}:{mode}")
            )
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton("◀ Retour", callback_data=f"rcn:mode:{mode}")])
        return InlineKeyboardMarkup(rows)

    def _build_cockpit_reason_keyboard(self, mode: str):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        mode = normalize_cockpit_mode(mode)
        rows = []
        row: list = []
        for idx, (label, _eff) in enumerate(TELEGRAM_REASONING_LEVELS):
            row.append(
                InlineKeyboardButton(label, callback_data=f"rcp:re:{idx}:{mode}")
            )
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton("◀ Retour", callback_data=f"rcn:mode:{mode}")])
        return InlineKeyboardMarkup(rows)

    def _build_models_config_keyboard(self, chat_id: str):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        state = self._models_config_state.get(str(chat_id)) or {}
        sel_model = int(state.get("quick_model_idx", 0))
        sel_re = int(state.get("reason_idx", 2))
        rows = []
        mrow: list = []
        for idx, (label, _mid, _prov) in enumerate(TELEGRAM_QUICK_MODELS):
            prefix = "✓ " if idx == sel_model else ""
            mrow.append(
                InlineKeyboardButton(
                    f"{prefix}{label}",
                    callback_data=f"msc:qm:{idx}",
                )
            )
            if len(mrow) == 2:
                rows.append(mrow)
                mrow = []
        if mrow:
            rows.append(mrow)
        rrow: list = []
        for idx, (label, _eff) in enumerate(TELEGRAM_REASONING_LEVELS):
            prefix = "✓ " if idx == sel_re else ""
            rrow.append(
                InlineKeyboardButton(
                    f"{prefix}{label}",
                    callback_data=f"msc:re:{idx}",
                )
            )
        rows.append(rrow)
        rows.append(
            [
                InlineKeyboardButton("Appliquer (session)", callback_data="msc:apply"),
                InlineKeyboardButton("Tous les modèles", callback_data="msc:full"),
            ]
        )
        rows.append([InlineKeyboardButton("Annuler", callback_data="msc:cancel")])
        return InlineKeyboardMarkup(rows)

    def _models_config_text(self, chat_id: str, current_model: str, current_provider: str, current_reasoning: str) -> str:
        state = self._models_config_state.get(str(chat_id)) or {}
        qm = int(state.get("quick_model_idx", 0))
        re_idx = int(state.get("reason_idx", 2))
        pick_model = TELEGRAM_QUICK_MODELS[qm][1] if 0 <= qm < len(TELEGRAM_QUICK_MODELS) else current_model
        pick_eff = (
            TELEGRAM_REASONING_LEVELS[re_idx][1]
            if 0 <= re_idx < len(TELEGRAM_REASONING_LEVELS)
            else current_reasoning
        )
        return (
            "<b>⚙ Modèle & réflexion</b>\n\n"
            f"Session actuelle : <code>{_html.escape(current_model or '?')}</code> "
            f"· <code>{_html.escape(current_provider or '?')}</code>\n"
            f"Réflexion actuelle : <b>{_html.escape(current_reasoning)}</b>\n\n"
            f"Sélection : <code>{_html.escape(pick_model)}</code> · réflexion <b>{_html.escape(pick_eff)}</b>\n\n"
            "Choisis un modèle rapide et un niveau de réflexion, puis <b>Appliquer</b>."
        )

    async def send_models_config_picker(
        self,
        *,
        chat_id: str,
        current_model: str,
        current_provider: str,
        current_reasoning: str,
        on_apply: OnModelsApply,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Open combined model + reasoning picker (/models)."""
        from telegram.constants import ParseMode

        self._ensure_models_config_state()
        if not self._bot:
            return SendResult(success=False, error="Bot not connected")

        qm = 0
        for idx, (_label, mid, prov) in enumerate(TELEGRAM_QUICK_MODELS):
            if mid == current_model and prov == current_provider:
                qm = idx
                break
        re_idx = 2
        for idx, (_label, eff) in enumerate(TELEGRAM_REASONING_LEVELS):
            if eff == current_reasoning:
                re_idx = idx
                break

        self._models_config_state[str(chat_id)] = {
            "quick_model_idx": qm,
            "reason_idx": re_idx,
            "on_apply": on_apply,
            "metadata": metadata or {},
            "display_model": current_model,
            "display_provider": current_provider,
            "display_reasoning": current_reasoning,
        }

        text = self._models_config_text(chat_id, current_model, current_provider, current_reasoning)
        keyboard = self._build_models_config_keyboard(chat_id)
        try:
            kwargs: dict = {
                "chat_id": int(chat_id),
                "text": text[:3900],
                "parse_mode": ParseMode.HTML,
                "reply_markup": keyboard,
                "disable_web_page_preview": True,
            }
            thread_id = None
            if metadata:
                thread_id = metadata.get("telegram_thread_id") or metadata.get("thread_id")
            if thread_id is not None:
                kwargs["message_thread_id"] = int(thread_id)
            msg = await self._bot.send_message(**kwargs)
            self._models_config_state[str(chat_id)]["msg_id"] = msg.message_id
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as exc:
            logger.warning("[%s] send_models_config_picker failed: %s", self.name, exc)
            return SendResult(success=False, error=str(exc))

    async def _handle_models_config_callback(self, query, data: str, chat_id: str) -> None:
        from telegram.constants import ParseMode

        self._ensure_models_config_state()
        state = self._models_config_state.get(str(chat_id))
        if not state:
            await query.answer(text="Sélecteur expiré — relance /models")
            return

        if data == "msc:cancel":
            self._models_config_state.pop(str(chat_id), None)
            await query.edit_message_text("Sélection annulée.", reply_markup=None)
            await query.answer()
            return

        if data.startswith("msc:qm:"):
            try:
                idx = int(data.split(":", 2)[2])
            except (ValueError, IndexError):
                await query.answer(text="Choix invalide")
                return
            if 0 <= idx < len(TELEGRAM_QUICK_MODELS):
                state["quick_model_idx"] = idx
            await query.edit_message_text(
                self._models_config_text(
                    chat_id,
                    state.get("display_model", ""),
                    state.get("display_provider", ""),
                    state.get("display_reasoning", "medium"),
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=self._build_models_config_keyboard(chat_id),
            )
            await query.answer()
            return

        if data.startswith("msc:re:"):
            try:
                idx = int(data.split(":", 2)[2])
            except (ValueError, IndexError):
                await query.answer(text="Choix invalide")
                return
            if 0 <= idx < len(TELEGRAM_REASONING_LEVELS):
                state["reason_idx"] = idx
            await query.edit_message_text(
                self._models_config_text(
                    chat_id,
                    state.get("display_model", ""),
                    state.get("display_provider", ""),
                    state.get("display_reasoning", "medium"),
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=self._build_models_config_keyboard(chat_id),
            )
            await query.answer()
            return

        if data == "msc:full":
            await query.answer(text="Ouvre /model pour la liste complète")
            return

        if data == "msc:apply":
            callback = state.get("on_apply")
            if not callable(callback):
                await query.answer(text="Sélecteur expiré")
                return
            qm = int(state.get("quick_model_idx", 0))
            re_idx = int(state.get("reason_idx", 2))
            if not (0 <= qm < len(TELEGRAM_QUICK_MODELS)):
                await query.answer(text="Modèle invalide")
                return
            if not (0 <= re_idx < len(TELEGRAM_REASONING_LEVELS)):
                await query.answer(text="Réflexion invalide")
                return
            _label, model_id, provider_slug = TELEGRAM_QUICK_MODELS[qm]
            _rlabel, effort = TELEGRAM_REASONING_LEVELS[re_idx]
            try:
                result_text = await callback(chat_id, model_id, provider_slug, effort)
            except Exception as exc:
                logger.error("models config apply failed: %s", exc)
                result_text = f"Erreur : {exc}"
            self._models_config_state.pop(str(chat_id), None)
            try:
                await query.edit_message_text(result_text[:3900], parse_mode=ParseMode.MARKDOWN)
            except Exception:
                await query.edit_message_text(result_text[:3900], reply_markup=None)
            await query.answer(text="Appliqué")
            return

        await query.answer()

    async def _handle_cockpit_prefs_callback(
        self,
        query,
        data: str,
        caller_id: str,
        mode: str,
    ) -> None:
        from telegram.constants import ParseMode

        mode = normalize_cockpit_mode(mode)
        parts = data.split(":")
        if len(parts) < 4:
            await query.answer()
            return
        verb = parts[1]
        if verb == "qm":
            try:
                idx = int(parts[2])
            except ValueError:
                await query.answer(text="Invalide")
                return
            if not (0 <= idx < len(TELEGRAM_QUICK_MODELS)):
                await query.answer(text="Invalide")
                return
            _label, model_id, provider_slug = TELEGRAM_QUICK_MODELS[idx]
            self._set_cockpit_llm_prefs(caller_id, model=model_id, provider=provider_slug)
            await self._sync_cockpit_llm_prefs_to_api(caller_id, mode, str(query.message.chat_id))
            prefs = self._get_cockpit_llm_prefs(caller_id)
            await query.answer(text=f"Modèle : {_label}")
            try:
                await query.edit_message_text(
                    "<b>Modèle pour cette tâche</b>\n\n" + self._llm_prefs_summary_html(prefs),
                    parse_mode=ParseMode.HTML,
                    reply_markup=self._build_cockpit_model_keyboard(mode),
                )
            except Exception:
                pass
            return
        if verb == "re":
            try:
                idx = int(parts[2])
            except ValueError:
                await query.answer(text="Invalide")
                return
            if not (0 <= idx < len(TELEGRAM_REASONING_LEVELS)):
                await query.answer(text="Invalide")
                return
            _label, effort = TELEGRAM_REASONING_LEVELS[idx]
            self._set_cockpit_llm_prefs(caller_id, reasoning_effort=effort)
            await self._sync_cockpit_llm_prefs_to_api(caller_id, mode, str(query.message.chat_id))
            prefs = self._get_cockpit_llm_prefs(caller_id)
            await query.answer(text=f"Réflexion : {_label}")
            try:
                await query.edit_message_text(
                    "<b>Réflexion pour cette tâche</b>\n\n" + self._llm_prefs_summary_html(prefs),
                    parse_mode=ParseMode.HTML,
                    reply_markup=self._build_cockpit_reason_keyboard(mode),
                )
            except Exception:
                pass
            return
        await query.answer()

    async def _sync_cockpit_llm_prefs_to_api(
        self,
        telegram_user_id: str,
        mode: str,
        chat_id: str,
    ) -> None:
        mode = normalize_cockpit_mode(mode)
        prefs = self._get_cockpit_llm_prefs(telegram_user_id)
        payload = {
            "telegram_user_id": str(telegram_user_id),
            "mode": mode,
            "chat_id": str(chat_id or ""),
            "chat_model": prefs.get("model"),
            "chat_provider": prefs.get("provider"),
            "reasoning_effort": prefs.get("reasoning_effort"),
        }
        try:
            await asyncio.to_thread(
                self._cockpit_api_sync,
                "POST",
                "/api/internal/state",
                payload,
                10,
            )
        except Exception:
            pass

    def _cockpit_register_thread_llm_prefs(
        self,
        *,
        chat_id: str,
        thread_id: str,
        telegram_user_id: str,
    ) -> None:
        if not thread_id:
            return
        prefs = self._get_cockpit_llm_prefs(telegram_user_id)
        from gateway.cockpit_thread_prefs import set_pending

        set_pending(
            platform="telegram",
            chat_id=str(chat_id),
            thread_id=str(thread_id),
            model=str(prefs.get("model") or ""),
            provider=str(prefs.get("provider") or ""),
            reasoning_effort=str(prefs.get("reasoning_effort") or "medium"),
        )

    def _new_chat_keyboard_with_prefs(self, mode: str, user_id: str):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        mode = normalize_cockpit_mode(mode)
        prefs = self._get_cockpit_llm_prefs(user_id)
        short_model = str(prefs.get("model") or "?")
        if len(short_model) > 18:
            short_model = short_model[:17] + "…"
        effort = str(prefs.get("reasoning_effort") or "medium")
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        ("✓ Ask review" if mode == "ask_review" else "Ask review"),
                        callback_data="rcn:mode:ask_review",
                    ),
                    InlineKeyboardButton(
                        ("✓ Pilote" if mode == "pilote" else "Pilote"),
                        callback_data="rcn:mode:pilote",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        ("✓ Autopilot" if mode == "autopilot" else "Autopilot"),
                        callback_data="rcn:mode:autopilot",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        f"Modèle · {short_model}",
                        callback_data=f"rcn:pickmodel:{mode}",
                    ),
                    InlineKeyboardButton(
                        f"Réflexion · {effort}",
                        callback_data=f"rcn:pickreason:{mode}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "Projet GitHub existant",
                        callback_data=f"rcn:existing:{mode}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "Start from scratch",
                        callback_data=f"rcn:scratch:{mode}",
                    ),
                ],
                [
                    InlineKeyboardButton("Annuler", callback_data="rcn:cancel"),
                ],
            ]
        )

    def _new_chat_text_with_prefs(
        self,
        mode: str,
        user_id: str,
        selected_repo: str | None = None,
    ) -> str:
        mode = normalize_cockpit_mode(mode)
        prefs = self._get_cockpit_llm_prefs(user_id)
        repo_line = (
            f"Repo actuel : <code>{_html.escape(selected_repo)}</code>"
            if selected_repo
            else "Repo actuel : <i>aucun repo sélectionné</i>"
        )
        return (
            "<b>🧭 Nouveau chat Hermes</b>\n\n"
            f"Mode : <b>{_html.escape(self._mode_title(mode))}</b>\n"
            f"Effet : {_html.escape(self._mode_note(mode))}.\n"
            f"{self._llm_prefs_summary_html(prefs)}\n"
            f"{repo_line}\n\n"
            "Choisis modèle / réflexion, puis un repo existant ou un nouveau projet."
        )