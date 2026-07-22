"""Telegram model picker mixin."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from gateway.platforms.base import SendResult

logger = logging.getLogger("gateway.platforms.telegram")


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

    @property
    def MARKDOWN_V2(self) -> str:
        from gateway.platforms import telegram as telegram_mod

        return getattr(telegram_mod.ParseMode, "MARKDOWN_V2", "MarkdownV2")


ParseMode = _ParseModeProxy()


class TelegramModelPickerMixin:
    async def send_model_picker(
        self,
        chat_id: str,
        providers: list,
        current_model: str,
        current_provider: str,
        session_key: str,
        on_model_selected,
        current_reasoning: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send an interactive inline-keyboard model picker.

        Two-step drill-down: provider selection → model selection.
        Edits the same message in-place as the user navigates.
        """
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        try:
            from hermes_cli.providers import get_label
        except ImportError:
            def get_label(slug):
                return slug

        try:
            # Build provider buttons — folds provider groups (display only).
            keyboard = self._build_provider_keyboard(providers)

            provider_label = get_label(current_provider)
            text = self.format_message(
                (
                    f"⚙ *Model Configuration*\n\n"
                    f"Current model: `{current_model or 'unknown'}`\n"
                    f"Provider: {provider_label}\n\n"
                    f"Select a provider:"
                )
            )

            thread_id = metadata.get("thread_id") if metadata else None
            reply_to_id = self._reply_to_message_id_for_send(None, metadata, reply_to_mode=self._reply_to_mode)
            msg = await self._send_message_with_thread_fallback(
                chat_id=int(chat_id),
                text=text,
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=keyboard,
                reply_to_message_id=reply_to_id,
                **self._thread_kwargs_for_send(
                    chat_id,
                    thread_id,
                    metadata,
                    reply_to_message_id=reply_to_id,
                    reply_to_mode=self._reply_to_mode
                ),
                **self._link_preview_kwargs(),
            )

            # Store picker state keyed by chat_id
            self._model_picker_state[str(chat_id)] = {
                "msg_id": msg.message_id,
                "providers": providers,
                "session_key": session_key,
                "on_model_selected": on_model_selected,
                "current_model": current_model,
                "current_provider": current_provider,
                "current_reasoning": current_reasoning,
            }

            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.warning("[%s] send_model_picker failed: %s", self.name, e)
            return SendResult(success=False, error=str(e))

    _MODEL_PAGE_SIZE = 8

    def _build_provider_keyboard(self, providers: list):
        """Build the top-level provider keyboard, folding provider groups.

        Provider families (Kimi/Moonshot, MiniMax, xAI Grok, ...) collapse to
        a single ``mpg:<gid>`` button; tapping it drills into a member
        sub-keyboard. Single providers (and groups with only one authenticated
        member) render as direct ``mp:<slug>`` buttons. Grouping mirrors the
        CLI ``hermes model`` picker via the shared ``group_providers`` fold,
        so all surfaces stay consistent.
        """
        try:
            from hermes_cli.models import group_providers
        except Exception:
            group_providers = None

        by_slug = {p.get("slug"): p for p in providers}

        def _provider_button(p):
            count = p.get("total_models", len(p.get("models", [])))
            label = f"{p['name']} ({count})"
            if p.get("is_current"):
                label = f"✓ {label}"
            return InlineKeyboardButton(label, callback_data=f"mp:{p['slug']}")

        buttons: list = []
        if group_providers is not None:
            for row in group_providers([p.get("slug") for p in providers]):
                if row["kind"] == "group":
                    members = [by_slug[m] for m in row["members"] if m in by_slug]
                    count = sum(
                        m.get("total_models", len(m.get("models", []))) for m in members
                    )
                    label = f"{row['label']} ▸ ({count})"
                    if any(m.get("is_current") for m in members):
                        label = f"✓ {label}"
                    buttons.append(
                        InlineKeyboardButton(label, callback_data=f"mpg:{row['group_id']}")
                    )
                else:
                    p = by_slug.get(row["slug"])
                    if p is not None:
                        buttons.append(_provider_button(p))
        else:
            for p in providers:
                buttons.append(_provider_button(p))

        rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
        rows.append([InlineKeyboardButton("✗ Cancel", callback_data="mx")])
        return InlineKeyboardMarkup(rows)

    def _build_model_keyboard(self, models: list, page: int) -> tuple:
        """Build paginated model buttons. Returns (keyboard, page_info_text)."""
        page_size = self._MODEL_PAGE_SIZE
        total = len(models)
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = max(0, min(page, total_pages - 1))

        start = page * page_size
        end = min(start + page_size, total)
        page_models = models[start:end]

        buttons: list = []
        for i, model_id in enumerate(page_models):
            abs_idx = start + i
            short = model_id.split("/")[-1] if "/" in model_id else model_id
            if len(short) > 38:
                short = short[:35] + "..."
            buttons.append(
                InlineKeyboardButton(short, callback_data=f"mm:{abs_idx}")
            )

        rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]

        # Pagination row (if needed)
        if total_pages > 1:
            nav: list = []
            if page > 0:
                nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"mg:{page - 1}"))
            nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="mx:noop"))
            if page < total_pages - 1:
                nav.append(InlineKeyboardButton("Next ▶", callback_data=f"mg:{page + 1}"))
            rows.append(nav)

        rows.append([
            InlineKeyboardButton("◀ Back", callback_data="mb"),
            InlineKeyboardButton("✗ Cancel", callback_data="mx"),
        ])

        page_info = f" ({start + 1}–{end} of {total})" if total_pages > 1 else ""
        return InlineKeyboardMarkup(rows), page_info

    @staticmethod
    def _reasoning_label(effort: Optional[str]) -> str:
        return {
            "low": "Low",
            "medium": "Medium",
            "high": "High",
            "xhigh": "Extra High",
            "max": "Ultra",
        }.get(str(effort or "").lower(), "Automatic")

    async def _show_model_reasoning_step(self, query, state: dict, model_id: str) -> None:
        from gateway.telegram_model_quick_picks import reasoning_levels_for_model

        provider_slug = state.get("selected_provider", "")
        # GitHub capability discovery may consult its authenticated live
        # catalog; keep that network-bound lookup off Telegram's event loop.
        options = await asyncio.to_thread(
            reasoning_levels_for_model, provider_slug, model_id
        )
        state["selected_model"] = model_id
        state["reasoning_options"] = options

        if not options:
            state["selected_reasoning"] = None
            await self._show_model_review(query, state)
            return

        rows = []
        buttons = [
            InlineKeyboardButton(label, callback_data=f"mr:{idx}")
            for idx, (label, _effort) in enumerate(options)
        ]
        rows.extend(buttons[i : i + 2] for i in range(0, len(buttons), 2))
        rows.append([
            InlineKeyboardButton("◀ Back", callback_data="mrm"),
            InlineKeyboardButton("✗ Cancel", callback_data="mx"),
        ])
        await query.edit_message_text(
            text=self.format_message(
                f"⚙ *Model Configuration*\n\n"
                f"Model: `{model_id}`\n\n"
                "Select reasoning effort:"
            ),
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup(rows),
        )

    async def _show_model_review(self, query, state: dict) -> None:
        model_id = state.get("selected_model", "")
        provider_name = state.get("selected_provider_name") or state.get("selected_provider", "")
        effort = state.get("selected_reasoning")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✓ Apply", callback_data="ma")],
            [
                InlineKeyboardButton(
                    "◀ Back",
                    callback_data="mrr" if state.get("reasoning_options") else "mrm",
                ),
                InlineKeyboardButton("✗ Cancel", callback_data="mx"),
            ],
        ])
        await query.edit_message_text(
            text=self.format_message(
                f"⚙ *Review model switch*\n\n"
                f"Provider: *{provider_name}*\n"
                f"Model: `{model_id}`\n"
                f"Reasoning: *{self._reasoning_label(effort)}*\n\n"
                "The current conversation and project will be preserved."
            ),
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=keyboard,
        )

    async def _apply_model_picker_selection(self, query, state: dict, chat_id: str) -> None:
        callback = state.get("on_model_selected")
        if not callback:
            await query.answer(text="Picker expired.")
            return

        switch_failed = False
        try:
            result_text = await callback(
                chat_id,
                state.get("selected_model", ""),
                state.get("selected_provider", ""),
                state.get("selected_reasoning"),
            )
        except Exception as exc:
            logger.error("Model picker switch failed: %s", exc)
            result_text = f"Error switching model: {exc}"
            switch_failed = True

        try:
            await query.edit_message_text(
                text=self.format_message(result_text),
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=None,
            )
        except Exception:
            try:
                await query.edit_message_text(text=result_text, parse_mode=None, reply_markup=None)
            except Exception:
                pass
        await query.answer(text="Switch failed." if switch_failed else "Model switched!")
        self._model_picker_state.pop(chat_id, None)

    async def _handle_model_picker_callback(
        self, query, data: str, chat_id: str
    ) -> None:
        """Handle provider → model → reasoning → review picker callbacks."""
        state = self._model_picker_state.get(chat_id)
        if not state:
            await query.answer(text="Picker expired — use /model again.")
            return

        try:
            from hermes_cli.providers import get_label
        except ImportError:
            def get_label(slug):
                return slug

        if data.startswith("mp:"):
            # --- Provider selected: show model buttons (page 0) ---
            provider_slug = data[3:]
            provider = next(
                (p for p in state["providers"] if p["slug"] == provider_slug),
                None,
            )
            if not provider:
                await query.answer(text="Provider not found.")
                return

            models = provider.get("models", [])
            state["selected_provider"] = provider_slug
            state["selected_provider_name"] = provider.get("name", provider_slug)
            state["model_list"] = models
            state["model_page"] = 0

            keyboard, page_info = self._build_model_keyboard(models, 0)

            pname = provider.get("name", provider_slug)
            total = provider.get("total_models", len(models))
            shown = len(models)
            extra = f"\n_{total - shown} more available — type `/model <name>` directly_" if total > shown else ""

            await query.edit_message_text(
                text=self.format_message(
                    (
                        f"⚙ *Model Configuration*\n\n"
                        f"Provider: *{pname}*{page_info}\n"
                        f"Select a model:{extra}"
                    )
                ),
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=keyboard,
            )
            await query.answer()

        elif data.startswith("mg:"):
            # --- Page navigation ---
            try:
                page = int(data[3:])
            except ValueError:
                await query.answer(text="Invalid page.")
                return

            models = state.get("model_list", [])
            state["model_page"] = page

            keyboard, page_info = self._build_model_keyboard(models, page)

            pname = state.get("selected_provider_name", "")
            provider_slug = state.get("selected_provider", "")
            provider = next(
                (p for p in state["providers"] if p["slug"] == provider_slug),
                None,
            )
            total = provider.get("total_models", len(models)) if provider else len(models)
            shown = len(models)
            extra = f"\n_{total - shown} more available — type `/model <name>` directly_" if total > shown else ""

            await query.edit_message_text(
                text=self.format_message(
                    (
                        f"⚙ *Model Configuration*\n\n"
                        f"Provider: *{pname}*{page_info}\n"
                        f"Select a model:{extra}"
                    )
                ),
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=keyboard,
            )
            await query.answer()

        elif data.startswith("mc:"):
            # --- Expensive model confirmed: continue to reasoning ---
            try:
                idx = int(data[3:])
            except ValueError:
                await query.answer(text="Invalid selection.")
                return

            model_list = state.get("model_list", [])
            if idx < 0 or idx >= len(model_list):
                await query.answer(text="Invalid model index.")
                return

            model_id = model_list[idx]
            await self._show_model_reasoning_step(query, state, model_id)
            await query.answer()

        elif data.startswith("mm:"):
            # --- Model selected: validate cost, then choose reasoning ---
            try:
                idx = int(data[3:])
            except ValueError:
                await query.answer(text="Invalid selection.")
                return

            model_list = state.get("model_list", [])
            if idx < 0 or idx >= len(model_list):
                await query.answer(text="Invalid model index.")
                return

            model_id = model_list[idx]
            provider_slug = state.get("selected_provider", "")

            try:
                from hermes_cli.model_cost_guard import expensive_model_warning

                # Pricing lookup can hit models.dev / a /models endpoint on a
                # cache miss — keep it off the event loop.
                warning = await asyncio.to_thread(
                    expensive_model_warning,
                    model_id,
                    provider=provider_slug,
                )
            except Exception:
                warning = None
            if warning is not None:
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("Switch anyway", callback_data=f"mc:{idx}")],
                    [
                        InlineKeyboardButton("◀ Back", callback_data="mb"),
                        InlineKeyboardButton("✗ Cancel", callback_data="mx"),
                    ],
                ])
                await query.edit_message_text(
                    text=self.format_message(
                        f"⚠ *Expensive Model Warning*\n\n{warning.message}"
                    ),
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=keyboard,
                )
                await query.answer(text="Confirm expensive model")
                return

            await self._show_model_reasoning_step(query, state, model_id)
            await query.answer()

        elif data.startswith("mr:"):
            try:
                idx = int(data[3:])
                _label, effort = state.get("reasoning_options", [])[idx]
            except (ValueError, IndexError):
                await query.answer(text="Invalid reasoning level.")
                return
            state["selected_reasoning"] = effort
            await self._show_model_review(query, state)
            await query.answer()

        elif data == "mrr":
            await self._show_model_reasoning_step(
                query, state, state.get("selected_model", "")
            )
            await query.answer()

        elif data == "mrm":
            models = state.get("model_list", [])
            page = state.get("model_page", 0)
            keyboard, page_info = self._build_model_keyboard(models, page)
            await query.edit_message_text(
                text=self.format_message(
                    f"⚙ *Model Configuration*\n\n"
                    f"Provider: *{state.get('selected_provider_name', '')}*{page_info}\n"
                    "Select a model:"
                ),
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=keyboard,
            )
            await query.answer()

        elif data == "ma":
            await self._apply_model_picker_selection(query, state, chat_id)

        elif data.startswith("mpg:"):
            # --- Provider group selected: show member providers ---
            group_id = data[4:]
            try:
                from hermes_cli.models import PROVIDER_GROUPS
                _label, _desc, member_slugs = PROVIDER_GROUPS.get(group_id, ("", "", []))
            except Exception:
                _label, member_slugs = "", []

            by_slug = {p["slug"]: p for p in state["providers"]}
            members = [by_slug[m] for m in member_slugs if m in by_slug]
            if not members:
                await query.answer(text="Group not found.")
                return

            buttons = []
            for p in members:
                count = p.get("total_models", len(p.get("models", [])))
                label = f"{p['name']} ({count})"
                if p.get("is_current"):
                    label = f"✓ {label}"
                buttons.append(
                    InlineKeyboardButton(label, callback_data=f"mp:{p['slug']}")
                )
            rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
            rows.append([
                InlineKeyboardButton("◀ Back", callback_data="mb"),
                InlineKeyboardButton("✗ Cancel", callback_data="mx"),
            ])
            keyboard = InlineKeyboardMarkup(rows)

            await query.edit_message_text(
                text=self.format_message(
                    (
                        f"⚙ *Model Configuration*\n\n"
                        f"Provider family: *{_label or group_id}*\n\n"
                        f"Select a provider:"
                    )
                ),
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=keyboard,
            )
            await query.answer()

        elif data == "mb":
            # --- Back to provider list (folds groups) ---
            keyboard = self._build_provider_keyboard(state["providers"])

            try:
                provider_label = get_label(state["current_provider"])
            except Exception:
                provider_label = state["current_provider"]

            await query.edit_message_text(
                text=self.format_message(
                    (
                        f"⚙ *Model Configuration*\n\n"
                        f"Current model: `{state['current_model'] or 'unknown'}`\n"
                        f"Provider: {provider_label}\n\n"
                        f"Select a provider:"
                    )
                ),
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=keyboard,
            )
            await query.answer()

        elif data == "mx":
            # --- Cancel ---
            self._model_picker_state.pop(chat_id, None)
            await query.edit_message_text(
                text="Model selection cancelled.",
                reply_markup=None,
            )
            await query.answer()

        else:
            # Catch-all (e.g. page counter button "mx:noop")
            await query.answer()
