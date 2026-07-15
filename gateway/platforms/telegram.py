"""
Telegram platform adapter.

Uses python-telegram-bot library for:
- Receiving messages from users/groups
- Sending responses back
- Handling media and commands
"""

import asyncio
import dataclasses
import inspect
import json
import logging
import os
import tempfile
import html as _html
import re
import shlex
import subprocess
import time
from urllib import request as _urlrequest
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Any

logger = logging.getLogger(__name__)

REPO_COCKPIT_MODES = {"ask_review", "pilote", "autopilot"}


def normalize_cockpit_mode(mode: str | None) -> str:
    """Return a supported Repo Cockpit mode, preserving the new Pilote flow."""
    clean = str(mode or "").strip().lower()
    return clean if clean in REPO_COCKPIT_MODES else "ask_review"

try:
    from telegram import (
        Update, Bot, Message, InlineKeyboardButton, InlineKeyboardMarkup,
        KeyboardButton, ReplyKeyboardMarkup,
    )
    try:
        from telegram import WebAppInfo
    except ImportError:
        WebAppInfo = None
    try:
        from telegram import LinkPreviewOptions
    except ImportError:
        LinkPreviewOptions = None
    from telegram.ext import (
        Application,
        CommandHandler,
        CallbackQueryHandler,
        MessageHandler as TelegramMessageHandler,
        ContextTypes,
        filters,
    )
    from telegram.constants import ParseMode, ChatType
    from telegram.request import HTTPXRequest
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    Update = Any
    Bot = Any
    Message = Any
    InlineKeyboardButton = Any
    InlineKeyboardMarkup = Any
    KeyboardButton = Any
    ReplyKeyboardMarkup = Any
    WebAppInfo = None
    LinkPreviewOptions = None
    Application = Any
    CommandHandler = Any
    CallbackQueryHandler = Any
    TelegramMessageHandler = Any
    HTTPXRequest = Any
    filters = None
    ParseMode = None
    ChatType = None

    # Mock ContextTypes so type annotations using ContextTypes.DEFAULT_TYPE
    # don't crash during class definition when the library isn't installed.
    class _MockContextTypes:
        DEFAULT_TYPE = Any
    ContextTypes = _MockContextTypes

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

from gateway.config import Platform, PlatformConfig
from gateway.platforms.telegram_models_config import TelegramModelsConfigMixin
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    ProcessingOutcome,
    SendResult,
    cache_image_from_bytes,
    cache_audio_from_bytes,
    cache_video_from_bytes,
    cache_document_from_bytes,
    resolve_proxy_url,
    SUPPORTED_VIDEO_TYPES,
    SUPPORTED_DOCUMENT_TYPES,
    SUPPORTED_IMAGE_DOCUMENT_TYPES,
    utf16_len,
)
from gateway.platforms.telegram_network import (
    TelegramFallbackTransport,
    discover_fallback_ips,
    parse_fallback_ip_env,
)
from gateway.human_heartbeat import progress_from_autonomy, render_progress_view
from gateway.repo_cockpit_client import RepoCockpitClient, cockpit_webapp_url
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
from gateway.telegram_transport_mixin import TelegramTransportMixin
from gateway.telegram_inbound_filter_mixin import TelegramInboundFilterMixin
from gateway.telegram_model_picker_mixin import TelegramModelPickerMixin
from gateway.telegram_conversations_mixin import TelegramConversationsMixin
from gateway.repo_cockpit_telegram_mixin import RepoCockpitTelegramMixin
from gateway.repo_cockpit_text import (
    audit_task_text,
    format_audit_blocked,
    format_audit_completed,
    format_audit_started,
    mode_note,
    mode_title,
    new_chat_text,
    pilot_intent_title,
    pilot_waiting_prompt_text,
    project_created_text,
    repo_selected_text,
    tasks_list_text,
)
from utils import atomic_replace

_TELEGRAM_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_TELEGRAM_IMAGE_MIME_TO_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_TELEGRAM_IMAGE_EXT_TO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


MAX_COMMANDS_PER_SCOPE = 30


def check_telegram_requirements() -> bool:
    """Check if Telegram dependencies are available.

    If python-telegram-bot is missing, attempts to lazy-install it via
    ``tools.lazy_deps.ensure("platform.telegram")``. After a successful
    install, re-imports the SDK and flips ``TELEGRAM_AVAILABLE`` to True
    so the adapter's class-level type aliases get rebound.
    """
    global TELEGRAM_AVAILABLE, Update, Bot, Message, InlineKeyboardButton
    global InlineKeyboardMarkup, WebAppInfo, LinkPreviewOptions, Application
    global CommandHandler, CallbackQueryHandler, TelegramMessageHandler
    global ContextTypes, filters, ParseMode, ChatType, HTTPXRequest
    if TELEGRAM_AVAILABLE:
        return True
    try:
        from tools.lazy_deps import ensure as _lazy_ensure
        _lazy_ensure("platform.telegram", prompt=False)
    except Exception:
        return False
    try:
        from telegram import Update as _Update, Bot as _Bot, Message as _Message
        from telegram import InlineKeyboardButton as _IKB, InlineKeyboardMarkup as _IKM
        try:
            from telegram import WebAppInfo as _WAI
        except ImportError:
            _WAI = None
        try:
            from telegram import LinkPreviewOptions as _LPO
        except ImportError:
            _LPO = None
        from telegram.ext import (
            Application as _App, CommandHandler as _CH,
            CallbackQueryHandler as _CQH,
            MessageHandler as _MH,
            ContextTypes as _CT, filters as _filters,
        )
        from telegram.constants import ParseMode as _PM, ChatType as _CtT
        from telegram.request import HTTPXRequest as _HR
    except ImportError:
        return False
    Update = _Update
    Bot = _Bot
    Message = _Message
    InlineKeyboardButton = _IKB
    InlineKeyboardMarkup = _IKM
    WebAppInfo = _WAI
    LinkPreviewOptions = _LPO
    Application = _App
    CommandHandler = _CH
    CallbackQueryHandler = _CQH
    TelegramMessageHandler = _MH
    ContextTypes = _CT
    filters = _filters
    ParseMode = _PM
    ChatType = _CtT
    HTTPXRequest = _HR
    TELEGRAM_AVAILABLE = True
    return True


from gateway.platforms.telegram_formatting import (
    _TABLE_SEPARATOR_RE,
    _escape_mdv2,
    _is_table_row,
    _render_table_block_for_telegram,
    _split_markdown_table_row,
    _strip_mdv2,
    _wrap_markdown_tables,
    format_telegram_markdown,
)


class TelegramAdapter(TelegramTransportMixin, TelegramInboundFilterMixin, TelegramModelPickerMixin, TelegramConversationsMixin, RepoCockpitTelegramMixin, TelegramModelsConfigMixin, BasePlatformAdapter):
    """
    Telegram bot adapter.

    Handles:
    - Receiving messages from users and groups
    - Sending responses with Telegram markdown
    - Forum topics (thread_id support)
    - Media messages
    """

    # Telegram message limits
    MAX_MESSAGE_LENGTH = 4096
    supports_code_blocks = True  # Telegram MarkdownV2 renders fenced code blocks
    # Bot API 10.1 Rich Messages cap the raw markdown/html text at 32,768
    # UTF-8 characters. Content above this is sent via the legacy chunking path.
    RICH_MESSAGE_MAX_CHARS = 32768
    # Backwards-compatible alias for tests/external callers that referenced the
    # initial implementation name. The API limit is character-based, not bytes.
    RICH_MESSAGE_MAX_BYTES = RICH_MESSAGE_MAX_CHARS
    # Threshold for detecting Telegram client-side message splits.
    # When a chunk is near this limit, a continuation is almost certain.
    _SPLIT_THRESHOLD = 4000
    MEDIA_GROUP_WAIT_SECONDS = 0.8
    _GENERAL_TOPIC_THREAD_ID = "1"

    # Telegram's edit_message applies MarkdownV2 formatting only on the
    # finalize=True path.  Without this flag, stream_consumer._send_or_edit
    # short-circuits when the raw text is unchanged between the last streamed
    # edit and the final edit, skipping the plain-text → MarkdownV2 conversion.
    # Fixes #25710.
    REQUIRES_EDIT_FINALIZE: bool = True

    # Adaptive text-batch ingress: short messages need a tighter delay so the
    # first token reaches the agent fast.  Numbers tuned for "feels instant":
    # ≤320 codepoints (one short paragraph) settles in ~180ms; ≤1024
    # (a normal paragraph) in ~240ms; longer waits the configured cap.
    # Always clamped to ``_text_batch_delay_seconds`` so an operator can lower
    # the cap further via env var.
    _TEXT_BATCH_FAST_LEN = 320
    _TEXT_BATCH_FAST_DELAY_S = 0.18
    _TEXT_BATCH_SHORT_LEN = 1024
    _TEXT_BATCH_SHORT_DELAY_S = 0.24

    @staticmethod
    def _env_float_clamped(
        name: str,
        default: float,
        *,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
    ) -> float:
        """Read a float env var, reject non-finite values, and clamp to bounds.

        Guarantees the returned value is a finite number usable directly in
        ``asyncio.sleep()`` and similar APIs that reject NaN / Inf.
        """
        import math

        raw = os.getenv(name)
        try:
            value = float(raw) if raw is not None else float(default)
        except (TypeError, ValueError):
            value = float(default)
        if not math.isfinite(value):
            value = float(default)
        if min_value is not None:
            value = max(value, min_value)
        if max_value is not None:
            value = min(value, max_value)
        return value

    @property
    def message_len_fn(self):
        """Telegram measures message length in UTF-16 code units."""
        return utf16_len

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.TELEGRAM)
        self._app: Optional[Application] = None
        self._bot: Optional[Bot] = None
        self._repo_cockpit_client = RepoCockpitClient()
        self._webhook_mode: bool = False
        self._mention_patterns = self._compile_mention_patterns()
        self._reply_to_mode: str = getattr(config, 'reply_to_mode', 'first') or 'first'
        self._disable_link_previews: bool = self._coerce_bool_extra("disable_link_previews", False)
        # Bot API 10.1 Rich Messages: render constructs the legacy MarkdownV2
        # path degrades (tables → bullet lists, task lists, <details>, block
        # math) via sendRichMessage / editMessageText's rich_message param using
        # the raw agent markdown. Enabled by default; users can opt out for
        # clients that accept but render rich messages poorly via
        # platforms.telegram.extra.rich_messages: false.
        self._rich_messages_enabled: bool = self._coerce_bool_extra("rich_messages", True)
        # Latched off after a capability failure on sendRichMessage /
        # sendRichMessageDraft (e.g. older python-telegram-bot without the
        # endpoint) so later sends skip the doomed rich attempt entirely.
        self._rich_send_disabled: bool = False
        self._rich_draft_disabled: bool = False
        # Buffer rapid/album photo updates so Telegram image bursts are handled
        # as a single MessageEvent instead of self-interrupting multiple turns.
        self._media_batch_delay_seconds = float(os.getenv("HERMES_TELEGRAM_MEDIA_BATCH_DELAY_SECONDS", "0.8"))
        self._pending_photo_batches: Dict[str, MessageEvent] = {}
        self._pending_photo_batch_tasks: Dict[str, asyncio.Task] = {}
        self._media_group_events: Dict[str, MessageEvent] = {}
        self._media_group_tasks: Dict[str, asyncio.Task] = {}
        # Buffer rapid text messages so Telegram client-side splits of long
        # messages are aggregated into a single MessageEvent.  Lower defaults
        # (0.3s / 1.0s instead of 0.6s / 2.0s) let short replies stream
        # without a noticeable wait — combined with the adaptive fast-path
        # in ``_calc_text_batch_delay`` below, ≤320-codepoint replies settle
        # in ~180ms.  All bounds are conservative for Telegram's
        # ~1 edit/s flood envelope.
        self._text_batch_delay_seconds = self._env_float_clamped(
            "HERMES_TELEGRAM_TEXT_BATCH_DELAY_SECONDS",
            0.3,
            min_value=0.08,
            max_value=2.0,
        )
        self._text_batch_split_delay_seconds = self._env_float_clamped(
            "HERMES_TELEGRAM_TEXT_BATCH_SPLIT_DELAY_SECONDS",
            1.0,
            min_value=self._text_batch_delay_seconds,
            max_value=4.0,
        )
        self._pending_text_batches: Dict[str, MessageEvent] = {}
        self._pending_text_batch_tasks: Dict[str, asyncio.Task] = {}
        self._polling_error_task: Optional[asyncio.Task] = None
        self._polling_conflict_count: int = 0
        self._polling_network_error_count: int = 0
        self._polling_error_callback_ref = None
        # After sustained reconnect storms the PTB httpx pool can return
        # SendResult(success=True) for sends that never actually transmit.
        # _handle_polling_network_error sets this; _verify_polling_after_reconnect
        # clears it once getMe() confirms the Bot client is healthy.
        # While True, send() short-circuits to a failure so callers
        # (cron live-adapter branch) fall through to standalone delivery.
        self._send_path_degraded: bool = False
        # DM Topics: map of topic_name -> message_thread_id (populated at startup)
        self._dm_topics: Dict[str, int] = {}
        # Track forum chats where we've already registered bot commands
        self._forum_command_registered: set[int] = set()
        # Lock per la registrazione sicura dei comandi nei forum supergroup
        self._forum_lock = asyncio.Lock()
        # DM Topics config from extra.dm_topics
        self._dm_topics_config: List[Dict[str, Any]] = self.config.extra.get("dm_topics", [])
        # Precomputed chat_ids that have DM topics configured (for O(1) root-DM ignore check)
        self._dm_topic_chat_ids: Set[str] = {
            str(e["chat_id"]) for e in self._dm_topics_config if "chat_id" in e
        }
        # Document size cap. Telegram's public Bot API caps getFile at 20MB; a
        # locally-hosted telegram-bot-api server (configured via extra.base_url)
        # raises that to 2GB, so the presence of base_url is the opt-in.
        self._max_doc_bytes: int = (
            2 * 1024 * 1024 * 1024
            if self.config.extra.get("base_url")
            else 20 * 1024 * 1024
        )
        # Interactive model picker state per chat
        self._model_picker_state: Dict[str, dict] = {}
        # Repo Cockpit new-chat repo choices per Telegram user. Callback data is
        # capped by Telegram, so buttons carry short indexes into this map.
        self._repo_new_chat_choices: Dict[str, dict] = {}
        # Pilot Intake guided workflow state (Telegram user id -> state).
        # This keeps /new -> buttons -> natural prompt/replies command-free.
        self._pilot_intake_states: Dict[str, dict] = {}
        # Libre V2 state (Telegram user id -> soft orchestration state). Libre
        # keeps durable memory and normal chat alive while closing transient
        # repo/wizard state from the active Repo Cockpit flow.
        self._libre_chat_states: Dict[str, dict] = {}
        self._libre_watch_enabled: bool = self._coerce_bool_extra("libre_watch_enabled", False)
        try:
            self._libre_watch_interval_seconds = max(30.0, float(self.config.extra.get("libre_watch_interval_seconds", 300)))
        except Exception:
            self._libre_watch_interval_seconds = 300.0
        try:
            self._libre_watch_initial_delay_seconds = max(0.0, float(self.config.extra.get("libre_watch_initial_delay_seconds", 15)))
        except Exception:
            self._libre_watch_initial_delay_seconds = 15.0
        self._libre_watch_task: Optional[asyncio.Task] = None
        self._libre_watch_last_signature: str = ""
        self._ensure_models_config_state()
        self._cockpit_background_tasks: set[asyncio.Task] = set()
        # Approval button state: message_id → session_key
        self._approval_state: Dict[int, str] = {}
        # Slash-confirm button state: confirm_id → session_key (for /reload-mcp
        # and any other slash-confirm prompts; see GatewayRunner._request_slash_confirm).
        self._slash_confirm_state: Dict[str, str] = {}
        # Clarify button state: clarify_id → session_key (for the clarify tool's
        # multiple-choice prompts; see GatewayRunner clarify_callback wiring).
        self._clarify_state: Dict[str, str] = {}
        # Notification mode for message sends.
        # "important" — only final responses, approvals, and slash confirmations
        #               trigger notifications; tool progress, streaming, status
        #               messages are delivered silently via disable_notification.
        #               This is the default — Telegram users found per-tool-call
        #               push notifications too noisy.
        # "all"       — every message triggers a push notification (legacy
        #               behavior; opt-in via display.platforms.telegram.notifications).
        self._notifications_mode: str = "important"
        # send_or_update_status() bookkeeping: {(chat_id, status_key) -> bot message_id}
        # Tracks status bubbles owned by this adapter so subsequent calls with the
        # same key edit the same message instead of appending new ones (#30045).
        self._status_message_ids: Dict[tuple, str] = {}


    def format_message(self, content: str) -> str:
        """Convert standard markdown to Telegram MarkdownV2 format."""
        return format_telegram_markdown(content)

    # ── Group mention gating ──────────────────────────────────────────────


    async def _ensure_forum_commands(self, message) -> None:
        """Lazy-register bot commands for forum supergroups.

        Forum topics don't inherit AllGroupChats scope — Telegram resolves
        via BotCommandScopeChat(chat_id).  Register on first message so the
        command menu works in topic views.
        """
        async with self._forum_lock:
            try:
                chat = getattr(message, "chat", None)
                if not chat or not getattr(chat, "is_forum", False):
                    return
                chat_id = int(chat.id)
                if chat_id in self._forum_command_registered:
                    return
                from telegram import BotCommand, BotCommandScopeChat
                from hermes_cli.commands import telegram_menu_commands
                menu_commands, _ = telegram_menu_commands(max_commands=MAX_COMMANDS_PER_SCOPE)
                bot_commands = [BotCommand(name, desc) for name, desc in menu_commands]
                await self._bot.set_my_commands(bot_commands, scope=BotCommandScopeChat(chat_id=chat_id))
                self._forum_command_registered.add(chat_id)
                logger.info("[%s] Lazy-registered %d commands for forum chat %s", self.name, len(bot_commands), chat_id)
            except Exception as e:
                logger.warning("[%s] Forum command lazy-registration failed: %s", self.name, e)

    def _effective_update_message(self, update: Update) -> Optional[Message]:
        """Return the message-like payload for normal messages and channel posts.

        Telegram exposes channel broadcasts as ``update.channel_post`` rather
        than ``update.message``.  MessageHandler filters can still dispatch
        those updates, so handlers must use ``effective_message`` to avoid
        consuming channel posts without ever building a gateway event.
        """
        return getattr(update, "effective_message", None) or getattr(update, "message", None)

    async def _handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming text messages.

        Telegram clients split long messages into multiple updates.  Buffer
        rapid successive text messages from the same user/chat and aggregate
        them into a single MessageEvent before dispatching.
        """
        msg = self._effective_update_message(update)
        if not msg or not msg.text:
            return
        if not self._should_process_message(msg):
            if self._should_observe_unmentioned_group_message(msg):
                self._observe_unmentioned_group_message(msg, MessageType.TEXT, update_id=update.update_id)
            return
        await self._ensure_forum_commands(update.message)

        event = self._build_message_event(msg, MessageType.TEXT, update_id=update.update_id)
        event.text = self._clean_bot_trigger_text(event.text)
        event._telegram_message = msg  # type: ignore[attr-defined]
        await self._cache_replied_media(msg, event)
        event = self._apply_telegram_group_observe_attribution(event)
        self._enqueue_text_event(event)

    async def _handle_web_app_data(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Resume a selected Hermes session from the Telegram Mini App."""
        msg = self._effective_update_message(update)
        if not msg or not self._should_process_message(msg):
            return
        raw_data = getattr(getattr(msg, "web_app_data", None), "data", "")
        try:
            payload = json.loads(raw_data)
        except (TypeError, ValueError):
            await msg.reply_text("Action Mini App illisible.")
            return
        if not isinstance(payload, dict) or payload.get("action") != "session.resume":
            await msg.reply_text("Action Mini App inconnue.")
            return
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            await msg.reply_text("Session Hermes introuvable.")
            return
        event = self._build_message_event(msg, MessageType.TEXT, update_id=update.update_id)
        event.text = f"/resume {shlex.quote(session_id)}"
        event._telegram_message = msg  # type: ignore[attr-defined]
        event = self._apply_telegram_group_observe_attribution(event)
        await self.handle_message(event)




    async def _send_serveurstatut(self, msg: Message) -> None:
        """Send an operational VPS / Hermes / Repo Cockpit health report."""

        def run_check(cmd: list[str], timeout: int = 8) -> tuple[bool, str]:
            try:
                proc = subprocess.run(
                    cmd,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                )
                out = (proc.stdout or "").strip()
                return proc.returncode == 0, out
            except subprocess.TimeoutExpired:
                return False, "timeout"
            except Exception as exc:
                return False, str(exc)

        checks: list[tuple[str, bool, str]] = []

        ok, out = run_check(["bash", "-lc", "XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user is-active hermes-gateway"], 5)
        checks.append(("Hermes Gateway", ok and out == "active", out))

        ok, out = run_check(["bash", "-lc", "XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user is-active hermes-repo-cockpit"], 5)
        checks.append(("Repo Cockpit service", ok and out == "active", out))

        ok, out = run_check(["python3", "-c", "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=6).read().decode())"], 8)
        checks.append(("Cockpit local /health", ok and '"ok":true' in out.replace(' ', ''), out[:160]))

        ok, out = run_check(["python3", "-c", "import urllib.request; print(urllib.request.urlopen('https://cockpit.134.122.73.242.sslip.io/health', timeout=8).read().decode())"], 10)
        checks.append(("Cockpit HTTPS /health", ok and '"ok":true' in out.replace(' ', ''), out[:160]))

        ok, out = run_check(["gh", "auth", "status", "--hostname", "github.com"], 10)
        gh_ok = ok and "Logged in to github.com" in out and "MFcv1" in out
        checks.append(("GitHub CLI VPS", gh_ok, "MFcv1 connecté" if gh_ok else out[:220]))

        ok, out = run_check(["python3", "-c", "import json, urllib.request; d=json.loads(urllib.request.urlopen('http://127.0.0.1:8765/api/capabilities', timeout=15).read()); q=d.get('quota') or {}; main=(q.get('main') or {}).get('primary_remaining_percent'); spark=((q.get('additional') or {}).get('GPT-5.3-Codex-Spark') or {}).get('primary_remaining_percent'); allowed=(q.get('main') or {}).get('allowed'); print(f\"main_remaining={main}% spark_remaining={spark}% main_allowed={allowed} source={q.get('source')}\")"], 18)
        checks.append(("Codex quota + Spark guard", ok and "main_allowed=True" in out, out[:220]))

        ok, out = run_check(["bash", "-lc", "df -h / | awk 'NR==2{print $5 \" used, \" $4 \" free\"}'"], 5)
        checks.append(("Disque VPS", ok, out))

        ok, out = run_check(["bash", "-lc", "uptime | sed 's/^ *//'"], 5)
        checks.append(("Uptime/load", ok, out))

        all_ok = all(item_ok for _, item_ok, _ in checks[:5])
        title = "✅ Serveur opérationnel" if all_ok else "⚠️ Serveur partiellement OK"
        html_lines = [f"<b>{_html.escape(title, quote=False)}</b>", ""]
        plain_lines = [title, ""]
        for name, item_ok, detail in checks:
            icon = "✅" if item_ok else "❌"
            clean = (detail or "").replace("\n", " | ")
            if len(clean) > 260:
                clean = clean[:257] + "..."
            shown = clean or ("OK" if item_ok else "KO")
            html_lines.append(
                f"{icon} <b>{_html.escape(name, quote=False)}</b>: "
                f"<code>{_html.escape(shown, quote=False)}</code>"
            )
            plain_lines.append(f"{icon} {name}: {shown}")

        html_lines.append("")
        html_lines.append("Raccourci cockpit : <b>/repo</b>")
        plain_lines.append("")
        plain_lines.append("Raccourci cockpit : /repo")

        rich_rows = []
        for name, item_ok, detail in checks:
            clean = (detail or "").replace("\n", " | ")
            if len(clean) > 220:
                clean = clean[:217] + "..."
            shown = clean or ("OK" if item_ok else "KO")
            rich_rows.append(
                "<tr>"
                f"<td>{'✅' if item_ok else '❌'} {_html.escape(name, quote=False)}</td>"
                f"<td><code>{_html.escape(shown, quote=False)}</code></td>"
                "</tr>"
            )
        rich_html = (
            f"<h1>{_html.escape(title, quote=False)}</h1>"
            "<p><b>Repo Cockpit / Hermes VPS</b> — rapport opérationnel natif Telegram.</p>"
            "<table bordered striped>"
            "<tr><th>Check</th><th>Résultat</th></tr>"
            + "".join(rich_rows)
            + "</table>"
            "<ul>"
            "<li><input type=\"checkbox\" checked> Menu Telegram conservé</li>"
            "<li><input type=\"checkbox\" checked> Raccourci cockpit via <code>/repo</code></li>"
            "<li><input type=\"checkbox\" checked> Rich formatting actif si ce message affiche un tableau</li>"
            "</ul>"
            "<details><summary>À retenir</summary>"
            "<p>Si le rendu riche échoue côté Telegram, le bot retombe automatiquement sur un message HTML classique.</p>"
            "</details>"
            "<footer>Raccourci cockpit : /repo</footer>"
        )
        if self._bot and hasattr(self._bot, "do_api_request"):
            try:
                rich_payload = {
                    "chat_id": int(getattr(msg, "chat_id")),
                    "rich_message": {"html": rich_html},
                }
                message_id = getattr(msg, "message_id", None)
                if message_id is not None:
                    rich_payload["reply_parameters"] = {"message_id": message_id}
                rich_result = await self._bot.do_api_request("sendRichMessage", api_kwargs=rich_payload)
                try:
                    result_payload = rich_result.get("result") if isinstance(rich_result, dict) else None
                    message_id = None
                    if isinstance(result_payload, dict):
                        message_id = result_payload.get("message_id")
                    elif isinstance(rich_result, dict):
                        message_id = rich_result.get("message_id")
                    else:
                        message_id = getattr(rich_result, "message_id", None)
                    if message_id is not None:
                        sent_obj = type("_Sent", (), {})()
                        sent_obj.chat_id = getattr(msg, "chat_id", None)
                        sent_obj.message_id = message_id
                        sent_obj.chat = getattr(msg, "chat", None)
                        await self._log_cockpit_message(msg, direction="outgoing", role="status", sent=sent_obj)
                except Exception:
                    pass
                return
            except Exception as exc:
                if self._is_rich_fallback_error(exc):
                    logger.debug("[%s] /serveurstatut rich render rejected: %s", self.name, exc)
                else:
                    logger.warning("[%s] /serveurstatut rich render failed; falling back to HTML: %s", self.name, exc)

        try:
            sent = await msg.reply_text(
                "\n".join(html_lines),
                parse_mode=ParseMode.HTML,
                **self._link_preview_kwargs(),
            )
            await self._log_cockpit_message(msg, direction="outgoing", role="status", sent=sent)
        except Exception as exc:
            if self._is_bad_request_error(exc):
                sent = await msg.reply_text("\n".join(plain_lines), **self._link_preview_kwargs())
                await self._log_cockpit_message(msg, direction="outgoing", role="status", sent=sent)
            else:
                raise


    async def _send_repo_cockpit_shortcut(self, msg: Message) -> None:
        """Send the Repo Cockpit WebApp shortcut without replacing Telegram's command menu."""
        cockpit_url = self._repo_cockpit_url("/")
        button_kwargs = (
            {"web_app": WebAppInfo(url=cockpit_url)}
            if WebAppInfo is not None
            else {"url": cockpit_url}
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🌐 Ouvrir Repo Cockpit", **button_kwargs)
        ]])
        html_text = (
            "<b>🌐 Repo Cockpit</b>\n\n"
            "Choisis un repo, puis le cockpit revient automatiquement dans ce chat."
        )
        plain_text = (
            "🌐 Repo Cockpit\n\n"
            "Choisis un repo, puis le cockpit revient automatiquement dans ce chat."
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
                await msg.reply_text(
                    plain_text,
                    reply_markup=keyboard,
                    **self._link_preview_kwargs(),
                )
            else:
                raise

    async def _send_hermes_mini_app_shortcut(self, msg: Message) -> None:
        """Open the dashboard Sessions view through a Telegram WebApp button."""
        from gateway.dashboard_links import hermes_mini_app_url

        dashboard_url = hermes_mini_app_url("/sessions")
        if not dashboard_url:
            await msg.reply_text(
                "Mini App indisponible : configure d'abord `dashboard.public_url` avec l'URL HTTPS privée du dashboard."
            )
            return
        if WebAppInfo is None:
            await msg.reply_text(f"Ouvre Hermes Sessions :\n{dashboard_url}")
            return
        # Telegram Desktop delivers WebApp.sendData only for a reply-keyboard
        # WebApp button; inline buttons merely open the page.
        keyboard = ReplyKeyboardMarkup(
            [[KeyboardButton("Ouvrir Hermes Mini App", web_app=WebAppInfo(url=dashboard_url))]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await msg.reply_text(
            "Hermes Mini App\n\nConsulte une conversation, puis utilise « Reprendre dans Telegram ».",
            reply_markup=keyboard,
            **self._link_preview_kwargs(),
        )

    async def _send_richdemo(self, msg: Message, template: str = "daily_digest") -> None:
        """Send a Repo Cockpit Telegram Rich Message template demo."""
        allowed = {
            "repo_status", "pr_review", "ci_failure", "daily_digest",
            "agent_progress", "handoff_before_cutoff", "media_report",
        }
        if template not in allowed:
            template = "daily_digest"
        try:
            import urllib.request as _urlrequest
            with _urlrequest.urlopen(
                f"http://127.0.0.1:8765/api/rich/preview/{template}", timeout=10
            ) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            rich_html = payload.get("html") or "<h1>Rich demo indisponible</h1>"
        except Exception as exc:
            await msg.reply_text(
                f"❌ Rich demo indisponible: {exc}",
                **self._link_preview_kwargs(),
            )
            return

        if self._bot and hasattr(self._bot, "do_api_request"):
            try:
                api_payload = {
                    "chat_id": int(getattr(msg, "chat_id")),
                    "rich_message": {"html": rich_html},
                }
                message_id = getattr(msg, "message_id", None)
                if message_id is not None:
                    api_payload["reply_parameters"] = {"message_id": message_id}
                await self._bot.do_api_request("sendRichMessage", api_kwargs=api_payload)
                return
            except Exception as exc:
                if not self._is_rich_fallback_error(exc):
                    logger.warning("[%s] /richdemo rich send failed; falling back: %s", self.name, exc)
        await msg.reply_text(
            "⚠️ Rich Message non disponible en fallback. Ouvre /serveurstatut ou consulte le backend /api/rich/preview/" + template,
            **self._link_preview_kwargs(),
        )


    async def _handle_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming command messages."""
        msg = self._effective_update_message(update)
        if not msg or not msg.text:
            return
        if not self._should_process_message(msg, is_command=True):
            return
        await self._ensure_forum_commands(msg)

        text = msg.text or ""
        command_token = text.strip().split(maxsplit=1)[0].split("@", 1)[0].lower()
        command_args = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""
        await self._log_cockpit_message(msg, direction="incoming", role="command_echo", command=command_token)
        if command_token in {"/new", "/newchat"}:
            await self._send_new_command(msg, command_args)
            return
        if command_token in {"/libre", "/reset-libre", "/chatlibre"}:
            await self._send_libre_command(msg, command_args)
            return
        if command_token == "/dev":
            await self._send_dev_command(msg, command_args)
            return
        if command_token in {"/chat", "/current"}:
            await self._send_chat_status_command(msg)
            return
        if command_token in {"/conv", "/convs", "/conversations"}:
            await self._send_conversations_command(msg, command_args)
            return
        if command_token in {"/renamechat", "/renommerchat", "/threadname"}:
            await self._send_rename_thread_command(msg, command_args)
            return
        if command_token in {"/archive", "/archivechat"}:
            await self._send_thread_action_command(msg, "archive")
            return
        if command_token in {"/delete", "/deletechat"}:
            await self._send_thread_action_command(msg, "delete")
            return
        if command_token in {"/app", "/miniapp"}:
            await self._send_hermes_mini_app_shortcut(msg)
            return
        if command_token == "/repo":
            await self._send_repo_cockpit_shortcut(msg)
            return
        if command_token == "/serveurstatut":
            await self._send_vps_command(msg)
            return
        if command_token in {"/vps", "/vpsstatus", "/serverstatus"}:
            await self._send_vps_command(msg)
            return
        if command_token == "/watch":
            await self._send_watch_command(msg, command_args)
            return
        if command_token == "/jobs":
            await self._send_jobs_command(msg, command_args)
            return
        if command_token in {"/updatecheck", "/update-check"}:
            await self._send_updatecheck_command(msg, command_args)
            return
        if command_token == "/tasks":
            await self._send_tasks_command(msg, command_args)
            return
        if command_token in {"/prs", "/pulls", "/pr"}:
            await self._send_pending_prs_command(msg, command_args)
            return
        if command_token in {"/audit", "/auditer"}:
            await self._send_audit_command(msg, command_args)
            return
        if command_token == "/task":
            await self._send_task_command(msg, command_args)
            return
        if command_token == "/status":
            await self._send_status_command(msg, command_args)
            return
        if command_token == "/runs":
            await self._send_runs_command(msg, command_args)
            return
        if command_token == "/approve":
            await self._send_approve_command(msg, command_args)
            return
        if command_token == "/worker":
            await self._send_worker_command(msg, command_args)
            return
        if command_token == "/quota":
            await self._send_quota_command(msg)
            return
        if command_token == "/logs":
            await self._send_logs_command(msg, command_args)
            return
        if command_token in {"/clear", "/clean", "/cleanchat"}:
            await self._send_clean_command(msg, command_args)
            return
        if command_token == "/richdemo":
            parts = (msg.text or "").strip().split(maxsplit=1)
            template = parts[1].strip().split()[0] if len(parts) > 1 and parts[1].strip() else "daily_digest"
            await self._send_richdemo(msg, template)
            return

        event = self._build_message_event(msg, MessageType.COMMAND, update_id=update.update_id)
        event.text = self._clean_bot_trigger_text(event.text)
        await self._cache_replied_media(msg, event)
        event = self._apply_telegram_group_observe_attribution(event)
        await self.handle_message(event)

    async def _handle_location_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming location/venue pin messages."""
        msg = self._effective_update_message(update)
        if not msg:
            return
        if not self._should_process_message(msg):
            if self._should_observe_unmentioned_group_message(msg):
                self._observe_unmentioned_group_message(msg, MessageType.LOCATION, update_id=update.update_id)
            return

        venue = getattr(msg, "venue", None)
        location = getattr(venue, "location", None) if venue else getattr(msg, "location", None)

        if not location:
            return

        lat = getattr(location, "latitude", None)
        lon = getattr(location, "longitude", None)
        if lat is None or lon is None:
            return

        # Build a text message with coordinates and context
        parts = ["[The user shared a location pin.]"]
        if venue:
            title = getattr(venue, "title", None)
            address = getattr(venue, "address", None)
            if title:
                parts.append(f"Venue: {title}")
            if address:
                parts.append(f"Address: {address}")
        parts.append(f"latitude: {lat}")
        parts.append(f"longitude: {lon}")
        parts.append(f"Map: https://www.google.com/maps/search/?api=1&query={lat},{lon}")
        parts.append("Ask what they'd like to find nearby (restaurants, cafes, etc.) and any preferences.")

        event = self._build_message_event(msg, MessageType.LOCATION, update_id=update.update_id)
        event.text = "\n".join(parts)
        event = self._apply_telegram_group_observe_attribution(event)
        await self.handle_message(event)

    # ------------------------------------------------------------------
    # Text message aggregation (handles Telegram client-side splits)
    # ------------------------------------------------------------------

    def _text_batch_key(self, event: MessageEvent) -> str:
        """Session-scoped key for text message batching.

        Applies the installed topic-recovery hook first so DM-topic batches
        coalesce on (and dispatch to) the recovered lane rather than the
        raw inbound ``message_thread_id`` Telegram may have attached.
        """
        from gateway.session import build_session_key
        self._apply_topic_recovery(event)
        return build_session_key(
            event.source,
            group_sessions_per_user=self.config.extra.get("group_sessions_per_user", True),
            thread_sessions_per_user=self.config.extra.get("thread_sessions_per_user", False),
        )

    def _enqueue_text_event(self, event: MessageEvent) -> None:
        """Buffer a text event and reset the flush timer.

        When Telegram splits a long user message into multiple updates,
        they arrive within a few hundred milliseconds.  This method
        concatenates them and waits for a short quiet period before
        dispatching the combined message.
        """
        key = self._text_batch_key(event)
        existing = self._pending_text_batches.get(key)
        chunk_len = len(event.text or "")
        if existing is None:
            event._last_chunk_len = chunk_len  # type: ignore[attr-defined]
            self._pending_text_batches[key] = event
        else:
            # Append text from the follow-up chunk
            if event.text:
                existing.text = f"{existing.text}\n{event.text}" if existing.text else event.text
            existing._last_chunk_len = chunk_len  # type: ignore[attr-defined]
            # Merge any media that might be attached
            if event.media_urls:
                existing.media_urls.extend(event.media_urls)
                existing.media_types.extend(event.media_types)

        # Cancel any pending flush and restart the timer
        prior_task = self._pending_text_batch_tasks.get(key)
        if prior_task and not prior_task.done():
            prior_task.cancel()
        self._pending_text_batch_tasks[key] = asyncio.create_task(
            self._flush_text_batch(key)
        )

    async def _flush_text_batch(self, key: str) -> None:
        """Wait for the quiet period then dispatch the aggregated text.

        Uses a longer delay when the latest chunk is near Telegram's 4096-char
        split point, since a continuation chunk is almost certain.
        """
        current_task = asyncio.current_task()
        try:
            # Adaptive delay tiers:
            #  - last chunk ≥ _SPLIT_THRESHOLD: a continuation is almost
            #    certain → wait the longer split delay.
            #  - total accumulated text ≤ _TEXT_BATCH_FAST_LEN (~320 cp):
            #    short message → cap delay at _TEXT_BATCH_FAST_DELAY_S
            #    so the agent sees the text near-instantly.
            #  - total ≤ _TEXT_BATCH_SHORT_LEN (~1024 cp):
            #    medium → cap at _TEXT_BATCH_SHORT_DELAY_S.
            #  - otherwise: use the configured cap.
            # Tiers compose with operator overrides via the env-var-driven
            # ``_text_batch_delay_seconds`` (e.g. an operator who sets the
            # cap below 0.18s gets that lower number on every tier).
            pending = self._pending_text_batches.get(key)
            last_len = getattr(pending, "_last_chunk_len", 0) if pending else 0
            total_len = len(getattr(pending, "text", "") or "") if pending else 0
            if last_len >= self._SPLIT_THRESHOLD:
                delay = self._text_batch_split_delay_seconds
            elif total_len <= self._TEXT_BATCH_FAST_LEN:
                delay = min(self._text_batch_delay_seconds, self._TEXT_BATCH_FAST_DELAY_S)
            elif total_len <= self._TEXT_BATCH_SHORT_LEN:
                delay = min(self._text_batch_delay_seconds, self._TEXT_BATCH_SHORT_DELAY_S)
            else:
                delay = self._text_batch_delay_seconds
            await asyncio.sleep(delay)
            event = self._pending_text_batches.pop(key, None)
            if not event:
                return
            logger.info(
                "[Telegram] Flushing text batch %s (%d chars)",
                key, len(event.text or ""),
            )
            original_msg = getattr(event, "_telegram_message", None)
            if original_msg is not None and await self._maybe_handle_libre_text(original_msg, event.text or ""):
                return
            if original_msg is not None and await self._maybe_handle_pilot_intake_text(original_msg, event.text or ""):
                return
            await self.handle_message(event)
        finally:
            if self._pending_text_batch_tasks.get(key) is current_task:
                self._pending_text_batch_tasks.pop(key, None)

    # ------------------------------------------------------------------
    # Photo batching
    # ------------------------------------------------------------------

    def _photo_batch_key(self, event: MessageEvent, msg: Message) -> str:
        """Return a batching key for Telegram photos/albums."""
        from gateway.session import build_session_key
        session_key = build_session_key(
            event.source,
            group_sessions_per_user=self.config.extra.get("group_sessions_per_user", True),
            thread_sessions_per_user=self.config.extra.get("thread_sessions_per_user", False),
        )
        media_group_id = getattr(msg, "media_group_id", None)
        if media_group_id:
            return f"{session_key}:album:{media_group_id}"
        return f"{session_key}:photo-burst"

    async def _flush_photo_batch(self, batch_key: str) -> None:
        """Send a buffered photo burst/album as a single MessageEvent."""
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(self._media_batch_delay_seconds)
            event = self._pending_photo_batches.pop(batch_key, None)
            if not event:
                return
            logger.info("[Telegram] Flushing photo batch %s with %d image(s)", batch_key, len(event.media_urls))
            await self.handle_message(event)
        finally:
            if self._pending_photo_batch_tasks.get(batch_key) is current_task:
                self._pending_photo_batch_tasks.pop(batch_key, None)

    def _enqueue_photo_event(self, batch_key: str, event: MessageEvent) -> None:
        """Merge photo events into a pending batch and schedule flush."""
        existing = self._pending_photo_batches.get(batch_key)
        if existing is None:
            self._pending_photo_batches[batch_key] = event
        else:
            existing.media_urls.extend(event.media_urls)
            existing.media_types.extend(event.media_types)
            if event.text:
                existing.text = self._merge_caption(existing.text, event.text)

        prior_task = self._pending_photo_batch_tasks.get(batch_key)
        if prior_task and not prior_task.done():
            prior_task.cancel()

        self._pending_photo_batch_tasks[batch_key] = asyncio.create_task(self._flush_photo_batch(batch_key))

    async def _handle_media_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming media messages, downloading images to local cache."""
        if not update.message:
            return
        if not self._should_process_message(update.message):
            if self._should_observe_unmentioned_group_message(update.message):
                _m = update.message
                _observe_type = self._media_message_type(_m)
                _event = self._build_message_event(_m, _observe_type, update_id=update.update_id)
                if _m.caption:
                    _event.text = self._clean_bot_trigger_text(_m.caption)
                await self._cache_observed_media(_m, _event)
                self._observe_unmentioned_group_message(
                    _m, _event.message_type, update_id=update.update_id, event=_event
                )
            return

        msg = update.message

        msg_type = self._media_message_type(msg)

        event = self._build_message_event(msg, msg_type, update_id=update.update_id)

        # Add caption as text
        if msg.caption:
            event.text = self._clean_bot_trigger_text(msg.caption)

        # Handle stickers: describe via vision tool with caching
        if msg.sticker:
            await self._handle_sticker(msg, event)
            event = self._apply_telegram_group_observe_attribution(event)
            await self.handle_message(event)
            return

        # Apply observe attribution after caption is set; sticker is handled above
        # because _handle_sticker overwrites event.text with its vision description.
        event = self._apply_telegram_group_observe_attribution(event)

        # Download photo to local image cache so the vision tool can access it
        # even after Telegram's ephemeral file URLs expire (~1 hour).
        if msg.photo:
            try:
                # msg.photo is a list of PhotoSize sorted by size; take the largest
                photo = msg.photo[-1]
                file_obj = await photo.get_file()
                # Download the image bytes directly into memory
                image_bytes = await file_obj.download_as_bytearray()
                # Determine extension from the file path if available
                ext = ".jpg"
                if file_obj.file_path:
                    for candidate in [".png", ".webp", ".gif", ".jpeg", ".jpg"]:
                        if file_obj.file_path.lower().endswith(candidate):
                            ext = candidate
                            break
                # Save to local cache (for vision tool access)
                cached_path = cache_image_from_bytes(bytes(image_bytes), ext=ext)
                event.media_urls = [cached_path]
                event.media_types = [f"image/{ext.lstrip('.')}" ]
                logger.info("[Telegram] Cached user photo at %s", cached_path)
                media_group_id = getattr(msg, "media_group_id", None)
                if media_group_id:
                    await self._queue_media_group_event(str(media_group_id), event)
                else:
                    batch_key = self._photo_batch_key(event, msg)
                    self._enqueue_photo_event(batch_key, event)
                return

            except Exception as e:
                logger.warning("[Telegram] Failed to cache photo: %s", e, exc_info=True)

        # Download voice/audio messages to cache for STT transcription
        if msg.voice:
            try:
                allowed, note = self._telegram_media_size_allowed(msg.voice, "voice message")
                if not allowed:
                    event.text = self._append_observed_note(event.text, note or "")
                    logger.info("[Telegram] Skipped oversized user voice (size=%s)", getattr(msg.voice, "file_size", None))
                    await self.handle_message(event)
                    return
                file_obj = await msg.voice.get_file()
                audio_bytes = await file_obj.download_as_bytearray()
                cached_path = cache_audio_from_bytes(bytes(audio_bytes), ext=".ogg")
                event.media_urls = [cached_path]
                event.media_types = ["audio/ogg"]
                logger.info("[Telegram] Cached user voice at %s", cached_path)
            except Exception as e:
                logger.warning("[Telegram] Failed to cache voice: %s", e, exc_info=True)
        elif msg.audio:
            try:
                allowed, note = self._telegram_media_size_allowed(msg.audio, "audio file")
                if not allowed:
                    event.text = self._append_observed_note(event.text, note or "")
                    logger.info("[Telegram] Skipped oversized user audio (size=%s)", getattr(msg.audio, "file_size", None))
                    await self.handle_message(event)
                    return
                file_obj = await msg.audio.get_file()
                audio_bytes = await file_obj.download_as_bytearray()
                cached_path = cache_audio_from_bytes(bytes(audio_bytes), ext=".mp3")
                event.media_urls = [cached_path]
                event.media_types = ["audio/mp3"]
                logger.info("[Telegram] Cached user audio at %s", cached_path)
            except Exception as e:
                logger.warning("[Telegram] Failed to cache audio: %s", e, exc_info=True)

        elif msg.video:
            try:
                file_obj = await msg.video.get_file()
                video_bytes = await file_obj.download_as_bytearray()
                ext = ".mp4"
                if getattr(file_obj, "file_path", None):
                    for candidate in SUPPORTED_VIDEO_TYPES:
                        if file_obj.file_path.lower().endswith(candidate):
                            ext = candidate
                            break
                cached_path = cache_video_from_bytes(bytes(video_bytes), ext=ext)
                event.media_urls = [cached_path]
                event.media_types = [SUPPORTED_VIDEO_TYPES.get(ext, "video/mp4")]
                logger.info("[Telegram] Cached user video at %s", cached_path)
            except Exception as e:
                logger.warning("[Telegram] Failed to cache video: %s", e, exc_info=True)

        # Download document files to cache for agent processing
        elif msg.document:
            doc = msg.document
            try:
                # Determine file extension
                ext = ""
                original_filename = doc.file_name or ""
                if original_filename:
                    _, ext = os.path.splitext(original_filename)
                    ext = ext.lower()

                # Normalize mime_type for robust comparisons (some clients send
                # uppercase like "IMAGE/PNG").
                doc_mime = (doc.mime_type or "").lower()

                # If no extension from filename, reverse-lookup from MIME type
                if not ext and doc_mime:
                    ext = _TELEGRAM_IMAGE_MIME_TO_EXT.get(doc_mime, "")
                    if not ext:
                        mime_to_ext = {v: k for k, v in SUPPORTED_DOCUMENT_TYPES.items()}
                        ext = mime_to_ext.get(doc_mime, "")

                # Check file size early so image documents cannot bypass the
                # document size limit by taking the image path.
                if not doc.file_size or doc.file_size > self._max_doc_bytes:
                    limit_mb = self._max_doc_bytes // (1024 * 1024)
                    event.text = (
                        "The document is too large or its size could not be verified. "
                        f"Maximum: {limit_mb} MB."
                    )
                    logger.info("[Telegram] Document too large: %s bytes", doc.file_size)
                    await self.handle_message(event)
                    return

                # Telegram may deliver screenshots/photos as documents. If the
                # payload is actually an image, route it through the image cache
                # and batching path instead of rejecting it as a document.
                if ext in _TELEGRAM_IMAGE_EXTENSIONS or doc_mime.startswith("image/"):
                    file_obj = await doc.get_file()
                    image_bytes = await file_obj.download_as_bytearray()
                    image_ext = ext if ext in _TELEGRAM_IMAGE_EXTENSIONS else _TELEGRAM_IMAGE_MIME_TO_EXT.get(doc_mime, ".jpg")
                    try:
                        cached_path = cache_image_from_bytes(bytes(image_bytes), ext=image_ext)
                    except ValueError as e:
                        logger.warning("[Telegram] Failed to cache image document: %s", e, exc_info=True)
                        event.text = (
                            f"Image document '{original_filename or doc_mime or ext or 'unknown'}' "
                            "could not be read as an image."
                        )
                        await self.handle_message(event)
                        return

                    event.message_type = MessageType.PHOTO
                    event.media_urls = [cached_path]
                    event.media_types = [doc_mime if doc_mime.startswith("image/") else _TELEGRAM_IMAGE_EXT_TO_MIME.get(image_ext, "image/jpeg")]
                    logger.info("[Telegram] Cached user image-document at %s", cached_path)

                    media_group_id = getattr(msg, "media_group_id", None)
                    if media_group_id:
                        await self._queue_media_group_event(str(media_group_id), event)
                    else:
                        batch_key = self._photo_batch_key(event, msg)
                        self._enqueue_photo_event(batch_key, event)
                    return

                if not ext and doc.mime_type:
                    video_mime_to_ext = {v: k for k, v in SUPPORTED_VIDEO_TYPES.items()}
                    ext = video_mime_to_ext.get(doc.mime_type, "")

                if not ext and doc.mime_type:
                    # SUPPORTED_IMAGE_DOCUMENT_TYPES has duplicate values (.jpg + .jpeg
                    # both map to image/jpeg); keep the first ext we encounter.
                    image_mime_to_ext: dict[str, str] = {}
                    for _ext, _mime in SUPPORTED_IMAGE_DOCUMENT_TYPES.items():
                        image_mime_to_ext.setdefault(_mime, _ext)
                    ext = image_mime_to_ext.get(doc.mime_type, "")

                if ext in SUPPORTED_VIDEO_TYPES:
                    file_obj = await doc.get_file()
                    video_bytes = await file_obj.download_as_bytearray()
                    cached_path = cache_video_from_bytes(bytes(video_bytes), ext=ext)
                    event.media_urls = [cached_path]
                    event.media_types = [SUPPORTED_VIDEO_TYPES[ext]]
                    event.message_type = MessageType.VIDEO
                    logger.info("[Telegram] Cached user video document at %s", cached_path)
                    await self.handle_message(event)
                    return

                # NOTE: image-document handling is performed earlier in this
                # function (ext in _TELEGRAM_IMAGE_EXTENSIONS or image/* mime),
                # which returns before reaching here.  Any subsequent
                # ext-in-SUPPORTED_IMAGE_DOCUMENT_TYPES branch would be dead
                # code — the extension sets are identical.

                # Check if supported
                if ext not in SUPPORTED_DOCUMENT_TYPES:
                    supported_list = ", ".join(sorted(SUPPORTED_DOCUMENT_TYPES.keys()))
                    event.text = (
                        f"Unsupported document type '{ext or 'unknown'}'. "
                        f"Supported types: {supported_list}"
                    )
                    logger.info("[Telegram] Unsupported document type: %s", ext or "unknown")
                    await self.handle_message(event)
                    return

                # Download and cache
                file_obj = await doc.get_file()
                doc_bytes = await file_obj.download_as_bytearray()
                raw_bytes = bytes(doc_bytes)
                cached_path = cache_document_from_bytes(raw_bytes, original_filename or f"document{ext}")
                mime_type = SUPPORTED_DOCUMENT_TYPES[ext]
                event.media_urls = [cached_path]
                event.media_types = [mime_type]
                logger.info("[Telegram] Cached user document at %s", cached_path)

                # For text files, inject content into event.text (capped at 100 KB)
                MAX_TEXT_INJECT_BYTES = 100 * 1024
                if ext in {".md", ".txt"} and len(raw_bytes) <= MAX_TEXT_INJECT_BYTES:
                    try:
                        text_content = raw_bytes.decode("utf-8")
                        display_name = original_filename or f"document{ext}"
                        display_name = re.sub(r'[^\w.\- ]', '_', display_name)
                        injection = f"[Content of {display_name}]:\n{text_content}"
                        if event.text:
                            event.text = f"{injection}\n\n{event.text}"
                        else:
                            event.text = injection
                    except UnicodeDecodeError:
                        logger.warning(
                            "[Telegram] Could not decode text file as UTF-8, skipping content injection",
                            exc_info=True,
                        )

            except Exception as e:
                logger.warning("[Telegram] Failed to cache document: %s", e, exc_info=True)

        media_group_id = getattr(msg, "media_group_id", None)
        if media_group_id:
            await self._queue_media_group_event(str(media_group_id), event)
            return

        await self.handle_message(event)

    async def _queue_media_group_event(self, media_group_id: str, event: MessageEvent) -> None:
        """Buffer Telegram media-group items so albums arrive as one logical event.

        Telegram delivers albums as multiple updates with a shared media_group_id.
        If we forward each item immediately, the gateway thinks the second image is a
        new user message and interrupts the first. We debounce briefly and merge the
        attachments into a single MessageEvent.
        """
        existing = self._media_group_events.get(media_group_id)
        if existing is None:
            self._media_group_events[media_group_id] = event
        else:
            existing.media_urls.extend(event.media_urls)
            existing.media_types.extend(event.media_types)
            if event.text:
                existing.text = self._merge_caption(existing.text, event.text)

        prior_task = self._media_group_tasks.get(media_group_id)
        if prior_task:
            prior_task.cancel()

        self._media_group_tasks[media_group_id] = asyncio.create_task(
            self._flush_media_group_event(media_group_id)
        )

    async def _flush_media_group_event(self, media_group_id: str) -> None:
        try:
            await asyncio.sleep(self.MEDIA_GROUP_WAIT_SECONDS)
            event = self._media_group_events.pop(media_group_id, None)
            if event is not None:
                await self.handle_message(event)
        except asyncio.CancelledError:
            return
        finally:
            self._media_group_tasks.pop(media_group_id, None)

    async def _handle_sticker(self, msg: Message, event: "MessageEvent") -> None:
        """
        Describe a Telegram sticker via vision analysis, with caching.

        For static stickers (WEBP), we download, analyze with vision, and cache
        the description by file_unique_id. For animated/video stickers, we inject
        a placeholder noting the emoji.
        """
        from gateway.sticker_cache import (
            get_cached_description,
            cache_sticker_description,
            build_sticker_injection,
            build_animated_sticker_injection,
            STICKER_VISION_PROMPT,
        )

        sticker = msg.sticker
        emoji = sticker.emoji or ""
        set_name = sticker.set_name or ""

        # Animated and video stickers can't be analyzed as static images
        if sticker.is_animated or sticker.is_video:
            event.text = build_animated_sticker_injection(emoji)
            return

        # Check the cache first
        cached = get_cached_description(sticker.file_unique_id)
        if cached:
            event.text = build_sticker_injection(
                cached["description"], cached.get("emoji", emoji), cached.get("set_name", set_name)
            )
            logger.info("[Telegram] Sticker cache hit: %s", sticker.file_unique_id)
            return

        # Cache miss -- download and analyze
        try:
            file_obj = await sticker.get_file()
            image_bytes = await file_obj.download_as_bytearray()
            cached_path = cache_image_from_bytes(bytes(image_bytes), ext=".webp")
            logger.info("[Telegram] Analyzing sticker at %s", cached_path)

            from tools.vision_tools import vision_analyze_tool
            result_json = await vision_analyze_tool(
                image_url=cached_path,
                user_prompt=STICKER_VISION_PROMPT,
            )
            result = json.loads(result_json)

            if result.get("success"):
                description = result.get("analysis", "a sticker")
                cache_sticker_description(sticker.file_unique_id, description, emoji, set_name)
                event.text = build_sticker_injection(description, emoji, set_name)
            else:
                # Vision failed -- use emoji as fallback
                event.text = build_sticker_injection(
                    f"a sticker with emoji {emoji}" if emoji else "a sticker",
                    emoji, set_name,
                )
        except Exception as e:
            logger.warning("[Telegram] Sticker analysis error: %s", e, exc_info=True)
            event.text = build_sticker_injection(
                f"a sticker with emoji {emoji}" if emoji else "a sticker",
                emoji, set_name,
            )

    def _reload_dm_topics_from_config(self) -> None:
        """Re-read dm_topics from config.yaml and load any new thread_ids into cache.

        This allows topics created externally (e.g. by the agent via API) to be
        recognized without a gateway restart.
        """
        try:
            from hermes_constants import get_hermes_home
            config_path = get_hermes_home() / "config.yaml"
            if not config_path.exists():
                return

            import yaml as _yaml
            with open(config_path, "r", encoding="utf-8") as f:
                config = _yaml.safe_load(f) or {}

            dm_topics = (
                config.get("platforms", {})
                .get("telegram", {})
                .get("extra", {})
                .get("dm_topics", [])
            )
            if not dm_topics:
                # Clear both config and precomputed set when all topics are removed
                self._dm_topics_config = []
                self._dm_topic_chat_ids = set()
                return

            # Update in-memory config and cache any new thread_ids
            self._dm_topics_config = dm_topics
            # Rebuild the chat_id set for O(1) root-DM ignore lookup
            self._dm_topic_chat_ids = {
                str(chat_entry["chat_id"]) for chat_entry in dm_topics if "chat_id" in chat_entry
            }
            for chat_entry in dm_topics:
                cid = chat_entry.get("chat_id")
                if not cid:
                    continue
                for t in chat_entry.get("topics", []):
                    tid = t.get("thread_id")
                    name = t.get("name")
                    if tid and name:
                        cache_key = f"{cid}:{name}"
                        if cache_key not in self._dm_topics:
                            self._dm_topics[cache_key] = int(tid)
                            logger.info(
                                "[%s] Hot-loaded DM topic from config: %s -> thread_id=%s",
                                self.name, cache_key, tid,
                            )
        except Exception as e:
            logger.debug("[%s] Failed to reload dm_topics from config: %s", self.name, e)

    def _get_dm_topic_info(self, chat_id: str, thread_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Look up DM topic config by chat_id and thread_id.

        Returns the topic config dict (name, skill, etc.) if this thread_id
        matches a known DM topic, or None.
        """
        if not thread_id:
            return None

        thread_id_int = int(thread_id)

        # Check cached topics first (created by us or loaded at startup)
        for key, cached_tid in self._dm_topics.items():
            if cached_tid == thread_id_int and key.startswith(f"{chat_id}:"):
                topic_name = key.split(":", 1)[1]
                # Find the full config for this topic
                for chat_entry in self._dm_topics_config:
                    if str(chat_entry.get("chat_id")) == chat_id:
                        for t in chat_entry.get("topics", []):
                            if t.get("name") == topic_name:
                                return t
                return {"name": topic_name}

        # Not in cache — hot-reload config in case topics were added externally
        self._reload_dm_topics_from_config()

        # Check cache again after reload
        for key, cached_tid in self._dm_topics.items():
            if cached_tid == thread_id_int and key.startswith(f"{chat_id}:"):
                topic_name = key.split(":", 1)[1]
                for chat_entry in self._dm_topics_config:
                    if str(chat_entry.get("chat_id")) == chat_id:
                        for t in chat_entry.get("topics", []):
                            if t.get("name") == topic_name:
                                return t
                return {"name": topic_name}

        return None

    def _cache_dm_topic_from_message(self, chat_id: str, thread_id: str, topic_name: str) -> None:
        """Cache a thread_id -> topic_name mapping discovered from an incoming message."""
        cache_key = f"{chat_id}:{topic_name}"
        if cache_key not in self._dm_topics:
            self._dm_topics[cache_key] = int(thread_id)
            logger.info(
                "[%s] Cached DM topic from message: %s -> thread_id=%s",
                self.name, cache_key, thread_id,
            )

    def _build_message_event(
        self,
        message: Message,
        msg_type: MessageType,
        update_id: Optional[int] = None,
    ) -> MessageEvent:
        """Build a MessageEvent from a Telegram message.

        ``update_id`` is the ``Update.update_id`` from PTB; passing it through
        lets ``/restart`` record the triggering offset so the new gateway
        process can advance past it (prevents ``/restart`` being re-delivered
        when PTB's graceful-shutdown ACK fails).
        """
        chat = message.chat
        user = message.from_user

        # Determine chat type.  Normalize through ``str`` so tests/mocks and
        # python-telegram-bot enum values both work (``ChatType.CHANNEL`` is
        # string-like, but mocks often provide plain strings).
        raw_chat_type = getattr(chat, "type", "")
        chat_type_tokens = {
            str(raw_chat_type),
            repr(raw_chat_type),
            str(getattr(raw_chat_type, "name", "")),
            str(getattr(raw_chat_type, "value", "")),
        }
        telegram_chat_type = " ".join(token.lower() for token in chat_type_tokens)
        chat_type = "dm"
        chat_id_text = str(getattr(chat, "id", ""))
        if raw_chat_type == getattr(ChatType, "CHANNEL", object()) or "channel" in telegram_chat_type:
            chat_type = "channel"
        elif (
            raw_chat_type in {getattr(ChatType, "GROUP", object()), getattr(ChatType, "SUPERGROUP", object())}
            or "supergroup" in telegram_chat_type
            or "group" in telegram_chat_type
            or chat_id_text.startswith("-")
        ):
            chat_type = "group"

        # Resolve Telegram topic name and skill binding.
        # Only preserve message_thread_id when Telegram marks the message as
        # a real topic/forum message. Telegram can also populate
        # message_thread_id for ordinary reply UI anchors; treating those as
        # durable session threads fragments workflows such as CAPTCHA/login
        # handoffs where the user later replies "done" in the same group.
        # Private chats have the same pitfall: only real DM topic messages
        # (is_topic_message=True) should keep the thread id, otherwise sends
        # can hit Telegram's 'Message thread not found' error (#3206).
        thread_id_raw = message.message_thread_id
        is_topic_message = bool(getattr(message, "is_topic_message", False))
        is_forum_group = getattr(chat, "is_forum", False) is True
        thread_id_str = None
        if thread_id_raw is not None:
            if chat_type == "group" and (is_topic_message or is_forum_group):
                thread_id_str = str(thread_id_raw)
            elif chat_type == "dm" and is_topic_message:
                thread_id_str = str(thread_id_raw)
        # For forum groups without an explicit topic, default to the
        # General-topic id so the gateway routes back to the General topic
        # rather than dropping into the bot's main channel (#22423).
        if chat_type == "group" and thread_id_str is None and is_forum_group:
            thread_id_str = self._GENERAL_TOPIC_THREAD_ID
        chat_topic = None
        topic_skill = None

        if chat_type == "dm" and thread_id_str:
            topic_info = self._get_dm_topic_info(str(chat.id), thread_id_str)
            if topic_info:
                chat_topic = topic_info.get("name")
                topic_skill = topic_info.get("skill")

            # Also check forum_topic_created service message for topic discovery
            if hasattr(message, "forum_topic_created") and message.forum_topic_created:
                created_name = message.forum_topic_created.name
                if created_name:
                    self._cache_dm_topic_from_message(str(chat.id), thread_id_str, created_name)
                    if not chat_topic:
                        chat_topic = created_name

        elif chat_type == "group" and thread_id_str:
            # Group/supergroup forum topic skill binding via config.extra['group_topics']
            group_topics_config: list = self.config.extra.get("group_topics", [])
            for chat_entry in group_topics_config:
                if str(chat_entry.get("chat_id", "")) == str(chat.id):
                    for topic in chat_entry.get("topics", []):
                        tid = topic.get("thread_id")
                        if tid is not None and str(tid) == thread_id_str:
                            chat_topic = topic.get("name")
                            topic_skill = topic.get("skill")
                            break
                    break

        # Build source
        source = self.build_source(
            chat_id=str(chat.id),
            chat_name=chat.title or (chat.full_name if hasattr(chat, "full_name") else None),
            chat_type=chat_type,
            user_id=(
                str(user.id)
                if user
                else (str(chat.id) if chat_type in {"dm", "channel"} else None)
            ),
            user_name=(
                user.full_name
                if user
                else (
                    chat.full_name
                    if hasattr(chat, "full_name") and chat_type == "dm"
                    else (chat.title if chat_type == "channel" else None)
                )
            ),
            thread_id=thread_id_str,
            chat_topic=chat_topic,
            message_id=str(message.message_id),
        )

        # Extract reply context if this message is a reply.
        # Prefer Telegram's native partial quote (message.quote, TextQuote)
        # so a user replying to a single selected substring of a prior
        # multi-section message doesn't get the whole replied-to message
        # injected into the agent's context — which can cause the agent
        # to act on unrelated actionable-looking text the user didn't
        # quote (#22619). Fall back to the full replied-to message text
        # / caption when no native quote is present.
        reply_to_id = None
        reply_to_text = None
        if message.reply_to_message:
            reply_to_id = str(message.reply_to_message.message_id)
            quote = getattr(message, "quote", None)
            quote_text = getattr(quote, "text", None) if quote is not None else None
            if quote_text:
                reply_to_text = quote_text
            else:
                reply_to_text = (
                    message.reply_to_message.text
                    or message.reply_to_message.caption
                    or None
                )
                if not reply_to_text:
                    # Rich messages (sendRichMessage — the launchd briefings and
                    # the gateway's own rich finals) are NOT echoed with their
                    # content in reply_to_message; Telegram sends no text,
                    # caption, or api_kwargs for them. Recover the text we sent
                    # from our local send-time index, keyed by message id.
                    try:
                        from gateway import rich_sent_store
                        reply_to_text = rich_sent_store.lookup(
                            str(chat.id), reply_to_id
                        )
                    except Exception:
                        reply_to_text = None

        # Per-channel/topic ephemeral prompt
        from gateway.platforms.base import resolve_channel_prompt
        _chat_id_str = str(chat.id)
        _channel_prompt = resolve_channel_prompt(
            self.config.extra,
            thread_id_str or _chat_id_str,
            _chat_id_str if thread_id_str else None,
        )

        return MessageEvent(
            text=message.text or "",
            message_type=msg_type,
            source=source,
            raw_message=message,
            message_id=str(message.message_id),
            platform_update_id=update_id,
            reply_to_message_id=reply_to_id,
            reply_to_text=reply_to_text,
            auto_skill=topic_skill,
            channel_prompt=_channel_prompt,
            timestamp=message.date,
        )

    # ── Message reactions (processing lifecycle) ──────────────────────────

    def _reactions_enabled(self) -> bool:
        """Check if message reactions are enabled via config/env."""
        return os.getenv("TELEGRAM_REACTIONS", "false").lower() not in {"false", "0", "no"}

    async def _set_reaction(self, chat_id: str, message_id: str, emoji: str) -> bool:
        """Set a single emoji reaction on a Telegram message."""
        if not self._bot:
            return False
        try:
            await self._bot.set_message_reaction(
                chat_id=int(chat_id),
                message_id=int(message_id),
                reaction=emoji,
            )
            return True
        except Exception as e:
            logger.debug("[%s] set_message_reaction failed (%s): %s", self.name, emoji, e)
            return False

    async def _clear_reactions(self, chat_id: str, message_id: str) -> bool:
        """Clear all reactions from a Telegram message.

        Calling ``set_message_reaction`` with ``reaction=None`` (or an empty
        sequence) is the documented Bot API way to remove all bot-set
        reactions on a message — equivalent to Bot API 10.0's
        ``deleteMessageReaction`` but supported in PTB 22.6 already.
        """
        if not self._bot:
            return False
        try:
            await self._bot.set_message_reaction(
                chat_id=int(chat_id),
                message_id=int(message_id),
                reaction=None,
            )
            return True
        except Exception as e:
            logger.debug("[%s] clear reactions failed: %s", self.name, e)
            return False

    async def on_processing_start(self, event: MessageEvent) -> None:
        """Add an in-progress reaction when message processing begins."""
        if not self._reactions_enabled():
            return
        chat_id = getattr(event.source, "chat_id", None)
        message_id = getattr(event, "message_id", None)
        if chat_id and message_id:
            await self._set_reaction(chat_id, message_id, "\U0001f440")

    async def on_processing_complete(self, event: MessageEvent, outcome: ProcessingOutcome) -> None:
        """Swap the in-progress reaction for a final success/failure reaction.

        Unlike Discord (additive reactions), Telegram's set_message_reaction
        replaces all existing reactions in one call — no remove step needed.

        On CANCELLED outcomes (e.g. the user runs ``/stop``, or a session is
        interrupted mid-flight), we explicitly clear the 👀 in-progress
        reaction so it doesn't linger on the user's message indefinitely.
        Without this clear, the only way to remove the 👀 was to wait for
        another agent run to swap it to 👍/👎 — which never happens if the
        cancellation was the last activity in the chat.
        """
        if not self._reactions_enabled():
            return
        chat_id = getattr(event.source, "chat_id", None)
        message_id = getattr(event, "message_id", None)
        if not (chat_id and message_id):
            return
        if outcome == ProcessingOutcome.CANCELLED:
            await self._clear_reactions(chat_id, message_id)
        else:
            await self._set_reaction(
                chat_id,
                message_id,
                "\U0001f44d" if outcome == ProcessingOutcome.SUCCESS else "\U0001f44e",
            )
