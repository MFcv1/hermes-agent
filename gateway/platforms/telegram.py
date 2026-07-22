"""Compatibility import for the plugin-owned Telegram adapter.

Telegram capability lives in ``plugins.platforms.telegram``. Keeping this
small module preserves imports used by older integrations without maintaining
a second adapter or a second command workflow in the gateway core.
"""

import html as _html

from plugins.platforms.telegram.adapter import (
    TelegramAdapter as _PluginTelegramAdapter,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ParseMode,
    check_telegram_requirements,
)


class TelegramAdapter(_PluginTelegramAdapter):
    """Import-compatible facade over the plugin-owned implementation."""

    async def _send_dashboard_shortcut(self, msg) -> None:
        from gateway.dashboard_links import hermes_dashboard_url

        url = hermes_dashboard_url("/sessions")
        if not url:
            await msg.reply_text(
                "Dashboard Hermes privé.\n\n"
                "Aucune URL web publique n'est configurée. Si ton tunnel SSH "
                "est ouvert sur ce Mac, ouvre :\n"
                "http://127.0.0.1:9120/sessions\n\n"
                "Configure dashboard.public_url pour activer le bouton web.",
                **self._link_preview_kwargs(),
            )
            return

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🖥 Ouvrir le Dashboard web", url=url)
        ]])
        html_text = (
            "<b>Dashboard Hermes — version web complète</b>\n\n"
            "Interface optimisée pour un navigateur sur ordinateur. "
            "La conversation et le projet courant restent les mêmes.\n\n"
            f"Lien copiable :\n<code>{_html.escape(url)}</code>"
        )
        plain_text = (
            "Dashboard Hermes — version web complète\n\n"
            "Interface optimisée pour un navigateur sur ordinateur. "
            "La conversation et le projet courant restent les mêmes.\n\n"
            f"Lien copiable :\n{url}"
        )
        try:
            await msg.reply_text(
                html_text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                **self._link_preview_kwargs(),
            )
        except Exception as exc:
            if self._is_bad_request_error(exc):
                await msg.reply_text(plain_text, reply_markup=keyboard)
            else:
                raise


__all__ = [
    "TelegramAdapter",
    "InlineKeyboardButton",
    "InlineKeyboardMarkup",
    "ParseMode",
    "check_telegram_requirements",
]
