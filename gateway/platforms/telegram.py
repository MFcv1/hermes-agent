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
import urllib.parse
from urllib import request as _urlrequest, error as _urlerror
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Any

logger = logging.getLogger(__name__)

REPO_COCKPIT_MODES = {"ask_review", "pilote", "autopilot"}


def normalize_cockpit_mode(mode: str | None) -> str:
    """Return a supported Repo Cockpit mode, preserving the new Pilote flow."""
    clean = str(mode or "").strip().lower()
    return clean if clean in REPO_COCKPIT_MODES else "ask_review"

try:
    from telegram import Update, Bot, Message, InlineKeyboardButton, InlineKeyboardMarkup
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
from gateway.observation_reporter import post_runtime_observations
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


# Matches every character that MarkdownV2 requires to be backslash-escaped
# when it appears outside a code span or fenced code block.
_MDV2_ESCAPE_RE = re.compile(r'([_*\[\]()~`>#\+\-=|{}.!\\])')


def _escape_mdv2(text: str) -> str:
    """Escape Telegram MarkdownV2 special characters with a preceding backslash."""
    return _MDV2_ESCAPE_RE.sub(r'\\\1', text)


def _strip_mdv2(text: str) -> str:
    """Strip MarkdownV2 escape backslashes to produce clean plain text.

    Also removes MarkdownV2 formatting markers so the fallback
    doesn't show stray syntax characters from format_message conversion.
    """
    # Remove escape backslashes before special characters
    cleaned = re.sub(r'\\([_*\[\]()~`>#\+\-=|{}.!\\])', r'\1', text)
    # Remove standard markdown bold (**text** → text) BEFORE MarkdownV2 bold
    cleaned = re.sub(r'\*\*([^*]+)\*\*', r'\1', cleaned)
    # Remove MarkdownV2 bold markers that format_message converted from **bold**
    cleaned = re.sub(r'\*([^*]+)\*', r'\1', cleaned)
    # Remove MarkdownV2 italic markers that format_message converted from *italic*
    # Use word boundary (\b) to avoid breaking snake_case like my_variable_name
    cleaned = re.sub(r'(?<!\w)_([^_]+)_(?!\w)', r'\1', cleaned)
    # Remove MarkdownV2 strikethrough markers (~text~ → text)
    cleaned = re.sub(r'~([^~]+)~', r'\1', cleaned)
    # Remove MarkdownV2 spoiler markers (||text|| → text)
    cleaned = re.sub(r'\|\|([^|]+)\|\|', r'\1', cleaned)
    return cleaned


# ---------------------------------------------------------------------------
# Markdown table → Telegram-friendly row groups
# ---------------------------------------------------------------------------
# Telegram's MarkdownV2 has no table syntax — '|' is just an escaped literal,
# so pipe tables render as noisy backslash-pipe text with no alignment.
# Reformating each row into a bold heading plus bullet list keeps the content
# readable on mobile clients while preserving the source data.

# Matches a GFM table delimiter row: optional outer pipes, cells containing
# only dashes (with optional leading/trailing colons for alignment) separated
# by '|'.  Requires at least one internal '|' so lone '---' horizontal rules
# are NOT matched.
_TABLE_SEPARATOR_RE = re.compile(
    r'^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*){1,}\|?\s*$'
)


def _is_table_row(line: str) -> bool:
    """Return True if *line* could plausibly be a table data row."""
    stripped = line.strip()
    return bool(stripped) and '|' in stripped


def _split_markdown_table_row(line: str) -> list[str]:
    """Split a simple GFM table row into stripped cell values."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _render_table_block_for_telegram(table_block: list[str]) -> str:
    """Render a detected GFM table as Telegram-friendly row groups."""
    if len(table_block) < 3:
        return "\n".join(table_block)

    headers = _split_markdown_table_row(table_block[0])
    if len(headers) < 2:
        return "\n".join(table_block)

    # Detect row-label column: present when data rows have one more cell
    # than the header row (the row-label column carries no header).
    first_data_row = _split_markdown_table_row(table_block[2]) if len(table_block) > 2 else []
    has_row_label_col = len(first_data_row) == len(headers) + 1

    rendered_groups: list[str] = []
    for index, row in enumerate(table_block[2:], start=1):
        cells = _split_markdown_table_row(row)
        if has_row_label_col:
            # First cell is the row-label (heading); remaining cells align with headers.
            heading = cells[0] if cells and cells[0] else f"Row {index}"
            data_cells = cells[1:]
        else:
            # No row-label column: use first non-empty cell as heading.
            heading = next((cell for cell in cells if cell), f"Row {index}")
            data_cells = cells

        # Pad or trim data_cells to match headers length.
        if len(data_cells) < len(headers):
            data_cells.extend([""] * (len(headers) - len(data_cells)))
        elif len(data_cells) > len(headers):
            data_cells = data_cells[: len(headers)]

        # Build the bulleted lines for this row.  Skip any bullet whose value
        # duplicates the heading text -- when has_row_label_col is False the
        # heading IS the first data cell, and emitting it twice (once as the
        # bold heading, once as the first bullet) is visual noise.
        bullets: list[str] = []
        for header, value in zip(headers, data_cells):
            if not has_row_label_col and value == heading:
                continue
            bullets.append(f"• {header}: {value}")

        # Within a row-group: single newline between heading and its bullets,
        # and between successive bullets.  This keeps the row visually tight
        # on Telegram instead of stretching each bullet into its own paragraph.
        group_lines = [f"**{heading}**", *bullets]
        rendered_groups.append("\n".join(group_lines))

    # Between row-groups: blank line so each group reads as a distinct block.
    return "\n\n".join(rendered_groups)


def _wrap_markdown_tables(text: str) -> str:
    """Rewrite GFM-style pipe tables into Telegram-friendly bullet groups.

    Detected by a row containing '|' immediately followed by a delimiter
    row matching :data:`_TABLE_SEPARATOR_RE`.  Subsequent pipe-containing
    non-blank lines are consumed as the table body and rewritten as
    per-row bullet groups. Tables inside existing fenced code blocks are left
    alone.
    """
    if '|' not in text or '-' not in text:
        return text

    lines = text.split('\n')
    out: list[str] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()

        # Track existing fenced code blocks — never touch content inside.
        if stripped.startswith('```'):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if in_fence:
            out.append(line)
            i += 1
            continue

        # Look for a header row (contains '|') immediately followed by a
        # delimiter row.
        if (
            '|' in line
            and i + 1 < len(lines)
            and _TABLE_SEPARATOR_RE.match(lines[i + 1])
        ):
            table_block = [line, lines[i + 1]]
            j = i + 2
            while j < len(lines) and _is_table_row(lines[j]):
                table_block.append(lines[j])
                j += 1
            out.append(_render_table_block_for_telegram(table_block))
            i = j
            continue

        out.append(line)
        i += 1

    return '\n'.join(out)


class TelegramAdapter(TelegramModelsConfigMixin, BasePlatformAdapter):
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

    def _notification_kwargs(
        self, metadata: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Return disable_notification kwargs when the adapter is in silent mode.

        In "important" mode, all message sends are silently delivered
        (disable_notification=True) unless the caller explicitly requests a
        notification by setting ``metadata["notify"] = True``.
        """
        if getattr(self, "_notifications_mode", "important") != "important":
            return {}
        if (metadata or {}).get("notify"):
            return {}
        return {"disable_notification": True}

    def _is_callback_user_authorized(
        self,
        user_id: str,
        *,
        chat_id: Optional[str] = None,
        chat_type: Optional[str] = None,
        thread_id: Optional[str] = None,
        user_name: Optional[str] = None,
    ) -> bool:
        """Return whether a Telegram inline-button caller may perform gated actions."""
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            return False

        runner = getattr(getattr(self, "_message_handler", None), "__self__", None)
        auth_fn = getattr(runner, "_is_user_authorized", None)
        if callable(auth_fn):
            try:
                from gateway.session import SessionSource

                normalized_chat_type = str(chat_type or "dm").strip().lower() or "dm"
                if normalized_chat_type == "private":
                    normalized_chat_type = "dm"
                elif normalized_chat_type == "supergroup":
                    normalized_chat_type = "forum" if thread_id is not None else "group"

                source = SessionSource(
                    platform=Platform.TELEGRAM,
                    chat_id=str(chat_id or normalized_user_id),
                    chat_type=normalized_chat_type,
                    user_id=normalized_user_id,
                    user_name=str(user_name).strip() if user_name else None,
                    thread_id=str(thread_id) if thread_id is not None else None,
                )
                return bool(auth_fn(source))
            except Exception:
                logger.debug(
                    "[Telegram] Falling back to env-only callback auth for user %s",
                    normalized_user_id,
                    exc_info=True,
                )

        allowed_csv = os.getenv("TELEGRAM_ALLOWED_USERS", "").strip()
        if not allowed_csv:
            # Fail-closed: no allowlist means deny by default.
            # The runner auth path in _is_user_authorized() handles
            # GATEWAY_ALLOW_ALL_USERS; this fallback must not silently
            # allow everyone (fixes #24457).
            return os.getenv("GATEWAY_ALLOW_ALL_USERS", "").lower() in {"true", "1", "yes"}
        allowed_ids = {uid.strip() for uid in allowed_csv.split(",") if uid.strip()}
        return "*" in allowed_ids or normalized_user_id in allowed_ids

    @classmethod
    def _metadata_thread_id(cls, metadata: Optional[Dict[str, Any]]) -> Optional[str]:
        if not metadata:
            return None
        thread_id = metadata.get("thread_id") or metadata.get("message_thread_id")
        return str(thread_id) if thread_id is not None else None

    @classmethod
    def _metadata_direct_messages_topic_id(cls, metadata: Optional[Dict[str, Any]]) -> Optional[str]:
        if not metadata:
            return None
        topic_id = metadata.get("direct_messages_topic_id") or metadata.get("telegram_direct_messages_topic_id")
        return str(topic_id) if topic_id is not None else None

    @classmethod
    def _metadata_reply_to_message_id(cls, metadata: Optional[Dict[str, Any]]) -> Optional[int]:
        if not metadata:
            return None
        reply_to = metadata.get("telegram_reply_to_message_id")
        return int(reply_to) if reply_to is not None else None

    @staticmethod
    def _looks_like_private_chat_id(chat_id: str) -> bool:
        try:
            return int(chat_id) > 0
        except (TypeError, ValueError):
            return False

    @classmethod
    def _is_private_dm_topic_send(
        cls,
        chat_id: str,
        thread_id: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> bool:
        if cls._metadata_direct_messages_topic_id(metadata) is not None:
            return bool(
                metadata
                and metadata.get("telegram_dm_topic_reply_fallback")
                and cls._metadata_reply_to_message_id(metadata) is not None
            )
        if metadata and metadata.get("telegram_dm_topic_created_for_send"):
            return False
        return bool(
            thread_id
            and (
                metadata and metadata.get("telegram_dm_topic_reply_fallback")
                or cls._looks_like_private_chat_id(chat_id)
            )
        )

    @staticmethod
    def _dm_topic_missing_anchor_error() -> str:
        return "Telegram DM topic delivery requires a reply anchor; refusing to send outside the requested topic"

    @classmethod
    def _reply_to_message_id_for_send(
        cls,
        reply_to: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
        reply_to_mode: Optional[str] = None,
    ) -> Optional[int]:
        if reply_to:
            return int(reply_to)
        if metadata and metadata.get("telegram_dm_topic_reply_fallback"):
            if reply_to_mode == "off":
                return None
            return cls._metadata_reply_to_message_id(metadata)
        return None

    @classmethod
    def _thread_kwargs_for_send(
        cls,
        chat_id: str,
        thread_id: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
        reply_to_message_id: Optional[int] = None,
        reply_to_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return Telegram send kwargs for forum and direct-message topic routing.

        Supergroup/forum topics use ``message_thread_id``. True Bot API Direct
        Messages topics can opt in with explicit ``direct_messages_topic_id``
        metadata. Hermes-created private-chat topic lanes are marked with
        ``telegram_dm_topic_reply_fallback``. Live replies send the private
        topic thread id together with a reply anchor; synthetic/resumed sends
        without an anchor use ``direct_messages_topic_id`` when metadata has it.
        ``message_thread_id`` alone can render outside the visible lane.

        When ``reply_to_mode`` is ``"off"``, the reply anchor is suppressed for
        DM topic fallback sends while preserving the ``message_thread_id`` so
        the message still lands in the correct topic.
        """
        if metadata and metadata.get("telegram_dm_topic_reply_fallback"):
            if reply_to_mode == "off":
                return {"message_thread_id": cls._message_thread_id_for_send(thread_id)}
            if reply_to_message_id is None:
                reply_to_message_id = cls._metadata_reply_to_message_id(metadata)
            if reply_to_message_id is None:
                direct_topic_id = cls._metadata_direct_messages_topic_id(metadata)
                if direct_topic_id is not None:
                    return {
                        "message_thread_id": None,
                        "direct_messages_topic_id": int(direct_topic_id),
                    }
                return {}
            return {"message_thread_id": cls._message_thread_id_for_send(thread_id)}
        direct_topic_id = cls._metadata_direct_messages_topic_id(metadata)
        if direct_topic_id is not None:
            return {
                "message_thread_id": None,
                "direct_messages_topic_id": int(direct_topic_id),
            }
        return {"message_thread_id": cls._message_thread_id_for_send(thread_id)}

    @classmethod
    def _message_thread_id_for_send(cls, thread_id: Optional[str]) -> Optional[int]:
        if not thread_id or str(thread_id) == cls._GENERAL_TOPIC_THREAD_ID:
            return None
        return int(thread_id)

    @classmethod
    def _message_thread_id_for_typing(cls, thread_id: Optional[str]) -> Optional[int]:
        # Asymmetric with _message_thread_id_for_send on purpose. Telegram's
        # sendMessage and sendChatAction treat thread id "1" (the forum General
        # topic) differently: sends reject message_thread_id=1 and must omit it,
        # but sendChatAction needs message_thread_id=1 to place the typing
        # bubble in the General topic (omitting it hides the bubble entirely
        # from the client's view of that topic). Preserve the real id here —
        # sends still map "1" → None via _message_thread_id_for_send.
        if not thread_id:
            return None
        return int(thread_id)

    @staticmethod
    def _is_thread_not_found_error(error: Exception) -> bool:
        return "thread not found" in str(error).lower()

    @staticmethod
    def _is_bad_request_error(error: Exception) -> bool:
        name = error.__class__.__name__.lower()
        if name == "badrequest" or name.endswith("badrequest"):
            return True
        try:
            from telegram.error import BadRequest
            return isinstance(error, BadRequest)
        except ImportError:
            return False

    @classmethod
    def _should_retry_without_dm_topic_reply_anchor(
        cls,
        error: Exception,
        metadata: Optional[Dict[str, Any]],
        reply_to_message_id: Optional[int],
    ) -> bool:
        """True when a DM-topic send should be retried with routing stripped.

        Two cases trigger the retry:

        1. The original anchor-stale case — the reply target was deleted, so
           Bot API returns "message to be replied not found". The retry drops
           the reply anchor and the topic id together.

        2. The synthetic-event case (added when #27937 introduced
           ``direct_messages_topic_id`` fallback for sends without an anchor):
           if Bot API rejects the topic id itself with any BadRequest that
           mentions topic/thread routing, we retry without routing rather
           than dropping the message.
        """
        if not (metadata and metadata.get("telegram_dm_topic_reply_fallback")):
            return False
        if not cls._is_bad_request_error(error):
            return False
        err_lower = str(error).lower()
        if reply_to_message_id is not None and "message to be replied not found" in err_lower:
            return True
        # Synthetic / resumed sends route via ``direct_messages_topic_id``
        # instead of a reply anchor. If Telegram rejects the topic id, fall
        # back to a plain DM send.
        if metadata.get("direct_messages_topic_id"):
            topic_markers = (
                "direct_messages_topic",
                "message thread not found",
                "thread not found",
                "topic_closed",
                "topic_deleted",
                "topic not found",
            )
            if any(marker in err_lower for marker in topic_markers):
                return True
        return False

    async def _send_with_dm_topic_reply_anchor_retry(
        self,
        send_fn: Any,
        send_kwargs: Dict[str, Any],
        metadata: Optional[Dict[str, Any]],
        reply_to_message_id: Optional[int],
        media_label: str,
        reset_media: Optional[Any] = None,
    ) -> Any:
        """Retry stale private-topic media replies once without the topic anchor."""
        try:
            return await send_fn(**send_kwargs)
        except Exception as send_err:
            if not self._should_retry_without_dm_topic_reply_anchor(
                send_err,
                metadata,
                reply_to_message_id,
            ):
                raise
            logger.warning(
                "[%s] Reply target deleted for Telegram %s, "
                "retrying without reply/topic anchor: %s",
                self.name,
                media_label,
                send_err,
            )
            if reset_media is not None:
                reset_media()
            retry_kwargs = dict(send_kwargs)
            retry_kwargs["reply_to_message_id"] = None
            retry_kwargs.pop("message_thread_id", None)
            retry_kwargs.pop("direct_messages_topic_id", None)
            return await send_fn(**retry_kwargs)

    def _fallback_ips(self) -> list[str]:
        """Return validated fallback IPs from config (populated by _apply_env_overrides)."""
        configured = self.config.extra.get("fallback_ips", []) if getattr(self.config, "extra", None) else []
        if isinstance(configured, str):
            configured = configured.split(",")
        return parse_fallback_ip_env(",".join(str(v) for v in configured) if configured else None)

    @staticmethod
    def _looks_like_polling_conflict(error: Exception) -> bool:
        text = str(error).lower()
        return (
            error.__class__.__name__.lower() == "conflict"
            or "terminated by other getupdates request" in text
            or "another bot instance is running" in text
        )

    @staticmethod
    def _looks_like_network_error(error: Exception) -> bool:
        """Return True for transient network errors that warrant a reconnect attempt."""
        name = error.__class__.__name__.lower()
        if name in {"networkerror", "timedout", "connectionerror"}:
            return True
        try:
            from telegram.error import NetworkError, TimedOut
            if isinstance(error, (NetworkError, TimedOut)):
                return True
        except ImportError:
            pass
        return isinstance(error, OSError)

    @staticmethod
    def _looks_like_connect_timeout(error: Exception) -> bool:
        """Return True when a Telegram TimedOut wraps a connect-timeout.

        A plain Telegram TimedOut may mean the request reached Telegram and
        should not be re-sent. A ConnectTimeout means the TCP connection was
        never established, so retrying is safe and prevents silent drops.
        """
        seen: set[int] = set()
        stack: list[BaseException] = [error]
        while stack:
            cur = stack.pop()
            ident = id(cur)
            if ident in seen:
                continue
            seen.add(ident)
            name = cur.__class__.__name__.lower()
            text = str(cur).lower()
            if "connecttimeout" in name or "connect timeout" in text or "connect timed out" in text:
                return True
            cause = getattr(cur, "__cause__", None)
            context = getattr(cur, "__context__", None)
            if cause is not None:
                stack.append(cause)
            if context is not None:
                stack.append(context)
        return False

    @staticmethod
    def _looks_like_pool_timeout(error: Exception) -> bool:
        """Return True when a Telegram TimedOut wraps an httpx pool timeout.

        PTB converts ``httpx.PoolTimeout`` into ``telegram.error.TimedOut`` with
        a message that explicitly states the request was *not* sent
        (``"Pool timeout: All connections in the connection pool are occupied.
        Request was *not* sent to Telegram."``). Because the request never left
        the process, re-sending is safe and cannot duplicate -- the opposite of
        a generic TimedOut, which may have reached Telegram. We match the
        wrapped ``httpx.PoolTimeout`` class as well as the message string so the
        check survives PTB message-wording changes.
        """
        seen: set[int] = set()
        stack: list[BaseException] = [error]
        while stack:
            cur = stack.pop()
            ident = id(cur)
            if ident in seen:
                continue
            seen.add(ident)
            name = cur.__class__.__name__.lower()
            text = str(cur).lower()
            if "pooltimeout" in name or "pool timeout" in text or (
                "connection pool" in text and "occupied" in text
            ):
                return True
            cause = getattr(cur, "__cause__", None)
            context = getattr(cur, "__context__", None)
            if cause is not None:
                stack.append(cause)
            if context is not None:
                stack.append(context)
        return False

    def _coerce_bool_extra(self, key: str, default: bool = False) -> bool:
        value = self.config.extra.get(key) if getattr(self.config, "extra", None) else None
        if value is None:
            return default
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
            return default
        return bool(value)

    def _link_preview_kwargs(self) -> Dict[str, Any]:
        if not getattr(self, "_disable_link_previews", False):
            return {}
        if LinkPreviewOptions is not None:
            return {"link_preview_options": LinkPreviewOptions(is_disabled=True)}
        return {"disable_web_page_preview": True}

    # ------------------------------------------------------------------
    # Bot API 10.1 Rich Messages (sendRichMessage)
    #
    # Final / new-message replies opportunistically use sendRichMessage with
    # the RAW agent markdown so richer constructs (tables, task lists,
    # collapsible details, math, ...) render natively. The legacy MarkdownV2
    # send() path stays as the fallback for unsupported/oversized content and
    # older PTB/clients. Streaming edits stay on Hermes' existing MarkdownV2
    # edit path for now; finalization can re-send as rich and delete the stale
    # preview until rich_message edit support is wired directly.
    # ------------------------------------------------------------------
    def _content_fits_rich_limits(self, content: str) -> bool:
        """Cheap pre-check for the one hard rich limit we can count locally.

        Only the 32,768 UTF-8 character text cap is enforced here. Other Bot API
        rich limits (500 blocks, 16 nesting levels, 20 table columns, ...) are
        not pre-counted; if exceeded Telegram returns a BadRequest, which
        :meth:`_is_rich_fallback_error` classifies as permanent so the send
        degrades to the legacy chunking path.
        """
        return len(content) <= self.RICH_MESSAGE_MAX_CHARS

    def _bot_supports_rich(self) -> bool:
        """True when the bound bot can issue raw ``sendRichMessage`` calls.

        Gates on ``do_api_request`` being an *async* callable. The real
        ``telegram.Bot.do_api_request`` is a coroutine function; test doubles
        that opt into rich set it to an ``AsyncMock`` (also a coroutine
        function). Plain ``MagicMock`` bots expose a *sync* auto-child and
        ``SimpleNamespace`` bots lack the attribute entirely — both resolve to
        ``False`` here, so the legacy path is used unchanged.
        """
        return inspect.iscoroutinefunction(getattr(self._bot, "do_api_request", None))

    _RICH_DETAILS_RE = re.compile(r"<details\b[^>]*>.*?</details>", re.IGNORECASE | re.DOTALL)
    _RICH_MATH_IN_DETAILS_RE = re.compile(
        r"(\$\$.*?\$\$|"
        r"\\\[.*?\\\]|"
        r"\\\(.*?\\\)|"
        r"\\(?:sum|frac|alpha|beta|gamma|delta|theta|lambda|mu|pi|sigma|"
        r"int|prod|sqrt|lim|infty|begin\{(?:equation|align|matrix|cases)\}))",
        re.IGNORECASE | re.DOTALL,
    )

    def _has_telegram_desktop_details_math_crash_shape(self, content: str) -> bool:
        """Return True for rich-message details+math content that crashes TDesktop.

        Telegram Desktop 6.9.1 can crash while rendering Bot API 10.1 rich
        messages containing math inside a collapsible details block
        (telegramdesktop/tdesktop#30808). The Bot API accepts the payload, so
        Hermes must skip rich delivery up front and use the legacy MarkdownV2
        path until affected Desktop clients age out.
        """
        if not content:
            return False
        for details_block in self._RICH_DETAILS_RE.findall(content):
            if self._RICH_MATH_IN_DETAILS_RE.search(details_block):
                return True
        return False

    def _needs_rich_rendering(self, content: str) -> bool:
        """Return True for markdown constructs that the legacy path degrades.

        Keep ordinary replies on the pre-rich MarkdownV2 path so Telegram
        clients render a consistent font weight/spacing. The rich endpoint is
        reserved for constructs where raw markdown materially improves output:
        pipe tables (MarkdownV2 has no table syntax and rewrites them into
        bullet lists), GFM task lists, collapsible ``<details>`` blocks, and
        block math.  Adapted from #45995 (@YonganZhang).
        """
        if not content:
            return False
        if any(_TABLE_SEPARATOR_RE.match(line) for line in content.splitlines()):
            return True
        if re.search(r"(?m)^\s*[-*]\s+\[[ xX]\]\s+", content):
            return True
        if re.search(r"(?m)^<details\b|^</details>|^<summary\b|^</summary>", content):
            return True
        if "$$" in content:
            return True
        return False

    def _rich_eligible(self, content: str) -> bool:
        """Capability/content eligibility for rich, ignoring ``expect_edits``.

        Shared core of :meth:`_should_attempt_rich` minus the per-call
        ``expect_edits`` metadata gate.  The rich EDIT-finalize path
        (:meth:`_try_edit_rich`) needs this: a streamed preview is sent with
        ``expect_edits=True`` to stay on the editable path mid-stream, but the
        FINAL edit should still upgrade to rich when the content warrants it.
        """
        return bool(
            getattr(self, "_rich_messages_enabled", True)
            and not getattr(self, "_rich_send_disabled", False)
            and content
            and content.strip()
            and self._needs_rich_rendering(content)
            and not self._has_telegram_desktop_details_math_crash_shape(content)
            and self._content_fits_rich_limits(content)
            and self._bot_supports_rich()
        )

    def _should_attempt_rich(
        self, content: str, metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        return bool(
            not (metadata or {}).get("expect_edits")
            and self._rich_eligible(content)
        )

    def prefers_fresh_final_streaming(
        self, content: str, metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Whether to replace a streamed preview with a fresh rich final.

        Disabled for Telegram. The fresh-final path briefly shows two copies of
        the final answer, then deletes the streaming preview after the rich send
        succeeds — it looks like duplicate delivery at the end of every streamed
        turn (the reason #46206 reverted it).  Rich finalize is instead handled
        by editing the existing preview in place via Bot API 10.1's
        ``editMessageText`` ``rich_message`` parameter (see
        :meth:`_try_edit_rich`), so no fresh re-send / delete is needed.
        """
        return False

    def streaming_overflow_limit(self) -> Optional[int]:
        """Allow the stream consumer to accumulate up to the rich-message cap
        before splitting, so a reply that fits one ``sendRichMessage`` /
        ``sendRichMessageDraft`` isn't fragmented at the 4,096 MarkdownV2 limit.

        Gated on the same rich capability as the send path (minus the
        content-length check — raising that cap is the whole point): rich not
        latched off and the bot exposes an async ``do_api_request``.  Returns
        ``None`` (→ legacy 4,096 limit) when rich isn't available, so non-rich
        streams split exactly as before.
        """
        if (
            getattr(self, "_rich_messages_enabled", True)
            and not getattr(self, "_rich_send_disabled", False)
            and self._bot_supports_rich()
        ):
            return self.RICH_MESSAGE_MAX_CHARS
        return None

    def _rich_message_payload(
        self, content: str, *, skip_entity_detection: bool = False
    ) -> Dict[str, Any]:
        """Build the ``InputRichMessage`` object from RAW markdown.

        Never pass ``format_message(content)`` here — that converts to
        MarkdownV2 and would escape/destroy rich syntax like table pipes.
        """
        payload: Dict[str, Any] = {"markdown": content}
        if skip_entity_detection:
            payload["skip_entity_detection"] = True
        return payload

    def _is_rich_capability_error(self, exc: Exception) -> bool:
        """True ⇒ the rich endpoint itself is unavailable (old PTB/server).

        These latch rich off for the rest of the adapter's life — retrying is
        pointless and would cost a failed roundtrip on every send. Per-message
        rejections (BadRequest from a parser/limit issue) are NOT capability
        errors: the next message may be fine.
        """
        name = exc.__class__.__name__.lower()
        if name in {"endpointnotfound", "invalidtoken"}:
            return True
        if isinstance(exc, (AttributeError, TypeError, NotImplementedError)):
            return True
        if getattr(exc, "error_code", None) == 404:
            return True
        s = str(exc).lower()
        if ("method" in s or "endpoint" in s) and (
            "not found" in s or "does not exist" in s
        ):
            return True
        return "no such method" in s

    def _is_rich_fallback_error(self, exc: Exception) -> bool:
        """True ⇒ permanent/capability error ⇒ safe to fall back to legacy.

        Conservative on purpose: only clearly-permanent failures (BadRequest,
        capability errors, unknown/unsupported endpoint) qualify. Everything
        else is treated as transient — the rich request may have reached
        Telegram, so we must NOT legacy-resend and risk a duplicate.
        """
        if self._is_bad_request_error(exc):
            return True
        if self._is_rich_capability_error(exc):
            return True
        s = str(exc).lower()
        return "unsupported" in s or "not implemented" in s

    def _compute_single_send_routing(
        self,
        chat_id: str,
        reply_to: Optional[str],
        metadata: Optional[Dict[str, Any]],
        thread_id: Optional[str],
    ) -> Optional[tuple]:
        """Routing for a single (rich) send — mirrors send()'s index-0 block.

        Returns ``(reply_to_id, thread_kwargs)``, or ``None`` to signal "skip
        rich, let the legacy path handle it" — used for the DM-topic fail-loud
        case so the legacy path stays the single source of the refuse result.
        """
        metadata_reply_to = self._metadata_reply_to_message_id(metadata)
        private_dm_topic_send = self._is_private_dm_topic_send(chat_id, thread_id, metadata)
        dm_topic_reply_to_off = (
            private_dm_topic_send
            and self._reply_to_mode == "off"
            and bool(metadata and metadata.get("telegram_dm_topic_reply_fallback"))
        )
        reply_to_source = reply_to or (
            str(metadata_reply_to)
            if private_dm_topic_send and metadata_reply_to is not None
            else None
        )
        if private_dm_topic_send:
            should_thread = reply_to_source is not None and self._reply_to_mode != "off"
        else:
            should_thread = self._should_thread_reply(reply_to_source, 0)
        reply_to_id = int(reply_to_source) if should_thread and reply_to_source else None
        if private_dm_topic_send and reply_to_id is None and not dm_topic_reply_to_off:
            # Refusing to send outside the requested DM topic — defer to the
            # legacy path, which returns the canonical fail-loud SendResult.
            return None
        thread_kwargs = self._thread_kwargs_for_send(
            chat_id,
            thread_id,
            metadata,
            reply_to_message_id=reply_to_id,
            reply_to_mode=self._reply_to_mode,
        )
        return reply_to_id, thread_kwargs

    async def _try_send_rich(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> Optional[SendResult]:
        """Attempt a single ``sendRichMessage`` send.

        Returns a :class:`SendResult` (success, or a transient failure that the
        caller must NOT legacy-resend), or ``None`` to signal "fall back to the
        legacy MarkdownV2 path" (permanent/capability error or DM-topic skip).
        """
        thread_id = self._metadata_thread_id(metadata)
        routing = self._compute_single_send_routing(chat_id, reply_to, metadata, thread_id)
        if routing is None:
            return None
        reply_to_id, thread_kwargs = routing

        payload: Dict[str, Any] = {
            "chat_id": int(chat_id),
            "rich_message": self._rich_message_payload(content),
        }
        # Only forward non-None routing keys: when direct_messages_topic_id is
        # present _thread_kwargs_for_send pairs it with message_thread_id=None,
        # which must not be sent as a stray field on the raw endpoint.
        payload.update({k: v for k, v in thread_kwargs.items() if v is not None})
        payload.update(self._notification_kwargs(metadata))
        if getattr(self, "_disable_link_previews", False):
            payload["link_preview_options"] = {"is_disabled": True}
        if reply_to_id is not None:
            # Spec: sendRichMessage takes reply_parameters (ReplyParameters
            # object), NOT the legacy reply_to_message_id scalar. Unknown
            # params are silently ignored by the Bot API, so the scalar would
            # quietly drop the reply anchor instead of erroring.
            payload["reply_parameters"] = {"message_id": reply_to_id}

        try:
            # Take the raw Bot API result (dict under real PTB). Passing
            # return_type=Message would make PTB deserialize a Bot API 10.1
            # response shape it does not fully model yet; a post-delivery parse
            # error must not be mistaken for a sendable failure.
            msg = await self._bot.do_api_request(
                "sendRichMessage", api_kwargs=payload
            )
        except Exception as exc:
            if self._is_rich_fallback_error(exc):
                if self._is_rich_capability_error(exc):
                    # Endpoint missing (old PTB/server) — latch rich off so
                    # every later send doesn't pay a doomed extra roundtrip.
                    self._rich_send_disabled = True
                logger.debug(
                    "[%s] sendRichMessage rejected (%s) — falling back to MarkdownV2",
                    self.name, exc,
                )
                return None
            # Transient / network / unknown: the request may have reached
            # Telegram. Do NOT legacy-resend (duplicate risk); surface a
            # failure with retry semantics mirroring the legacy send() except.
            err_str = str(exc).lower()
            try:
                from telegram.error import TimedOut as _TimedOut
            except (ImportError, AttributeError):
                _TimedOut = None
            is_timeout = (_TimedOut and isinstance(exc, _TimedOut)) or "timed out" in err_str
            is_connect_timeout = self._looks_like_connect_timeout(exc)
            logger.warning(
                "[%s] sendRichMessage transient failure (no legacy resend): %s",
                self.name, exc,
            )
            return SendResult(
                success=False,
                error=str(exc),
                retryable=(is_connect_timeout or not is_timeout),
            )

        message_id = None
        if isinstance(msg, dict):
            message_id = msg.get("message_id")
            if message_id is None:
                message_id = (msg.get("result") or {}).get("message_id")
        else:
            message_id = getattr(msg, "message_id", None)
        if message_id is not None:
            # Telegram won't echo rich content in reply_to_message, so remember
            # what we sent — replies to this message resolve via this index.
            try:
                from gateway import rich_sent_store
                rich_sent_store.record(str(chat_id), str(message_id), content)
            except Exception:
                pass
        return SendResult(
            success=True,
            message_id=str(message_id) if message_id is not None else None,
        )

    async def _try_edit_rich(
        self,
        chat_id: str,
        message_id: str,
        content: str,
    ) -> Optional[SendResult]:
        """Edit an existing message in place as a rich message (Bot API 10.1).

        Uses ``editMessageText`` with the ``rich_message`` parameter so a
        streamed preview can finalize as rich (tables/task lists/details/math)
        WITHOUT a fresh send + delete — no duplicate preview.  Mirrors
        :meth:`_try_send_rich`'s error contract:

        - success → ``SendResult(success=True, message_id=...)``
        - permanent / capability error → ``None`` (caller falls back to the
          legacy MarkdownV2 edit; capability errors latch rich off)
        - transient / unknown → ``SendResult(success=False)`` with retry
          semantics (the message may already be edited; do NOT legacy-resend)
        """
        payload: Dict[str, Any] = {
            "chat_id": int(chat_id),
            "message_id": int(message_id),
            "rich_message": self._rich_message_payload(content),
        }
        if getattr(self, "_disable_link_previews", False):
            payload["link_preview_options"] = {"is_disabled": True}
        try:
            # Raw Bot API result; do not request return_type=Message (PTB does
            # not fully model the 10.1 response shape yet — a post-edit parse
            # error must not be mistaken for a failed edit).
            await self._bot.do_api_request("editMessageText", api_kwargs=payload)
        except Exception as exc:
            if self._is_rich_fallback_error(exc):
                if self._is_rich_capability_error(exc):
                    self._rich_send_disabled = True
                # "Message is not modified" — content identical to the current
                # rich message; treat as a successful no-op so the caller does
                # not fall through to a redundant legacy edit.
                if "not modified" in str(exc).lower():
                    return SendResult(success=True, message_id=message_id)
                logger.debug(
                    "[%s] rich editMessageText rejected (%s) — falling back to MarkdownV2 edit",
                    self.name, exc,
                )
                return None
            if "not modified" in str(exc).lower():
                return SendResult(success=True, message_id=message_id)
            err_str = str(exc).lower()
            try:
                from telegram.error import TimedOut as _TimedOut
            except (ImportError, AttributeError):
                _TimedOut = None
            is_timeout = (_TimedOut and isinstance(exc, _TimedOut)) or "timed out" in err_str
            is_connect_timeout = self._looks_like_connect_timeout(exc)
            logger.warning(
                "[%s] rich editMessageText transient failure (no legacy resend): %s",
                self.name, exc,
            )
            return SendResult(
                success=False,
                error=str(exc),
                retryable=(is_connect_timeout or not is_timeout),
            )
        return SendResult(success=True, message_id=message_id)

    def _should_attempt_rich_draft(self, content: str) -> bool:
        return bool(
            getattr(self, "_rich_messages_enabled", True)
            and not getattr(self, "_rich_send_disabled", False)
            and not getattr(self, "_rich_draft_disabled", False)
            and content
            and content.strip()
            and not self._has_telegram_desktop_details_math_crash_shape(content)
            and self._content_fits_rich_limits(content)
            and self._bot_supports_rich()
        )

    async def _try_send_rich_draft(
        self,
        chat_id: str,
        draft_id: int,
        content: str,
        metadata: Optional[Dict[str, Any]],
    ) -> bool:
        """Emit one ``sendRichMessageDraft`` preview frame; True on success.

        Draft frames are ephemeral and overwritten by the next frame / the
        final ``sendRichMessage``, so a duplicate or lost rich draft is
        harmless — any failure simply returns False and the caller renders the
        legacy plain-text draft. A permanent/capability failure additionally
        latches ``_rich_draft_disabled`` so later frames skip the rich attempt.
        """
        payload: Dict[str, Any] = {
            "chat_id": int(chat_id),
            "draft_id": int(draft_id),
            "rich_message": self._rich_message_payload(content),
        }
        thread_id = self._metadata_thread_id(metadata)
        if thread_id is not None:
            payload["message_thread_id"] = int(thread_id)
        try:
            ok = await self._bot.do_api_request("sendRichMessageDraft", api_kwargs=payload)
            return bool(ok)
        except Exception as exc:
            if self._is_rich_capability_error(exc):
                self._rich_draft_disabled = True
                logger.debug(
                    "[%s] sendRichMessageDraft unsupported (%s) — using legacy drafts",
                    self.name, exc,
                )
            else:
                logger.debug(
                    "[%s] sendRichMessageDraft transient failure (%s) — legacy draft this frame",
                    self.name, exc,
                )
            return False

    async def _drain_polling_connections(self) -> None:
        """Reset the httpx connection pool used for getUpdates polling.

        Network errors (especially through proxies like sing-box) can leave
        httpx connections in a half-closed state that still occupy pool slots.
        After enough reconnect cycles the pool fills up entirely, causing
        ``Pool timeout: All connections in the connection pool are occupied.``

        We reset ONLY ``_request[0]`` (the getUpdates request) — the general
        request (``_request[1]``) is left untouched so concurrent
        ``send_message`` / ``edit_message`` calls are never interrupted.

        Implementation note: accesses ``Bot._request[0]`` which is the
        get-updates ``BaseRequest`` in the PTB 22.x internal tuple
        ``(get_updates_request, general_request)``.  There is no public
        accessor for the polling request; review if upgrading to PTB 23+.
        """
        if not (self._app and self._app.bot):
            return
        try:
            # PTB 22.x: _request is a (get_updates, general) tuple;
            # no public accessor exists for the polling request.
            polling_req = self._app.bot._request[0]  # noqa: SLF001
        except Exception:
            return
        try:
            await polling_req.shutdown()
        except Exception:
            logger.debug(
                "[%s] Polling request shutdown failed (non-fatal)",
                self.name, exc_info=True,
            )
        try:
            await polling_req.initialize()
            logger.debug(
                "[%s] Polling request pool drained before reconnect", self.name
            )
        except Exception:
            logger.debug(
                "[%s] Polling request re-initialize failed (non-fatal)",
                self.name, exc_info=True,
            )

    async def _handle_polling_network_error(self, error: Exception) -> None:
        """Reconnect polling after a transient network interruption.

        Triggered by NetworkError/TimedOut in the polling error callback, which
        happen when the host loses connectivity (Mac sleep, WiFi switch, VPN
        reconnect, etc.).  The gateway process stays alive but the long-poll
        connection silently dies; without this handler the bot never recovers.

        Strategy: exponential back-off (5s, 10s, 20s, 40s, 60s cap) up to
        MAX_NETWORK_RETRIES attempts, then mark the adapter retryable-fatal so
        the supervisor restarts the gateway process.
        """
        if self.has_fatal_error:
            return

        MAX_NETWORK_RETRIES = 10
        BASE_DELAY = 5
        MAX_DELAY = 60

        self._polling_network_error_count += 1
        self._send_path_degraded = True
        attempt = self._polling_network_error_count

        if attempt > MAX_NETWORK_RETRIES:
            message = (
                "Telegram polling could not reconnect after %d network error retries. "
                "Restarting gateway." % MAX_NETWORK_RETRIES
            )
            logger.error("[%s] %s Last error: %s", self.name, message, error)
            self._set_fatal_error("telegram_network_error", message, retryable=True)
            await self._notify_fatal_error()
            return

        delay = min(BASE_DELAY * (2 ** (attempt - 1)), MAX_DELAY)
        logger.warning(
            "[%s] Telegram network error (attempt %d/%d), reconnecting in %ds. Error: %s",
            self.name, attempt, MAX_NETWORK_RETRIES, delay, error,
        )
        await asyncio.sleep(delay)

        try:
            if self._app and self._app.updater and self._app.updater.running:
                await self._app.updater.stop()
        except Exception:
            pass

        await self._drain_polling_connections()

        try:
            await self._app.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=False,
                error_callback=self._polling_error_callback_ref,
            )
            logger.info(
                "[%s] Telegram polling resumed after network error (attempt %d)",
                self.name, attempt,
            )
            self._polling_network_error_count = 0
            # start_polling() returning is necessary but not sufficient:
            # PTB's Updater can be left in a state where `running` is True
            # but the underlying long-poll task is wedged on a stale httpx
            # connection and never makes progress. No error_callback fires
            # in that state, so the reconnect ladder won't advance on its
            # own. Schedule a deferred probe to detect the wedge and
            # re-enter the ladder if needed.
            if not self.has_fatal_error:
                probe = asyncio.ensure_future(self._verify_polling_after_reconnect())
                self._background_tasks.add(probe)
                probe.add_done_callback(self._background_tasks.discard)
        except Exception as retry_err:
            logger.warning("[%s] Telegram polling reconnect failed: %s", self.name, retry_err)
            # start_polling failed — polling is dead and no further error
            # callbacks will fire, so schedule the next retry ourselves.
            if not self.has_fatal_error:
                task = asyncio.ensure_future(
                    self._handle_polling_network_error(retry_err)
                )
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)

    async def _verify_polling_after_reconnect(self) -> None:
        """Heartbeat probe scheduled after a successful reconnect.

        PTB's Updater can survive a botched stop()+start_polling() cycle
        with `running=True` but a wedged consumer task. No error callback
        fires, so the reconnect ladder doesn't advance on its own. This
        probe detects the wedge by:

        1. Sleeping HEARTBEAT_PROBE_DELAY so a healthy long-poll has time
           to complete at least one cycle.
        2. Verifying `Updater.running` is still True.
        3. Probing the bot endpoint with a tight asyncio timeout. A
           wedged httpx pool fails this probe; a healthy one returns
           well under the timeout.

        On any failure, re-enter the reconnect ladder so the existing
        MAX_NETWORK_RETRIES path can ultimately escalate to fatal-error.
        """
        HEARTBEAT_PROBE_DELAY = 60
        PROBE_TIMEOUT = 10

        await asyncio.sleep(HEARTBEAT_PROBE_DELAY)

        if self.has_fatal_error:
            return
        if not (self._app and self._app.updater and self._app.updater.running):
            logger.warning(
                "[%s] Updater not running %ds after reconnect — treating as wedged",
                self.name, HEARTBEAT_PROBE_DELAY,
            )
            await self._handle_polling_network_error(
                RuntimeError("Updater not running after reconnect heartbeat")
            )
            return

        try:
            await asyncio.wait_for(self._app.bot.get_me(), PROBE_TIMEOUT)
            self._send_path_degraded = False
        except Exception as probe_err:
            logger.warning(
                "[%s] Polling heartbeat probe failed %ds after reconnect: %s",
                self.name, HEARTBEAT_PROBE_DELAY, probe_err,
            )
            await self._handle_polling_network_error(probe_err)

    async def _handle_polling_conflict(self, error: Exception) -> None:
        if self.has_fatal_error and self.fatal_error_code == "telegram_polling_conflict":
            return
        # Transient 409 Conflict errors arise when the previous gateway process
        # has been killed (e.g. during `hermes update` or `--replace` handoffs)
        # but its long-poll connection hasn't yet expired on Telegram's servers.
        # Telegram holds open getUpdates sessions for up to ~30s after the
        # client disconnects, so a new gateway starting immediately will receive
        # a 409 until that server-side session expires.
        #
        # Strategy: stop the local updater, wait long enough for Telegram's
        # server-side session to expire (RETRY_DELAY grows with each attempt),
        # drain the connection pool, then restart polling.  We attempt this
        # MAX_CONFLICT_RETRIES times before declaring a fatal error.
        #
        # Crucially, a failed retry must NOT leave polling in an ambiguous
        # state.  If start_polling() raises, the updater is neither running
        # nor fatal — messages are silently dropped.  We schedule another
        # retry attempt instead of returning silently, and only escalate to
        # fatal after all retries are exhausted.
        self._polling_conflict_count += 1

        MAX_CONFLICT_RETRIES = 5
        # Delay grows with each attempt: 15s, 25s, 35s, 45s, 55s.
        # Telegram server-side getUpdates sessions typically expire within
        # 30s; the increasing back-off ensures we clear that window without
        # hammering the API on fast-restart loops.
        RETRY_DELAY = 10 + (self._polling_conflict_count * 10)  # seconds

        if self._polling_conflict_count <= MAX_CONFLICT_RETRIES:
            logger.warning(
                "[%s] Telegram polling conflict (%d/%d) — previous session still "
                "held open on Telegram's servers. Waiting %ds for it to expire. "
                "Error: %s",
                self.name, self._polling_conflict_count, MAX_CONFLICT_RETRIES,
                RETRY_DELAY, error,
            )
            # Stop the local updater cleanly before sleeping.  If it's already
            # stopped (e.g. PTB raised before updater.running was set) this is
            # a no-op.
            try:
                if self._app and self._app.updater and self._app.updater.running:
                    await self._app.updater.stop()
            except Exception:
                pass

            await asyncio.sleep(RETRY_DELAY)
            await self._drain_polling_connections()

            try:
                await self._app.updater.start_polling(
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=False,
                    error_callback=self._polling_error_callback_ref,
                )
                logger.info(
                    "[%s] Telegram polling resumed after conflict retry %d/%d",
                    self.name, self._polling_conflict_count, MAX_CONFLICT_RETRIES,
                )
                self._polling_conflict_count = 0  # reset counter on success
                return
            except Exception as retry_err:
                logger.warning(
                    "[%s] Telegram polling retry %d/%d failed: %s. "
                    "Scheduling next attempt.",
                    self.name, self._polling_conflict_count, MAX_CONFLICT_RETRIES,
                    retry_err,
                )
                # Schedule the next retry rather than returning silently.
                # Returning here without either restarting polling or setting
                # a fatal error leaves the adapter in a limbo state: the
                # gateway process is alive and reports "connected" but
                # no messages are received or sent.
                if self._polling_conflict_count < MAX_CONFLICT_RETRIES:
                    # We are inside a running coroutine, so the running loop is
                    # guaranteed to exist. asyncio.get_event_loop() is deprecated
                    # and raises "RuntimeError: There is no current event loop in
                    # thread 'MainThread'" on Python 3.10+ when invoked from a
                    # context without an attached loop (which can happen when PTB
                    # dispatches this error callback). Use get_running_loop().
                    loop = asyncio.get_running_loop()
                    self._polling_error_task = loop.create_task(
                        self._handle_polling_conflict(retry_err)
                    )
                    return
                # Fall through to fatal on the last retry.

        # Exhausted all retries — declare a fatal error so the gateway
        # runner can surface this clearly and the user knows to act.
        message = (
            "Telegram polling could not recover after %d retries (%ds total wait). "
            "The previous gateway session is still held open on Telegram's servers, "
            "or another process is using the same bot token. "
            "To recover: ensure no other Hermes or OpenClaw instance is running "
            "with this token, then restart the gateway with 'hermes gateway restart'."
            % (MAX_CONFLICT_RETRIES, sum(10 + i * 10 for i in range(1, MAX_CONFLICT_RETRIES + 1)))
        )
        logger.error(
            "[%s] %s Original error: %s",
            self.name, message, error,
        )
        self._set_fatal_error("telegram_polling_conflict", message, retryable=False)
        try:
            if self._app and self._app.updater:
                await self._app.updater.stop()
        except Exception as stop_error:
            logger.warning(
                "[%s] Failed stopping Telegram updater after exhausting conflict retries: %s",
                self.name, stop_error, exc_info=True,
            )
        await self._notify_fatal_error()

    async def _create_dm_topic(
        self,
        chat_id: int,
        name: str,
        icon_color: Optional[int] = None,
        icon_custom_emoji_id: Optional[str] = None,
    ) -> Optional[int]:
        """Create a forum topic in a private (DM) chat.

        Uses Bot API 9.4's createForumTopic which now works for 1-on-1 chats.
        Returns the message_thread_id on success, None on failure.
        """
        if not self._bot:
            return None
        try:
            kwargs: Dict[str, Any] = {"chat_id": chat_id, "name": name}
            if icon_color is not None:
                kwargs["icon_color"] = icon_color
            if icon_custom_emoji_id:
                kwargs["icon_custom_emoji_id"] = icon_custom_emoji_id

            topic = await self._bot.create_forum_topic(**kwargs)
            thread_id = topic.message_thread_id
            logger.info(
                "[%s] Created DM topic '%s' in chat %s -> thread_id=%s",
                self.name, name, chat_id, thread_id,
            )
            return thread_id
        except Exception as e:
            error_text = str(e).lower()
            # If topic already exists, try to find it via getForumTopicIconStickers
            # or we just log and skip — Telegram doesn't provide a "list topics" API
            if "topic_name_duplicate" in error_text or "already" in error_text:
                logger.info(
                    "[%s] DM topic '%s' already exists in chat %s (will be mapped from incoming messages)",
                    self.name, name, chat_id,
                )
            elif "not a forum" in error_text or "forums_disabled" in error_text:
                logger.warning(
                    "[%s] Cannot create DM topic '%s' in chat %s: Topics mode is not enabled. "
                    "The user must open the DM with this bot in Telegram, tap the bot name "
                    "at the top, and enable 'Topics' in chat settings before topics can be created.",
                    self.name, name, chat_id,
                )
            else:
                logger.warning(
                    "[%s] Failed to create DM topic '%s' in chat %s: %s",
                    self.name, name, chat_id, e,
                )
            return None

    async def create_handoff_thread(
        self,
        parent_chat_id: str,
        name: str,
    ) -> Optional[str]:
        """Create a forum topic for a session handoff.

        Works for DM topics (Bot API 9.4+, requires user to enable Topics
        in their chat with the bot) and forum supergroups. Returns the
        ``message_thread_id`` as a string, or ``None`` on failure.
        """
        try:
            chat_id_int = int(parent_chat_id)
        except (TypeError, ValueError):
            return None
        thread_id = await self._create_dm_topic(chat_id_int, name=name)
        return str(thread_id) if thread_id else None

    async def ensure_dm_topic(self, chat_id: str, topic_name: str, force_create: bool = False) -> Optional[str]:
        """Return a private DM topic thread id, creating and persisting it if needed."""
        name = str(topic_name or "").strip()
        if not name:
            return None
        try:
            chat_id_int = int(chat_id)
        except (TypeError, ValueError):
            return None

        cache_key = f"{chat_id_int}:{name}"
        cached = self._dm_topics.get(cache_key)
        if cached and not force_create:
            return str(cached)

        topic_conf: Optional[Dict[str, Any]] = None
        chat_entry: Optional[Dict[str, Any]] = None
        for entry in self._dm_topics_config:
            if str(entry.get("chat_id")) != str(chat_id_int):
                continue
            chat_entry = entry
            for candidate in entry.get("topics", []):
                if candidate.get("name") == name:
                    topic_conf = candidate
                    break
            break

        if topic_conf and topic_conf.get("thread_id") and not force_create:
            thread_id = int(topic_conf["thread_id"])
            self._dm_topics[cache_key] = thread_id
            return str(thread_id)

        if chat_entry is None:
            chat_entry = {"chat_id": chat_id_int, "topics": []}
            self._dm_topics_config.append(chat_entry)
        if topic_conf is None:
            topic_conf = {"name": name}
            chat_entry.setdefault("topics", []).append(topic_conf)

        thread_id = await self._create_dm_topic(
            chat_id_int,
            name=name,
            icon_color=topic_conf.get("icon_color"),
            icon_custom_emoji_id=topic_conf.get("icon_custom_emoji_id"),
        )
        if not thread_id:
            return None

        topic_conf["thread_id"] = thread_id
        self._dm_topics[cache_key] = int(thread_id)
        self._persist_dm_topic_thread_id(chat_id_int, name, int(thread_id), replace_existing=force_create)
        return str(thread_id)

    async def rename_dm_topic(
        self,
        chat_id: int,
        thread_id: int,
        name: str,
    ) -> None:
        """Rename a forum topic in a private (DM) chat."""
        if not self._bot:
            return
        try:
            chat_id_arg = int(chat_id)
        except (TypeError, ValueError):
            chat_id_arg = chat_id
        await self._bot.edit_forum_topic(
            chat_id=chat_id_arg,
            message_thread_id=int(thread_id),
            name=name,
        )
        logger.info(
            "[%s] Renamed DM topic in chat %s thread_id=%s -> '%s'",
            self.name, chat_id, thread_id, name,
        )

    def _persist_dm_topic_thread_id(
        self,
        chat_id: int,
        topic_name: str,
        thread_id: int,
        replace_existing: bool = False,
    ) -> None:
        """Save a newly created thread_id back into config.yaml so it persists across restarts."""
        try:
            from hermes_constants import get_hermes_home
            config_path = get_hermes_home() / "config.yaml"
            if not config_path.exists():
                logger.warning("[%s] Config file not found at %s, cannot persist thread_id", self.name, config_path)
                return

            import yaml as _yaml
            with open(config_path, "r", encoding="utf-8") as f:
                config = _yaml.safe_load(f) or {}

            # Navigate to platforms.telegram.extra.dm_topics, creating the path
            # when a named delivery target asks us to create a topic that was
            # not predeclared in config.yaml.
            platforms = config.setdefault("platforms", {})
            telegram_config = platforms.setdefault("telegram", {})
            extra = telegram_config.setdefault("extra", {})
            dm_topics = extra.setdefault("dm_topics", [])

            changed = False
            matching_chat_entry = None
            for chat_entry in dm_topics:
                try:
                    chat_matches = int(chat_entry.get("chat_id", 0)) == int(chat_id)
                except (TypeError, ValueError):
                    chat_matches = False
                if not chat_matches:
                    continue
                matching_chat_entry = chat_entry
                for t in chat_entry.setdefault("topics", []):
                    if t.get("name") == topic_name:
                        if replace_existing or not t.get("thread_id"):
                            if t.get("thread_id") != thread_id:
                                t["thread_id"] = thread_id
                                changed = True
                        break
                else:
                    chat_entry.setdefault("topics", []).append(
                        {"name": topic_name, "thread_id": thread_id}
                    )
                    changed = True
                break

            if matching_chat_entry is None:
                dm_topics.append({
                    "chat_id": chat_id,
                    "topics": [{"name": topic_name, "thread_id": thread_id}],
                })
                changed = True

            if changed:
                fd, tmp_path = tempfile.mkstemp(
                    dir=str(config_path.parent),
                    suffix=".tmp",
                    prefix=".config_",
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        _yaml.dump(config, f, default_flow_style=False, sort_keys=False)
                        f.flush()
                        os.fsync(f.fileno())
                    atomic_replace(tmp_path, config_path)
                except BaseException:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise
                logger.info(
                    "[%s] Persisted thread_id=%s for topic '%s' in config.yaml",
                    self.name, thread_id, topic_name,
                )
        except Exception as e:
            logger.warning("[%s] Failed to persist thread_id to config: %s", self.name, e, exc_info=True)

    async def _setup_dm_topics(self) -> None:
        """Load or create configured DM topics for specified chats.

        Reads config.extra['dm_topics'] — a list of dicts:
        [
            {
                "chat_id": 123456789,
                "topics": [
                    {"name": "General", "icon_color": 7322096, "thread_id": 100},
                    {"name": "Accessibility Auditor", "icon_color": 9367192, "skill": "accessibility-auditor"}
                ]
            }
        ]

        If a topic already has a thread_id in the config (persisted from a previous
        creation), it is loaded into the cache without calling createForumTopic.
        Only topics without a thread_id are created via the API, and their thread_id
        is then saved back to config.yaml for future restarts.
        """
        if not self._dm_topics_config:
            return

        for chat_entry in self._dm_topics_config:
            chat_id = chat_entry.get("chat_id")
            topics = chat_entry.get("topics", [])
            if not chat_id or not topics:
                continue

            logger.info(
                "[%s] Setting up %d DM topic(s) for chat %s",
                self.name, len(topics), chat_id,
            )

            for topic_conf in topics:
                topic_name = topic_conf.get("name")
                if not topic_name:
                    continue

                cache_key = f"{chat_id}:{topic_name}"

                # If thread_id is already persisted in config, just load into cache
                existing_thread_id = topic_conf.get("thread_id")
                if existing_thread_id:
                    self._dm_topics[cache_key] = int(existing_thread_id)
                    logger.info(
                        "[%s] DM topic loaded from config: %s -> thread_id=%s",
                        self.name, cache_key, existing_thread_id,
                    )
                    continue

                # No persisted thread_id — create the topic via API
                icon_color = topic_conf.get("icon_color")
                icon_emoji = topic_conf.get("icon_custom_emoji_id")

                thread_id = await self._create_dm_topic(
                    chat_id=int(chat_id),
                    name=topic_name,
                    icon_color=icon_color,
                    icon_custom_emoji_id=icon_emoji,
                )

                if thread_id:
                    self._dm_topics[cache_key] = thread_id
                    logger.info(
                        "[%s] DM topic cached: %s -> thread_id=%s",
                        self.name, cache_key, thread_id,
                    )
                    # Persist thread_id to config so we don't recreate on next restart
                    self._persist_dm_topic_thread_id(int(chat_id), topic_name, thread_id)

                    # Send a seed message so the topic is visible in Telegram's client.
                    # Empty topics are hidden by the client UI until they contain a message.
                    try:
                        await self._bot.send_message(
                            chat_id=int(chat_id),
                            message_thread_id=thread_id,
                            text=f"\U0001f4cc {topic_name}",
                        )
                    except Exception as seed_err:
                        logger.debug(
                            "[%s] Could not send seed message to topic '%s': %s",
                            self.name, topic_name, seed_err,
                        )

    async def connect(self) -> bool:
        """Connect to Telegram via polling or webhook.

        By default, uses long polling (outbound connection to Telegram).
        If ``TELEGRAM_WEBHOOK_URL`` is set, starts an HTTP webhook server
        instead.  Webhook mode is useful for cloud deployments (Fly.io,
        Railway) where inbound HTTP can wake a suspended machine.

        Env vars for webhook mode::

            TELEGRAM_WEBHOOK_URL    Public HTTPS URL (e.g. https://app.fly.dev/telegram)
            TELEGRAM_WEBHOOK_PORT   Local listen port (default 8443)
            TELEGRAM_WEBHOOK_SECRET Secret token for update verification
        """
        if not TELEGRAM_AVAILABLE:
            logger.error(
                "[%s] python-telegram-bot not installed. Run: pip install python-telegram-bot",
                self.name,
            )
            return False

        if not self.config.token:
            logger.error("[%s] No bot token configured", self.name)
            return False

        try:
            if not self._acquire_platform_lock('telegram-bot-token', self.config.token, 'Telegram bot token'):
                return False

            # Build the application
            builder = Application.builder().token(self.config.token)
            custom_base_url = self.config.extra.get("base_url")
            if custom_base_url:
                builder = builder.base_url(custom_base_url)
                builder = builder.base_file_url(
                    self.config.extra.get("base_file_url", custom_base_url)
                )
                logger.info(
                    "[%s] Using custom Telegram base_url: %s",
                    self.name, custom_base_url,
                )
            # In local-mode telegram-bot-api, file_path is an absolute path on the
            # server's filesystem rather than a relative HTTP path. PTB needs
            # local_mode=True so download_*() reads from disk instead of issuing
            # an HTTP GET that would 404. Requires that the same path is
            # readable by the Hermes process (shared mount, same machine, etc.).
            if self.config.extra.get("local_mode"):
                builder = builder.local_mode(True)
                logger.info("[%s] Using Telegram local_mode (read files from disk)", self.name)

            # PTB defaults (pool_timeout=1s) are too aggressive on flaky networks and
            # can trigger "Pool timeout: All connections in the connection pool are occupied"
            # during reconnect/bootstrap. Use safer defaults and allow env overrides.
            def _env_int(name: str, default: int) -> int:
                try:
                    return int(os.getenv(name, str(default)))
                except (TypeError, ValueError):
                    return default

            def _env_float(name: str, default: float) -> float:
                try:
                    return float(os.getenv(name, str(default)))
                except (TypeError, ValueError):
                    return default

            request_kwargs = {
                "connection_pool_size": _env_int("HERMES_TELEGRAM_HTTP_POOL_SIZE", 512),
                "pool_timeout": _env_float("HERMES_TELEGRAM_HTTP_POOL_TIMEOUT", 8.0),
                "connect_timeout": _env_float("HERMES_TELEGRAM_HTTP_CONNECT_TIMEOUT", 10.0),
                "read_timeout": _env_float("HERMES_TELEGRAM_HTTP_READ_TIMEOUT", 20.0),
                "write_timeout": _env_float("HERMES_TELEGRAM_HTTP_WRITE_TIMEOUT", 20.0),
            }

            disable_fallback = (os.getenv("HERMES_TELEGRAM_DISABLE_FALLBACK_IPS", "").strip().lower() in {"1", "true", "yes", "on"})
            fallback_ips = self._fallback_ips()
            if not fallback_ips:
                fallback_ips = await discover_fallback_ips()
                logger.info(
                    "[%s] Auto-discovered Telegram fallback IPs: %s",
                    self.name,
                    ", ".join(fallback_ips),
                )

            proxy_targets = ["api.telegram.org", *fallback_ips]
            proxy_url = resolve_proxy_url("TELEGRAM_PROXY", target_hosts=proxy_targets)
            if fallback_ips and not proxy_url and not disable_fallback:
                logger.info(
                    "[%s] Telegram fallback IPs active: %s",
                    self.name,
                    ", ".join(fallback_ips),
                )
                # Keep request/update pools separate to reduce contention during
                # polling reconnect + bot API bootstrap/delete_webhook calls.
                request = HTTPXRequest(
                    **request_kwargs,
                    httpx_kwargs={"transport": TelegramFallbackTransport(fallback_ips)},
                )
                get_updates_request = HTTPXRequest(
                    **request_kwargs,
                    httpx_kwargs={"transport": TelegramFallbackTransport(fallback_ips)},
                )
            elif proxy_url:
                logger.info("[%s] Proxy detected; passing explicitly to HTTPXRequest: %s", self.name, proxy_url)
                request = HTTPXRequest(**request_kwargs, proxy=proxy_url)
                get_updates_request = HTTPXRequest(**request_kwargs, proxy=proxy_url)
            else:
                if disable_fallback:
                    logger.info("[%s] Telegram fallback-IP transport disabled via env", self.name)
                request = HTTPXRequest(**request_kwargs)
                get_updates_request = HTTPXRequest(**request_kwargs)

            builder = builder.request(request).get_updates_request(get_updates_request)
            self._app = builder.build()
            self._bot = self._app.bot

            # Register handlers
            self._app.add_handler(TelegramMessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self._handle_text_message
            ))
            self._app.add_handler(TelegramMessageHandler(
                filters.COMMAND,
                self._handle_command
            ))
            self._app.add_handler(TelegramMessageHandler(
                filters.LOCATION | getattr(filters, "VENUE", filters.LOCATION),
                self._handle_location_message
            ))
            self._app.add_handler(TelegramMessageHandler(
                filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.ALL | filters.Sticker.ALL,
                self._handle_media_message
            ))
            # Handle inline keyboard button callbacks (update prompts)
            self._app.add_handler(CallbackQueryHandler(self._handle_callback_query))

            # Start polling — retry initialize() for transient TLS resets
            try:
                from telegram.error import NetworkError, TimedOut
            except ImportError:
                NetworkError = TimedOut = OSError  # type: ignore[misc,assignment]
            _max_connect = 8
            for _attempt in range(_max_connect):
                try:
                    await self._app.initialize()
                    break
                except (NetworkError, TimedOut, OSError) as init_err:
                    if _attempt < _max_connect - 1:
                        wait = min(2 ** _attempt, 15)
                        logger.warning(
                            "[%s] Connect attempt %d/%d failed: %s — retrying in %ds",
                            self.name, _attempt + 1, _max_connect, init_err, wait,
                        )
                        await asyncio.sleep(wait)
                    else:
                        raise
            await self._app.start()

            # Decide between webhook and polling mode
            webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL", "").strip()

            if webhook_url:
                # ── Webhook mode ─────────────────────────────────────
                # Telegram pushes updates to our HTTP endpoint.  This
                # enables cloud platforms (Fly.io, Railway) to auto-wake
                # suspended machines on inbound HTTP traffic.
                #
                # SECURITY: TELEGRAM_WEBHOOK_SECRET is REQUIRED. Without it,
                # python-telegram-bot passes secret_token=None and the
                # webhook endpoint accepts any HTTP POST — attackers can
                # inject forged updates as if from Telegram. Refuse to
                # start rather than silently run in fail-open mode.
                # See GHSA-3vpc-7q5r-276h.
                webhook_port = int(os.getenv("TELEGRAM_WEBHOOK_PORT", "8443"))
                webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
                if not webhook_secret:
                    raise RuntimeError(
                        "TELEGRAM_WEBHOOK_SECRET is required when "
                        "TELEGRAM_WEBHOOK_URL is set. Without it, the "
                        "webhook endpoint accepts forged updates from "
                        "anyone who can reach it — see "
                        "https://github.com/NousResearch/hermes-agent/"
                        "security/advisories/GHSA-3vpc-7q5r-276h.\n\n"
                        "Generate a secret and set it in your .env:\n"
                        "  export TELEGRAM_WEBHOOK_SECRET=\"$(openssl rand -hex 32)\"\n\n"
                        "Then register it with Telegram when setting the "
                        "webhook via setWebhook's secret_token parameter."
                    )
                from urllib.parse import urlparse
                webhook_path = urlparse(webhook_url).path or "/telegram"

                await self._app.updater.start_webhook(
                    listen="0.0.0.0",
                    port=webhook_port,
                    url_path=webhook_path,
                    webhook_url=webhook_url,
                    secret_token=webhook_secret,
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=True,
                )
                self._webhook_mode = True
                logger.info(
                    "[%s] Webhook server listening on 0.0.0.0:%d%s",
                    self.name, webhook_port, webhook_path,
                )
            else:
                # ── Polling mode (default) ───────────────────────────
                # Clear any stale webhook first so polling doesn't inherit a
                # previous webhook registration and silently stop receiving updates.
                delete_webhook = getattr(self._bot, "delete_webhook", None)
                if callable(delete_webhook):
                    await delete_webhook(drop_pending_updates=False)

                loop = asyncio.get_running_loop()

                def _polling_error_callback(error: Exception) -> None:
                    if self._polling_error_task and not self._polling_error_task.done():
                        return
                    if self._looks_like_polling_conflict(error):
                        self._polling_error_task = loop.create_task(self._handle_polling_conflict(error))
                    elif self._looks_like_network_error(error):
                        logger.warning("[%s] Telegram network error, scheduling reconnect: %s", self.name, error)
                        self._polling_error_task = loop.create_task(self._handle_polling_network_error(error))
                    else:
                        logger.error("[%s] Telegram polling error: %s", self.name, error, exc_info=True)

                # Store reference for retry use in _handle_polling_conflict
                self._polling_error_callback_ref = _polling_error_callback

                await self._app.updater.start_polling(
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=True,
                    error_callback=_polling_error_callback,
                )

            # Register bot commands so Telegram shows a hint menu when users type /
            # List is derived from the central COMMAND_REGISTRY — adding a new
            # gateway command there automatically adds it to the Telegram menu.
            try:
                from telegram import (
                    BotCommand,
                    BotCommandScopeAllPrivateChats,
                    BotCommandScopeAllGroupChats,
                    BotCommandScopeDefault,
                )
                from hermes_cli.commands import telegram_menu_commands
                # Telegram allows up to 100 commands but has an undocumented
                # payload size limit (~4KB total).  Limit to 30 core commands
                # to stay well under the threshold while covering all categories.
                menu_commands, hidden_count = telegram_menu_commands(max_commands=MAX_COMMANDS_PER_SCOPE)
                bot_commands = [BotCommand(name, desc) for name, desc in menu_commands]
                # Register for all scopes independently — Telegram picks the
                # narrowest matching scope per chat type (forum topics fall
                # through to AllGroupChats or Default).
                for scope_cls in (BotCommandScopeDefault, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats):
                    scope_name = scope_cls.__name__
                    try:
                        await self._bot.set_my_commands(bot_commands, scope=scope_cls())
                        logger.info("[%s] set_my_commands OK for scope %s (%d cmds)", self.name, scope_name, len(bot_commands))
                    except Exception as scope_err:
                        logger.warning("[%s] set_my_commands FAILED for scope %s: %s", self.name, scope_name, scope_err)
                # Forum topics don't inherit AllGroupChats — Telegram resolves
                # commands via BotCommandScopeChat(chat_id) for forum groups.
                # Lazy registration happens in _ensure_forum_commands on first
                # message from a forum topic (see _handle_text_message).
                if hidden_count:
                    logger.info(
                        "[%s] Telegram menu: %d commands registered, %d hidden (over %d limit). Use /commands for full list.",
                        self.name, len(menu_commands), hidden_count, 30,
                    )
            except Exception as e:
                logger.warning(
                    "[%s] Could not register Telegram command menu: %s",
                    self.name,
                    e,
                    exc_info=True,
                )

            self._mark_connected()
            mode = "webhook" if self._webhook_mode else "polling"
            logger.info("[%s] Connected to Telegram (%s mode)", self.name, mode)

            # Set up DM topics (Bot API 9.4 — Private Chat Topics)
            # Runs after connection is established so the bot can call createForumTopic.
            # Failures here are non-fatal — the bot works fine without topics.
            try:
                await self._setup_dm_topics()
            except Exception as topics_err:
                logger.warning(
                    "[%s] DM topics setup failed (non-fatal): %s",
                    self.name, topics_err, exc_info=True,
                )

            self._start_libre_watch_daemon()

            return True

        except Exception as e:
            self._release_platform_lock()
            message = f"Telegram startup failed: {e}"
            self._set_fatal_error("telegram_connect_error", message, retryable=True)
            logger.error("[%s] Failed to connect to Telegram: %s", self.name, e, exc_info=True)
            return False

    async def disconnect(self) -> None:
        """Stop polling/webhook, cancel pending album flushes, and disconnect."""
        await self._stop_libre_watch_daemon()
        pending_media_group_tasks = list(self._media_group_tasks.values())
        for task in pending_media_group_tasks:
            task.cancel()
        if pending_media_group_tasks:
            await asyncio.gather(*pending_media_group_tasks, return_exceptions=True)
        self._media_group_tasks.clear()
        self._media_group_events.clear()

        if self._app:
            try:
                # Only stop the updater if it's running
                if self._app.updater and self._app.updater.running:
                    await self._app.updater.stop()
                if self._app.running:
                    await self._app.stop()
                await self._app.shutdown()
            except Exception as e:
                logger.warning("[%s] Error during Telegram disconnect: %s", self.name, e, exc_info=True)
        self._release_platform_lock()

        for task in self._pending_photo_batch_tasks.values():
            if task and not task.done():
                task.cancel()
        self._pending_photo_batch_tasks.clear()
        self._pending_photo_batches.clear()

        self._mark_disconnected()
        self._app = None
        self._bot = None
        logger.info("[%s] Disconnected from Telegram", self.name)

    def _should_thread_reply(self, reply_to: Optional[str], chunk_index: int) -> bool:
        """Determine if this message chunk should thread to the original message.

        Args:
            reply_to: The original message ID to reply to
            chunk_index: Index of this chunk (0 = first chunk)

        Returns:
            True if this chunk should be threaded to the original message
        """
        if not reply_to:
            return False
        mode = self._reply_to_mode
        if mode == "off":
            return False
        elif mode == "all":
            return True
        else:  # "first" (default)
            return chunk_index == 0

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> SendResult:
        """Send a message to a Telegram chat."""
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        # getattr() — tests build adapters via object.__new__() (no __init__).
        if getattr(self, "_send_path_degraded", False):
            return SendResult(success=False, error="send_path_degraded", retryable=True)

        # Skip whitespace-only text to prevent Telegram 400 empty-text errors.
        if not content or not content.strip():
            return SendResult(success=True, message_id=None)

        try:
            # Bot API 10.1 rich fast-path: send the raw agent markdown via
            # sendRichMessage so tables/task lists/etc. render natively. Falls
            # through to the legacy MarkdownV2 path on permanent/capability
            # errors or DM-topic routing skips; returns directly on success or
            # on a transient failure (which must NOT be legacy-resent).
            if self._should_attempt_rich(content, metadata=metadata):
                rich_result = await self._try_send_rich(chat_id, content, reply_to, metadata)
                if rich_result is not None:
                    if rich_result.success:
                        # Re-trigger typing like the legacy success path does.
                        try:
                            await self.send_typing(chat_id, metadata=metadata)
                        except Exception:
                            pass  # Typing failures are non-fatal
                    return rich_result

            # Format and split message if needed
            formatted = self.format_message(content)
            chunks = self.truncate_message(
                formatted, self.MAX_MESSAGE_LENGTH, len_fn=utf16_len,
            )
            if len(chunks) > 1:
                # truncate_message appends a raw " (1/2)" suffix. Escape the
                # MarkdownV2-special parentheses so Telegram doesn't reject the
                # chunk and fall back to plain text.
                chunks = [
                    re.sub(r" \((\d+)/(\d+)\)$", r" \\(\1/\2\\)", chunk)
                    for chunk in chunks
                ]

            message_ids = []
            thread_id = self._metadata_thread_id(metadata)
            requested_thread_id = self._message_thread_id_for_send(thread_id)
            used_thread_fallback = False

            try:
                from telegram.error import NetworkError as _NetErr
            except ImportError:
                _NetErr = OSError  # type: ignore[misc,assignment]

            try:
                from telegram.error import BadRequest as _BadReq
            except ImportError:
                _BadReq = None  # type: ignore[assignment,misc]

            try:
                from telegram.error import TimedOut as _TimedOut
            except (ImportError, AttributeError):
                _TimedOut = None  # type: ignore[assignment,misc]

            for i, chunk in enumerate(chunks):
                retried_thread_not_found = False
                metadata_reply_to = self._metadata_reply_to_message_id(metadata)
                private_dm_topic_send = self._is_private_dm_topic_send(chat_id, thread_id, metadata)
                # reply_to_mode="off" on the existing telegram_dm_topic_reply_fallback path
                # is an explicit user opt-in to "message_thread_id alone is enough" (PR #23994
                # / commit 21a15b671). Honor it — don't fail loud just because the anchor was
                # suppressed by config. The new fail-loud contract only applies when the caller
                # didn't ask for the anchor to be dropped.
                dm_topic_reply_to_off = (
                    private_dm_topic_send
                    and self._reply_to_mode == "off"
                    and bool(metadata and metadata.get("telegram_dm_topic_reply_fallback"))
                )
                reply_to_source = reply_to or (
                    str(metadata_reply_to) if private_dm_topic_send and metadata_reply_to is not None else None
                )
                if private_dm_topic_send:
                    should_thread = (
                        reply_to_source is not None
                        and self._reply_to_mode != "off"
                    )
                else:
                    should_thread = self._should_thread_reply(reply_to_source, i)
                reply_to_id = int(reply_to_source) if should_thread and reply_to_source else None
                if private_dm_topic_send and reply_to_id is None and not dm_topic_reply_to_off:
                    return SendResult(
                        success=False,
                        error=self._dm_topic_missing_anchor_error(),
                        retryable=False,
                    )
                thread_kwargs = self._thread_kwargs_for_send(
                    chat_id,
                    thread_id,
                    metadata,
                    reply_to_message_id=reply_to_id,
                    reply_to_mode=self._reply_to_mode,
                )
                if used_thread_fallback and thread_kwargs.get("message_thread_id") is not None:
                    thread_kwargs = dict(thread_kwargs)
                    thread_kwargs["message_thread_id"] = None
                effective_thread_id = thread_kwargs.get("message_thread_id")

                msg = None
                for _send_attempt in range(3):
                    try:
                        # Try Markdown first, fall back to plain text if it fails
                        try:
                            msg = await self._bot.send_message(
                                chat_id=int(chat_id),
                                text=chunk,
                                parse_mode=ParseMode.MARKDOWN_V2,
                                reply_to_message_id=reply_to_id,
                                **thread_kwargs,
                                **self._link_preview_kwargs(),
                                **self._notification_kwargs(metadata),
                            )
                        except Exception as md_error:
                            # Markdown parsing failed, try plain text
                            if "parse" in str(md_error).lower() or "markdown" in str(md_error).lower():
                                logger.warning("[%s] MarkdownV2 parse failed, falling back to plain text: %s", self.name, md_error)
                                plain_chunk = _strip_mdv2(chunk)
                                msg = await self._bot.send_message(
                                    chat_id=int(chat_id),
                                    text=plain_chunk,
                                    parse_mode=None,
                                    reply_to_message_id=reply_to_id,
                                    **thread_kwargs,
                                    **self._link_preview_kwargs(),
                                    **self._notification_kwargs(metadata),
                                )
                            else:
                                raise
                        break  # success
                    except _NetErr as send_err:
                        # BadRequest is a subclass of NetworkError in
                        # python-telegram-bot but represents permanent errors
                        # (not transient network issues). Detect and handle
                        # specific cases instead of blindly retrying.
                        if _BadReq and isinstance(send_err, _BadReq):
                            if self._is_thread_not_found_error(send_err) and effective_thread_id is not None:
                                if private_dm_topic_send or (metadata and metadata.get("telegram_dm_topic_created_for_send")):
                                    return SendResult(
                                        success=False,
                                        error=str(send_err),
                                        retryable=False,
                                    )
                                # Telegram has been observed to return a
                                # one-off "thread not found" that recovers on
                                # an immediate retry (transient flake — see
                                # test_send_retries_transient_thread_not_found_before_fallback).
                                # Try the same thread_id once without sleeping
                                # before falling back to a plain send.
                                if not retried_thread_not_found:
                                    retried_thread_not_found = True
                                    logger.warning(
                                        "[%s] Thread %s not found, retrying once with same thread_id",
                                        self.name, effective_thread_id,
                                    )
                                    continue
                                # Second failure: the thread is genuinely gone.
                                # Retry without ``message_thread_id`` so the
                                # message still reaches the chat.
                                logger.warning(
                                    "[%s] Thread %s not found, retrying without message_thread_id",
                                    self.name, effective_thread_id,
                                )
                                used_thread_fallback = True
                                effective_thread_id = None
                                thread_kwargs = {"message_thread_id": None}
                                continue
                            err_lower = str(send_err).lower()
                            if "message to be replied not found" in err_lower and reply_to_id is not None:
                                if private_dm_topic_send:
                                    return SendResult(
                                        success=False,
                                        error=str(send_err),
                                        retryable=False,
                                    )
                                # Original message was deleted before we
                                # could reply. For private-topic fallback
                                # sends, message_thread_id is only valid with
                                # the reply anchor, so drop both together.
                                logger.warning(
                                    "[%s] Reply target deleted, retrying without reply_to: %s",
                                    self.name, send_err,
                                )
                                reply_to_id = None
                                if metadata and metadata.get("telegram_dm_topic_reply_fallback"):
                                    thread_kwargs = {}
                                    effective_thread_id = None
                                else:
                                    thread_kwargs = self._thread_kwargs_for_send(
                                        chat_id,
                                        thread_id,
                                        metadata,
                                        reply_to_message_id=reply_to_id,
                                        reply_to_mode=self._reply_to_mode,
                                    )
                                    effective_thread_id = thread_kwargs.get("message_thread_id")
                                continue
                            # Other BadRequest errors are permanent — don't retry
                            raise
                        # TimedOut is also a subclass of NetworkError. A
                        # generic timeout may have reached Telegram, so don't
                        # retry; a wrapped ConnectTimeout means no connection
                        # was established, so retrying is safe. A pool timeout
                        # (httpx pool exhausted) is explicitly "not sent to
                        # Telegram" -- retrying through the loop is safe and
                        # prevents silent drops when the pool frees up.
                        if (
                            _TimedOut
                            and isinstance(send_err, _TimedOut)
                            and not self._looks_like_connect_timeout(send_err)
                            and not self._looks_like_pool_timeout(send_err)
                        ):
                            raise
                        if _send_attempt < 2:
                            wait = 2 ** _send_attempt
                            logger.warning("[%s] Network error on send (attempt %d/3), retrying in %ds: %s",
                                           self.name, _send_attempt + 1, wait, send_err)
                            await asyncio.sleep(wait)
                        else:
                            raise
                    except Exception as send_err:
                        retry_after = getattr(send_err, "retry_after", None)
                        if retry_after is not None or "retry after" in str(send_err).lower():
                            if _send_attempt < 2:
                                wait = float(retry_after) if retry_after is not None else 1.0
                                logger.warning(
                                    "[%s] Telegram flood control on send (attempt %d/3), retrying in %.1fs: %s",
                                    self.name,
                                    _send_attempt + 1,
                                    wait,
                                    send_err,
                                )
                                await asyncio.sleep(wait)
                                continue
                        raise
                message_ids.append(str(msg.message_id))

            # Re-trigger typing indicator after sending a message.
            # Telegram clears the typing state when a new message is delivered,
            # so without this the "...typing" bubble disappears mid-response
            # (especially noticeable when the agent sends intermediate progress
            # messages like "Checking:" before running tools).
            try:
                await self.send_typing(chat_id, metadata=metadata)
            except Exception:
                pass  # Typing failures are non-fatal

            return SendResult(
                success=True,
                message_id=message_ids[0] if message_ids else None,
                raw_response={
                    "message_ids": message_ids,
                    "requested_thread_id": requested_thread_id,
                    "thread_fallback": used_thread_fallback,
                },
            )

        except Exception as e:
            logger.error("[%s] Failed to send Telegram message: %s", self.name, e, exc_info=True)
            err_str = str(e).lower()
            # Message too long — content exceeded 4096 chars. Return failure so
            # stream consumer enters fallback mode and sends the remainder.
            if "message_too_long" in err_str or "too long" in err_str:
                logger.debug(
                    "[%s] send() content too long, falling back to new-message continuation",
                    self.name,
                )
                return SendResult(success=False, error="message_too_long")
            # TimedOut usually means the request may have reached Telegram —
            # mark as non-retryable so _send_with_retry() doesn't re-send.
            # Exceptions: a wrapped ConnectTimeout (no connection established)
            # and an httpx pool timeout (request explicitly not sent) -- both
            # are safe to re-send and must not be silently dropped.
            _to = locals().get("_TimedOut")
            is_timeout = (_to and isinstance(e, _to)) or "timed out" in err_str
            is_connect_timeout = self._looks_like_connect_timeout(e)
            is_pool_timeout = self._looks_like_pool_timeout(e)
            return SendResult(success=False, error=str(e), retryable=(is_connect_timeout or is_pool_timeout or not is_timeout))

    async def send_or_update_status(
        self,
        chat_id: str,
        status_key: str,
        content: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send a status message, or edit the previous one with the same key.

        Issue #30045: progress/status callbacks (context-pressure, lifecycle,
        compression, etc.) used to append a fresh bubble on every call. With
        this method, the first call sends and the message id is remembered;
        subsequent calls with the same (chat_id, status_key) edit that same
        message in place. If the edit fails (message deleted, too old, etc.)
        we drop the cached id and send fresh.
        """
        key = (str(chat_id), str(status_key))
        cached_id = self._status_message_ids.get(key)
        if cached_id is not None:
            result = await self.edit_message(
                chat_id, cached_id, content, finalize=True, metadata=metadata,
            )
            if result.success:
                if result.message_id:
                    self._status_message_ids[key] = str(result.message_id)
                return result
            # Edit failed — clear the cached id and fall through to a fresh send.
            self._status_message_ids.pop(key, None)
        result = await self.send(chat_id, content, metadata=metadata)
        if result.success and result.message_id:
            self._status_message_ids[key] = str(result.message_id)
        return result

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        *,
        finalize: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Edit a previously sent Telegram message.

        Telegram caps single-message text at 4096 UTF-16 codeunits.  Streaming
        replies that grow past this limit must NOT be silently truncated and
        must NOT return failure (the consumer would re-send and create a
        duplicate).  Instead this method split-and-delivers: edit the
        existing message with the first chunk and send the rest as
        continuation messages, returning the final chunk's id so subsequent
        edits target the most recent visible message.
        """
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        # Rich finalize (Bot API 10.1): when the completed content has
        # constructs the legacy MarkdownV2 edit degrades (tables → bullet
        # lists, task lists, <details>, block math) and rich is available,
        # edit the preview IN PLACE via editMessageText's rich_message param.
        # No fresh send + delete → no duplicate preview (the problem #46206
        # reverted the fresh-final path for).  Attempted before the 4,096
        # overflow pre-flight because the rich text cap is 32,768 — a rich
        # table that exceeds the MarkdownV2 limit must not be split into legacy
        # chunks.  Falls back to the legacy edit path (overflow split included)
        # on capability/permanent rejection.
        if finalize and self._rich_eligible(content):
            rich_result = await self._try_edit_rich(chat_id, message_id, content)
            if rich_result is not None:
                return rich_result

        # Pre-flight: if content already exceeds the limit, split-and-deliver
        # without round-tripping a doomed edit.
        if utf16_len(content) > self.MAX_MESSAGE_LENGTH:
            return await self._edit_overflow_split(
                chat_id, message_id, content, finalize=finalize, metadata=metadata,
            )

        try:
            if not finalize:
                await self._bot.edit_message_text(
                    chat_id=int(chat_id),
                    message_id=int(message_id),
                    text=content,
                )
                return SendResult(success=True, message_id=message_id)

            formatted = self.format_message(content)
            try:
                await self._bot.edit_message_text(
                    chat_id=int(chat_id),
                    message_id=int(message_id),
                    text=formatted,
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            except Exception as fmt_err:
                # "Message is not modified" is a no-op, not an error
                if "not modified" in str(fmt_err).lower():
                    return SendResult(success=True, message_id=message_id)
                # Fallback: strip MarkdownV2 escapes and retry as clean plain text
                logger.warning(
                    "[%s] MarkdownV2 edit failed, falling back to plain text: %s",
                    self.name,
                    fmt_err,
                )
                _plain = _strip_mdv2(content) if content else content
                await self._bot.edit_message_text(
                    chat_id=int(chat_id),
                    message_id=int(message_id),
                    text=_plain,
                )
            return SendResult(success=True, message_id=message_id)
        except Exception as e:
            err_str = str(e).lower()
            # "Message is not modified" — content identical, treat as success
            if "not modified" in err_str:
                return SendResult(success=True, message_id=message_id)
            # Reactive split-and-deliver: parse_mode formatting can inflate
            # the payload past the limit even when the raw text was under
            # (e.g. MarkdownV2 escapes).  Same fix as the pre-flight path.
            if "message_too_long" in err_str or "too long" in err_str:
                logger.debug(
                    "[%s] edit_message overflow (%d UTF-16 > %d), splitting",
                    self.name, utf16_len(content), self.MAX_MESSAGE_LENGTH,
                )
                return await self._edit_overflow_split(
                    chat_id, message_id, content, finalize=finalize, metadata=metadata,
                )
            # Flood control / RetryAfter — short waits are retried inline,
            # long waits return a failure immediately so streaming can fall back
            # to a normal final send instead of leaving a truncated partial.
            retry_after = getattr(e, "retry_after", None)
            if retry_after is not None or "retry after" in err_str:
                wait = retry_after if retry_after else 1.0
                logger.warning(
                    "[%s] Telegram flood control, waiting %.1fs",
                    self.name, wait,
                )
                if wait > 5.0:
                    return SendResult(success=False, error=f"flood_control:{wait}")
                await asyncio.sleep(wait)
                try:
                    await self._bot.edit_message_text(
                        chat_id=int(chat_id),
                        message_id=int(message_id),
                        text=content,
                    )
                    return SendResult(success=True, message_id=message_id)
                except Exception as retry_err:
                    logger.error(
                        "[%s] Edit retry failed after flood wait: %s",
                        self.name, retry_err,
                    )
                    return SendResult(success=False, error=str(retry_err))
            # Transient network errors (ConnectError, timeouts, server
            # disconnects) should not permanently disable progress-message
            # editing.  Mark the result retryable so the caller knows it
            # can keep trying on the next update cycle.
            _transient_markers = (
                "connecterror",
                "connect error",
                "connection error",
                "networkerror",
                "network error",
                "timed out",
                "readtimeout",
                "writetimeout",
                "server disconnected",
                "temporarily unavailable",
                "temporary failure",
                "httpx",
            )
            _is_transient = any(m in err_str for m in _transient_markers)
            if _is_transient:
                logger.warning(
                    "[%s] Transient network error editing message %s (will retry): %s",
                    self.name,
                    message_id,
                    e,
                )
                return SendResult(success=False, error=str(e), retryable=True)
            logger.error(
                "[%s] Failed to edit Telegram message %s: %s",
                self.name,
                message_id,
                e,
                exc_info=True,
            )
            return SendResult(success=False, error=str(e))

    async def _edit_overflow_split(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        *,
        finalize: bool,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Split an oversized edit across the existing message + continuations.

        Edit the original ``message_id`` with chunk 1 (with the platform's
        usual ``(1/N)`` suffix preserved), then send the remaining chunks as
        new messages threaded as replies to the previous chunk so the user
        sees them grouped.  Returns ``SendResult(success=True,
        message_id=<last-chunk-id>, continuation_message_ids=(...))`` so the
        stream consumer can keep editing the most recent visible message
        and the gateway has full visibility into every message id we put on
        screen.

        Falls back to ``SendResult(success=False)`` only if even the first-
        chunk edit fails — that's a real adapter problem, not an overflow.
        """
        chunks = self.truncate_message(
            content, self.MAX_MESSAGE_LENGTH, len_fn=utf16_len,
        )
        if len(chunks) <= 1:
            # Defensive: shouldn't happen given the caller's pre-flight, but
            # if truncate_message returned a single chunk just edit normally.
            chunks = [content]

        # Step 1 — edit the existing message with the first chunk.
        first_chunk = chunks[0]
        try:
            if finalize:
                # Use format_message + parse_mode for the final chunk;
                # mirror edit_message's main happy-path.
                formatted = self.format_message(first_chunk)
                try:
                    await self._bot.edit_message_text(
                        chat_id=int(chat_id),
                        message_id=int(message_id),
                        text=formatted,
                        parse_mode=ParseMode.MARKDOWN_V2,
                    )
                except Exception as fmt_err:
                    if "not modified" not in str(fmt_err).lower():
                        logger.warning(
                            "[%s] Overflow split: MarkdownV2 first-chunk edit "
                            "failed, falling back to plain text: %s",
                            self.name, fmt_err,
                        )
                        await self._bot.edit_message_text(
                            chat_id=int(chat_id),
                            message_id=int(message_id),
                            text=_strip_mdv2(first_chunk),
                        )
            else:
                await self._bot.edit_message_text(
                    chat_id=int(chat_id),
                    message_id=int(message_id),
                    text=first_chunk,
                )
        except Exception as e:
            err_str = str(e).lower()
            if "not modified" in err_str:
                # First chunk identical to current text — fall through to
                # send continuations.
                pass
            else:
                logger.error(
                    "[%s] Overflow split: first-chunk edit failed: %s",
                    self.name, e, exc_info=True,
                )
                return SendResult(success=False, error=str(e))

        # Step 2 — send each remaining chunk as a continuation message,
        # threaded as a reply to the previous so the user sees them as a
        # contiguous block.  We call self._bot.send_message directly so the
        # continuation skips ``self.send``'s own pre-chunking pass (chunks
        # are already correctly sized).  Best-effort MarkdownV2 with plain
        # fallback, mirroring send().
        continuation_ids: list[str] = []
        delivered_chunks = [first_chunk]
        prev_id = message_id
        thread_id = self._metadata_thread_id(metadata)
        for chunk in chunks[1:]:
            sent_msg = None
            reply_to_id = int(prev_id) if prev_id else None
            thread_kwargs = self._thread_kwargs_for_send(
                chat_id,
                thread_id,
                metadata,
                reply_to_message_id=reply_to_id,
            )
            for use_markdown in (True, False) if finalize else (False,):
                try:
                    if use_markdown:
                        text = self.format_message(chunk)
                    else:
                        # Plain attempt: on finalize the MarkdownV2 attempt
                        # failed, so degrade to clean stripped text, never
                        # the raw chunk (raw ** / ``` markers would render
                        # literally); streaming previews stay raw.
                        text = _strip_mdv2(chunk) if finalize else chunk
                    sent_msg = await self._bot.send_message(
                        chat_id=int(chat_id),
                        text=text,
                        parse_mode=ParseMode.MARKDOWN_V2 if use_markdown else None,
                        reply_to_message_id=reply_to_id,
                        **thread_kwargs,
                        **self._link_preview_kwargs(),
                        **self._notification_kwargs(metadata),
                    )
                    break
                except Exception as send_err:
                    if "reply message not found" in str(send_err).lower():
                        # Drop the reply anchor and try again.  Private DM
                        # topic fallback needs the anchor and topic id together;
                        # forum topics can still safely keep message_thread_id.
                        retry_thread_kwargs = (
                            {}
                            if metadata and metadata.get("telegram_dm_topic_reply_fallback")
                            else self._thread_kwargs_for_send(
                                chat_id, thread_id, metadata, reply_to_message_id=None
                            )
                        )
                        try:
                            sent_msg = await self._bot.send_message(
                                chat_id=int(chat_id),
                                text=_strip_mdv2(chunk) if finalize else chunk,
                                **retry_thread_kwargs,
                                **self._link_preview_kwargs(),
                                **self._notification_kwargs(metadata),
                            )
                            break
                        except Exception as _retry_err:
                            logger.warning(
                                "[%s] Overflow continuation no-reply retry failed: %s",
                                self.name, _retry_err,
                            )
                            sent_msg = None
                            break
                    if use_markdown:
                        # try plain text on next loop iteration
                        continue
                    logger.warning(
                        "[%s] Overflow continuation send failed: %s",
                        self.name, send_err,
                    )
                    sent_msg = None
                    break
            if sent_msg is None:
                # Continuation failed — the user has chunk 1 + however many
                # continuations succeeded, but NOT the full response.  Do not
                # report success: the stream consumer treats a successful edit
                # as final delivery on got_done, which would suppress fallback
                # delivery and leave the Telegram topic clipped after the last
                # delivered chunk.
                logger.warning(
                    "[%s] Overflow split: stopped at %d/%d chunks delivered",
                    self.name, 1 + len(continuation_ids), len(chunks),
                )
                delivered_prefix = "".join(
                    re.sub(r" \(\d+/\d+\)$", "", delivered)
                    for delivered in delivered_chunks
                )
                return SendResult(
                    success=False,
                    message_id=prev_id,
                    error="overflow_continuation_failed",
                    retryable=True,
                    raw_response={
                        "partial_overflow": True,
                        "delivered_chunks": 1 + len(continuation_ids),
                        "total_chunks": len(chunks),
                        "last_message_id": prev_id,
                        "delivered_prefix": delivered_prefix,
                        "continuation_message_ids": tuple(continuation_ids),
                    },
                    continuation_message_ids=tuple(continuation_ids),
                )
            new_id = str(getattr(sent_msg, "message_id", "")) or prev_id
            continuation_ids.append(new_id)
            delivered_chunks.append(chunk)
            prev_id = new_id

        last_id = continuation_ids[-1] if continuation_ids else message_id
        logger.debug(
            "[%s] Overflow split delivered %d chunks; last_id=%s",
            self.name, 1 + len(continuation_ids), last_id,
        )
        return SendResult(
            success=True,
            message_id=last_id,
            continuation_message_ids=tuple(continuation_ids),
        )

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        """Delete a previously sent Telegram message.

        Used by the stream consumer's fresh-final cleanup path (ported
        from openclaw/openclaw#72038) to remove long-lived preview
        messages after sending the completed reply as a fresh message.
        Telegram's Bot API ``deleteMessage`` works for bot-posted
        messages in the last 48 hours.  Failures are non-fatal — the
        caller leaves the preview in place and logs at debug level.
        """
        if not self._bot:
            return False
        try:
            await self._bot.delete_message(
                chat_id=int(chat_id),
                message_id=int(message_id),
            )
            return True
        except Exception as e:
            logger.debug(
                "[%s] Failed to delete Telegram message %s: %s",
                self.name, message_id, e,
            )
            return False

    def supports_draft_streaming(
        self,
        chat_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Telegram supports sendMessageDraft for private chats only.

        Bot API 9.5 (March 2026) opened ``sendMessageDraft`` to all bots
        unconditionally for private (DM) chats.  Groups, supergroups, and
        channels still rely on the edit-based path.

        We additionally require ``self._bot`` to expose ``send_message_draft``
        (added to python-telegram-bot in 22.6); older PTB installs gracefully
        fall back to the edit path even on DMs.
        """
        if not self._bot or not hasattr(self._bot, "send_message_draft"):
            return False
        return (chat_type or "").lower() in {"dm", "private"}

    async def send_draft(
        self,
        chat_id: str,
        draft_id: int,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Stream a partial message via Telegram's native draft API.

        Uses ``sendRichMessageDraft`` (Bot API 10.1) with the raw markdown when
        rich messages are enabled and supported, otherwise the plain-text
        ``sendMessageDraft``. The Bot API animates the preview when the same
        ``draft_id`` is reused across consecutive calls in the same chat.  When
        the response finishes, the caller sends the final text via the normal
        ``send`` path; the draft preview clears naturally on the client
        (Telegram has no Bot API to "promote" a draft to a real message — the
        final ``sendMessage``/``sendRichMessage`` is what the user receives in
        their history).
        """
        if not self._bot:
            return SendResult(success=False, error="not_connected")

        # Rich draft fast-path (Bot API 10.1 sendRichMessageDraft): render the
        # streaming preview with the same raw markdown the final
        # sendRichMessage will persist, so the animated draft matches the final
        # message. Any failure degrades to the legacy plain-text draft below.
        if self._should_attempt_rich_draft(content):
            if await self._try_send_rich_draft(chat_id, draft_id, content, metadata):
                # Drafts have no message_id; report success without one.
                return SendResult(success=True, message_id=None)

        if not hasattr(self._bot, "send_message_draft"):
            return SendResult(success=False, error="api_unavailable")

        # Trim to the same UTF-16 budget the platform enforces on regular
        # sends.  Drafts have the same length contract as messages.
        text = content if len(content) <= self.MAX_MESSAGE_LENGTH else \
            self.truncate_message(content, self.MAX_MESSAGE_LENGTH, len_fn=utf16_len)[0]

        thread_id = self._metadata_thread_id(metadata)

        # Apply the same MarkdownV2 conversion the regular ``send`` path uses
        # so the animated draft preview renders with identical formatting to
        # the final message.  Without this, the draft streams as raw text and
        # the final ``sendMessage`` (which DOES use MarkdownV2) snaps into
        # formatted output, producing a jarring visual shift at the end of the
        # response.  We try MarkdownV2 first and fall back to plain text if a
        # malformed escape would be rejected — mirroring the (True, False)
        # retry the streaming send loop uses — so a single bad token never
        # kills draft streaming for the whole response.
        for use_markdown in (True, False):
            kwargs: Dict[str, Any] = {
                "chat_id": int(chat_id),
                "draft_id": int(draft_id),
                "text": self.format_message(text) if use_markdown else text,
            }
            if use_markdown:
                kwargs["parse_mode"] = ParseMode.MARKDOWN_V2
            if thread_id is not None:
                kwargs["message_thread_id"] = thread_id

            try:
                ok = await self._bot.send_message_draft(**kwargs)
                if ok:
                    # Drafts have no message_id; we report success without one
                    # so the caller knows the animation frame landed.
                    return SendResult(success=True, message_id=None)
                return SendResult(success=False, error="draft_rejected")
            except Exception as e:
                # A MarkdownV2 parse failure (BadRequest "can't parse entities")
                # is recoverable: retry once as plain text.  Any other failure
                # (chat doesn't allow drafts, transient hiccup) — or a failure
                # on the plain-text attempt — propagates to the caller, which
                # treats it as "fall back to edit-based for this response".
                if use_markdown and self._is_bad_request_error(e):
                    logger.debug(
                        "[%s] sendMessageDraft MarkdownV2 rejected, retrying "
                        "as plain text (chat=%s draft_id=%s): %s",
                        self.name, chat_id, draft_id, e,
                    )
                    continue
                logger.debug(
                    "[%s] sendMessageDraft failed (chat=%s draft_id=%s): %s",
                    self.name, chat_id, draft_id, e,
                )
                return SendResult(success=False, error=str(e))

        return SendResult(success=False, error="draft_rejected")

    async def _send_message_with_thread_fallback(self, **kwargs):
        """Send a Telegram message, retrying once without message_thread_id
        if Telegram returns 'Message thread not found'.

        Used for control-style sends (approval prompts, model picker,
        update prompts) that can carry a stale thread_id from a DM
        reply chain.  The streaming send loop has its own equivalent
        (PR #3390) at the body of ``send``; this helper applies the
        same retry pattern to the non-streaming control paths.
        """
        if not self._bot:
            raise RuntimeError("Not connected")

        message_thread_id = kwargs.get("message_thread_id")
        try:
            return await self._bot.send_message(**kwargs)
        except Exception as send_err:
            if (
                message_thread_id is not None
                and self._is_bad_request_error(send_err)
                and self._is_thread_not_found_error(send_err)
            ):
                logger.warning(
                    "[%s] Thread %s not found for control message, retrying without message_thread_id",
                    self.name,
                    message_thread_id,
                )
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop("message_thread_id", None)
                return await self._bot.send_message(**retry_kwargs)
            raise

    async def send_update_prompt(
        self, chat_id: str, prompt: str, default: str = "",
        session_key: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send an inline-keyboard update prompt (Yes / No buttons).

        Used by the gateway ``/update`` watcher when ``hermes update --gateway``
        needs user input (stash restore, config migration).
        """
        if not self._bot:
            return SendResult(success=False, error="Not connected")
        try:
            default_hint = f" (default: {default})" if default else ""
            text = self.format_message(f"⚕ *Update needs your input:*\n\n{prompt}{default_hint}")
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✓ Yes", callback_data="update_prompt:y"),
                    InlineKeyboardButton("✗ No", callback_data="update_prompt:n"),
                ]
            ])
            thread_id = self._metadata_thread_id(metadata)
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
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.warning("[%s] send_update_prompt failed: %s", self.name, e)
            return SendResult(success=False, error=str(e))

    async def send_exec_approval(
        self, chat_id: str, command: str, session_key: str,
        description: str = "dangerous command",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send an inline-keyboard approval prompt with interactive buttons.

        The buttons call ``resolve_gateway_approval()`` to unblock the waiting
        agent thread — same mechanism as the text ``/approve`` flow.
        """
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        try:
            cmd_preview = command[:3800] + "..." if len(command) > 3800 else command
            text = (
                f"⚠️ <b>Command Approval Required</b>\n\n"
                f"<pre>{_html.escape(cmd_preview)}</pre>\n\n"
                f"Reason: {_html.escape(description)}"
            )

            # Resolve thread context for thread replies
            thread_id = self._metadata_thread_id(metadata)

            # We'll use the message_id as part of callback_data to look up session_key
            # Send a placeholder first, then update — or use a counter.
            # Simpler: use a monotonic counter to generate short IDs.
            import itertools
            if not hasattr(self, "_approval_counter"):
                self._approval_counter = itertools.count(1)
            approval_id = next(self._approval_counter)

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Allow Once", callback_data=f"ea:once:{approval_id}"),
                    InlineKeyboardButton("✅ Session", callback_data=f"ea:session:{approval_id}"),
                ],
                [
                    InlineKeyboardButton("✅ Always", callback_data=f"ea:always:{approval_id}"),
                    InlineKeyboardButton("❌ Deny", callback_data=f"ea:deny:{approval_id}"),
                ],
            ])

            kwargs: Dict[str, Any] = {
                "chat_id": int(chat_id),
                "text": text,
                "parse_mode": ParseMode.HTML,
                "reply_markup": keyboard,
                **self._link_preview_kwargs(),
            }
            reply_to_id = self._reply_to_message_id_for_send(None, metadata, reply_to_mode=self._reply_to_mode)
            kwargs["reply_to_message_id"] = reply_to_id
            kwargs.update(
                self._thread_kwargs_for_send(
                    chat_id,
                    thread_id,
                    metadata,
                    reply_to_message_id=reply_to_id,
                    reply_to_mode=self._reply_to_mode
                )
            )

            msg = await self._send_message_with_thread_fallback(**kwargs)

            # Store session_key keyed by approval_id for the callback handler
            self._approval_state[approval_id] = session_key

            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.warning("[%s] send_exec_approval failed: %s", self.name, e)
            return SendResult(success=False, error=str(e))

    async def send_slash_confirm(
        self, chat_id: str, title: str, message: str, session_key: str,
        confirm_id: str, metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Render a three-button slash-command confirmation prompt."""
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        try:
            preview = self.format_message(message if len(message) <= 3800 else message[:3800] + "...")

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Approve Once", callback_data=f"sc:once:{confirm_id}"),
                    InlineKeyboardButton("🔒 Always Approve", callback_data=f"sc:always:{confirm_id}"),
                ],
                [
                    InlineKeyboardButton("❌ Cancel", callback_data=f"sc:cancel:{confirm_id}"),
                ],
            ])

            thread_id = self._metadata_thread_id(metadata)
            kwargs: Dict[str, Any] = {
                "chat_id": int(chat_id),
                "text": preview,
                "parse_mode": ParseMode.MARKDOWN_V2,
                "reply_markup": keyboard,
                **self._link_preview_kwargs(),
            }
            reply_to_id = self._reply_to_message_id_for_send(None, metadata, reply_to_mode=self._reply_to_mode)
            kwargs["reply_to_message_id"] = reply_to_id
            kwargs.update(
                self._thread_kwargs_for_send(
                    chat_id,
                    thread_id,
                    metadata,
                    reply_to_message_id=reply_to_id,
                    reply_to_mode=self._reply_to_mode
                )
            )

            msg = await self._send_message_with_thread_fallback(**kwargs)
            self._slash_confirm_state[confirm_id] = session_key
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.warning("[%s] send_slash_confirm failed: %s", self.name, e)
            return SendResult(success=False, error=str(e))

    async def send_clarify(
        self,
        chat_id: str,
        question: str,
        choices: Optional[list],
        clarify_id: str,
        session_key: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Render a clarify prompt with one inline button per choice.

        Multi-choice mode (``choices`` non-empty): renders one button per
        option plus a final "✏️ Other (type answer)" button.  Picking the
        "Other" button flips the entry into text-capture mode so the next
        message becomes the response.

        Open-ended mode (``choices`` empty): renders the question as plain
        text — no buttons.  The next message in the session is captured by
        the gateway's text-intercept and resolves the clarify.
        """
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        try:
            text = f"❓ {_html.escape(question)}"
            thread_id = self._metadata_thread_id(metadata)

            if choices:
                # Render full option text in the message body so mobile
                # users can read long choices that would be truncated in
                # inline button labels.  Buttons keep short numeric labels
                # (1, 2, …, Other) to avoid Telegram truncation.
                option_lines = "\n".join(
                    f"{i + 1}. {_html.escape(str(c))}"
                    for i, c in enumerate(choices)
                )
                text += f"\n\n{option_lines}"

            kwargs: Dict[str, Any] = {
                "chat_id": int(chat_id),
                "text": text,
                "parse_mode": ParseMode.HTML,
                **self._link_preview_kwargs(),
            }

            if choices:
                # Telegram caps callback_data at 64 bytes; keep "cl:<id>:<idx>"
                # short.
                rows = []
                for idx in range(len(choices)):
                    rows.append([
                        InlineKeyboardButton(
                            str(idx + 1),
                            callback_data=f"cl:{clarify_id}:{idx}",
                        )
                    ])
                rows.append([
                    InlineKeyboardButton(
                        "✏️ Other (type answer)",
                        callback_data=f"cl:{clarify_id}:other",
                    )
                ])
                kwargs["reply_markup"] = InlineKeyboardMarkup(rows)

            reply_to_id = self._reply_to_message_id_for_send(None, metadata)
            kwargs["reply_to_message_id"] = reply_to_id
            kwargs.update(
                self._thread_kwargs_for_send(
                    chat_id,
                    thread_id,
                    metadata,
                    reply_to_message_id=reply_to_id,
                )
            )

            msg = await self._send_message_with_thread_fallback(**kwargs)
            self._clarify_state[clarify_id] = session_key
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.warning("[%s] send_clarify failed: %s", self.name, e)
            return SendResult(success=False, error=str(e))

    async def send_model_picker(
        self,
        chat_id: str,
        providers: list,
        current_model: str,
        current_provider: str,
        session_key: str,
        on_model_selected,
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

    async def _handle_model_picker_callback(
        self, query, data: str, chat_id: str
    ) -> None:
        """Handle model picker inline keyboard callbacks (mp:/mm:/mc:/mb:/mx:/mg:)."""
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
            # --- Expensive model confirmed: perform the switch ---
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
            callback = state.get("on_model_selected")

            if not callback:
                await query.answer(text="Picker expired.")
                return

            switch_failed = False
            try:
                result_text = await callback(chat_id, model_id, provider_slug)
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
                    await query.edit_message_text(
                        text=result_text,
                        parse_mode=None,
                        reply_markup=None,
                    )
                except Exception:
                    pass
            await query.answer(
                text="Switch failed." if switch_failed else "Model switched!"
            )
            self._model_picker_state.pop(chat_id, None)

        elif data.startswith("mm:"):
            # --- Model selected: perform the switch ---
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
            callback = state.get("on_model_selected")

            if not callback:
                await query.answer(text="Picker expired.")
                return

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

            switch_failed = False
            try:
                result_text = await callback(chat_id, model_id, provider_slug)
            except Exception as exc:
                logger.error("Model picker switch failed: %s", exc)
                result_text = f"Error switching model: {exc}"
                switch_failed = True

            # Edit message to show confirmation, remove buttons
            try:
                await query.edit_message_text(
                    text=self.format_message(result_text),
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=None,
                )
            except Exception:
                # Markdown parse failure — retry as plain text
                try:
                    await query.edit_message_text(
                        text=result_text,
                        parse_mode=None,
                        reply_markup=None,
                    )
                except Exception:
                    pass
            await query.answer(
                text="Switch failed." if switch_failed else "Model switched!"
            )

            # Clean up state
            self._model_picker_state.pop(chat_id, None)

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

    async def _handle_callback_query(
        self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"
    ) -> None:
        """Handle inline keyboard button clicks."""
        query = update.callback_query
        if not query or not query.data:
            return
        data = query.data
        query_message = getattr(query, "message", None)
        query_chat_id = getattr(query_message, "chat_id", None)
        query_chat = getattr(query_message, "chat", None)
        query_chat_type = getattr(query_chat, "type", None)
        query_thread_id = getattr(query_message, "message_thread_id", None)
        query_user_name = getattr(query.from_user, "first_name", None)

        # --- /models combined picker (msc:*) ---
        if data.startswith("msc:"):
            caller_id = str(getattr(query.from_user, "id", ""))
            if not self._is_callback_user_authorized(
                caller_id,
                chat_id=query_chat_id,
                chat_type=str(query_chat_type) if query_chat_type is not None else None,
                thread_id=str(query_thread_id) if query_thread_id is not None else None,
                user_name=query_user_name,
            ):
                await query.answer(text="⛔ Tu n'es pas autorisé à utiliser ce bouton.")
                return
            await self._handle_models_config_callback(
                query, data, str(query_chat_id or "")
            )
            return

        # --- Repo Cockpit model/reasoning quick picks (rcp:*) ---
        if data.startswith("rcp:"):
            caller_id = str(getattr(query.from_user, "id", ""))
            if not self._is_callback_user_authorized(
                caller_id,
                chat_id=query_chat_id,
                chat_type=str(query_chat_type) if query_chat_type is not None else None,
                thread_id=str(query_thread_id) if query_thread_id is not None else None,
                user_name=query_user_name,
            ):
                await query.answer(text="⛔ Tu n'es pas autorisé à utiliser ce bouton.")
                return
            parts = data.split(":")
            mode = normalize_cockpit_mode(parts[3] if len(parts) > 3 else None)
            await self._handle_cockpit_prefs_callback(query, data, caller_id, mode)
            return

        # --- Repo Cockpit new-chat callbacks (rcn:verb:mode) ---
        if data.startswith("rcn:"):
            caller_id = str(getattr(query.from_user, "id", ""))
            if not self._is_callback_user_authorized(
                caller_id,
                chat_id=query_chat_id,
                chat_type=str(query_chat_type) if query_chat_type is not None else None,
                thread_id=str(query_thread_id) if query_thread_id is not None else None,
                user_name=query_user_name,
            ):
                await query.answer(text="⛔ Tu n'es pas autorisé à utiliser ce bouton.")
                return
            parts = data.split(":", 2)
            verb = parts[1] if len(parts) > 1 else ""
            mode = normalize_cockpit_mode(parts[2] if len(parts) > 2 else None)
            if verb == "cancel":
                self._pilot_intake_states.pop(caller_id, None)
                await query.answer(text="Annulé")
                try:
                    await query.edit_message_text("Nouveau chat annulé.", reply_markup=None)
                except Exception:
                    pass
                return
            if verb == "mode":
                await self._set_cockpit_mode(caller_id, mode, str(query_chat_id or ""))
                await self._sync_cockpit_llm_prefs_to_api(caller_id, mode, str(query_chat_id or ""))
                await query.answer(text=f"Mode: {self._mode_title(mode)}")
                try:
                    await query.edit_message_text(
                        self._new_chat_text_with_prefs(mode, caller_id),
                        parse_mode=ParseMode.HTML,
                        reply_markup=self._new_chat_keyboard_with_prefs(mode, caller_id),
                    )
                except Exception:
                    pass
                return
            if verb == "pickmodel":
                prefs = self._get_cockpit_llm_prefs(caller_id)
                await query.answer()
                try:
                    await query.edit_message_text(
                        "<b>Modèle pour cette tâche</b>\n\n" + self._llm_prefs_summary_html(prefs),
                        parse_mode=ParseMode.HTML,
                        reply_markup=self._build_cockpit_model_keyboard(mode),
                    )
                except Exception:
                    pass
                return
            if verb == "pickreason":
                prefs = self._get_cockpit_llm_prefs(caller_id)
                await query.answer()
                try:
                    await query.edit_message_text(
                        "<b>Réflexion pour cette tâche</b>\n\n" + self._llm_prefs_summary_html(prefs),
                        parse_mode=ParseMode.HTML,
                        reply_markup=self._build_cockpit_reason_keyboard(mode),
                    )
                except Exception:
                    pass
                return
            if verb == "intent":
                intent = parts[2].split(":", 1)[0] if len(parts) > 2 else "pilot_discovery"
                # Data shape is rcn:intent:<intent>:<mode>; recover mode from tail if present.
                subparts = data.split(":")
                intent = subparts[2] if len(subparts) > 2 else "pilot_discovery"
                mode = normalize_cockpit_mode(subparts[3] if len(subparts) > 3 else mode)
                self._pilot_intake_states[caller_id] = {
                    "awaiting": "repo",
                    "mode": mode,
                    "origin": "github_existing",
                    "intent": intent,
                    "chat_id": str(query_chat_id or ""),
                    "ts": time.time(),
                }
                await self._set_cockpit_mode(caller_id, mode, str(query_chat_id or ""))
                await query.answer(text="Choisis le repo")
                cockpit_url = self._repo_cockpit_url("/select-repo", mode=mode)
                repos_payload = await asyncio.to_thread(self._cockpit_api_sync, "GET", "/api/internal/repos", None, 20)
                repos = repos_payload.get("repos") if isinstance(repos_payload, dict) else []
                if not isinstance(repos, list):
                    repos = []
                self._repo_new_chat_choices[caller_id] = {
                    "repos": repos,
                    "mode": mode,
                    "chat_id": str(query_chat_id or ""),
                    "intent": intent,
                    "ts": time.time(),
                }
                try:
                    await query.edit_message_text(
                        "<b>Projet GitHub existant</b>\n\n"
                        f"Route : <b>{_html.escape(self._pilot_intent_title(intent))}</b>\n\n"
                        "Choisis le repo MFcv1 à utiliser.",
                        parse_mode=ParseMode.HTML,
                        reply_markup=self._repo_new_chat_keyboard(caller_id, mode, repos, cockpit_url),
                    )
                except Exception:
                    pass
                return
            if verb == "existing":
                await self._set_cockpit_mode(caller_id, mode, str(query_chat_id or ""))
                if mode == "pilote":
                    await query.answer(text="Choisis l'intention")
                    try:
                        await query.edit_message_text(
                            "<b>Projet GitHub existant</b>\n\n"
                            "Tu veux faire quoi sur ce repo ?",
                            parse_mode=ParseMode.HTML,
                            reply_markup=self._pilot_existing_intent_keyboard(mode),
                        )
                    except Exception:
                        pass
                    return
                await query.answer(text="Repos MFcv1")
                cockpit_url = self._repo_cockpit_url("/select-repo", mode=mode)
                repos_payload = await asyncio.to_thread(self._cockpit_api_sync, "GET", "/api/internal/repos", None, 20)
                repos = repos_payload.get("repos") if isinstance(repos_payload, dict) else []
                if not isinstance(repos, list):
                    repos = []
                self._repo_new_chat_choices[caller_id] = {
                    "repos": repos,
                    "mode": mode,
                    "chat_id": str(query_chat_id or ""),
                    "ts": time.time(),
                }
                keyboard = self._repo_new_chat_keyboard(caller_id, mode, repos, cockpit_url)
                try:
                    await query.edit_message_text(
                        "<b>Projet GitHub existant</b>\n\n"
                        f"Mode choisi : <b>{_html.escape(self._mode_title(mode))}</b>\n\n"
                        "Choisis un repo MFcv1 ci-dessous. La Mini App reste disponible en secours pour la liste complète.",
                        parse_mode=ParseMode.HTML,
                        reply_markup=keyboard,
                    )
                except Exception:
                    pass
                return
            if verb == "scratch":
                await self._set_cockpit_mode(caller_id, mode, str(query_chat_id or ""))
                if mode == "pilote":
                    self._pilot_intake_states[caller_id] = {
                        "awaiting": "prompt",
                        "mode": "pilote",
                        "origin": "from_scratch",
                        "intent": "architect",
                        "chat_id": str(query_chat_id or ""),
                        "ts": time.time(),
                    }
                    await query.answer(text="Écris le prompt")
                    try:
                        await query.edit_message_text(
                            self._pilot_waiting_prompt_text(origin="from_scratch", intent="architect", user_id=caller_id),
                            parse_mode=ParseMode.HTML,
                            reply_markup=InlineKeyboardMarkup([[
                                InlineKeyboardButton("Annuler", callback_data="rcn:cancel")
                            ]]),
                        )
                    except Exception:
                        pass
                    return
                await query.answer(text="Confirmation texte requise")
                try:
                    await query.edit_message_text(
                        "<b>Start from scratch</b>\n\n"
                        f"Mode choisi : <b>{_html.escape(self._mode_title(mode))}</b>\n\n"
                        "Pour éviter une création accidentelle de repo GitHub, confirme avec une commande texte :\n\n"
                        f"<code>/new scratch nom-projet | description courte | private | {mode}</code>\n\n"
                        "Exemple :\n"
                        f"<code>/new scratch landing-ai-test | landing page IA de test | private | {mode}</code>",
                        parse_mode=ParseMode.HTML,
                        reply_markup=None,
                    )
                except Exception:
                    pass
                return

        # --- Repo Cockpit native repo selection callbacks (rcnr:mode:index) ---
        if data.startswith("rcnr:"):
            caller_id = str(getattr(query.from_user, "id", ""))
            if not self._is_callback_user_authorized(
                caller_id,
                chat_id=query_chat_id,
                chat_type=str(query_chat_type) if query_chat_type is not None else None,
                thread_id=str(query_thread_id) if query_thread_id is not None else None,
                user_name=query_user_name,
            ):
                await query.answer(text="⛔ Tu n'es pas autorisé à utiliser ce bouton.")
                return
            parts = data.split(":", 2)
            mode = normalize_cockpit_mode(parts[1] if len(parts) > 1 else None)
            try:
                index = int(parts[2]) if len(parts) > 2 else -1
            except ValueError:
                index = -1
            choice_state = self._repo_new_chat_choices.get(caller_id) or {}
            repos = choice_state.get("repos") if isinstance(choice_state, dict) else []
            if not isinstance(repos, list) or index < 0 or index >= len(repos):
                await query.answer(text="Liste expirée, relance /new")
                return
            repo = str((repos[index] or {}).get("nameWithOwner") or "")
            if not repo:
                await query.answer(text="Repo invalide")
                return
            await query.answer(text="Sélection...")
            prefs = self._get_cockpit_llm_prefs(caller_id)
            payload = {
                "telegram_user_id": caller_id,
                "chat_id": str(query_chat_id or choice_state.get("chat_id") or ""),
                "repo": repo,
                "mode": mode,
                "notify": False,
                "chat_model": prefs.get("model"),
                "chat_provider": prefs.get("provider"),
                "reasoning_effort": prefs.get("reasoning_effort"),
            }
            result = await asyncio.to_thread(self._cockpit_api_sync, "POST", "/api/internal/select", payload, 20)
            if not result.get("ok"):
                try:
                    await query.edit_message_text(
                        "<b>Projet GitHub existant</b>\n\n"
                        "Sélection impossible : <code>"
                        + _html.escape(str(result.get("description") or result))[:900]
                        + "</code>",
                        parse_mode=ParseMode.HTML,
                        reply_markup=None,
                    )
                except Exception:
                    pass
                return
            self._repo_new_chat_choices.pop(caller_id, None)
            thread_id = str(result.get("thread_id") or "")
            if thread_id:
                self._cockpit_register_thread_llm_prefs(
                    chat_id=str(query_chat_id or choice_state.get("chat_id") or ""),
                    thread_id=thread_id,
                    telegram_user_id=caller_id,
                )
            try:
                if mode == "pilote":
                    intent = str(choice_state.get("intent") or (self._pilot_intake_states.get(caller_id) or {}).get("intent") or "pilot_discovery")
                    self._pilot_intake_states[caller_id] = {
                        "awaiting": "prompt",
                        "mode": mode,
                        "origin": "github_existing",
                        "intent": intent,
                        "repo": repo,
                        "chat_id": str(query_chat_id or choice_state.get("chat_id") or ""),
                        "thread_id": thread_id,
                        "ts": time.time(),
                    }
                    await query.edit_message_text(
                        self._pilot_waiting_prompt_text(origin="github_existing", intent=intent, repo=repo, user_id=caller_id),
                        parse_mode=ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Annuler", callback_data="rcn:cancel")]]),
                    )
                else:
                    await query.edit_message_text(
                        self._repo_selected_text(repo, mode, str(result.get("thread_id") or "")),
                        parse_mode=ParseMode.HTML,
                        reply_markup=self._repo_selected_keyboard(mode),
                    )
            except Exception:
                pass
            return

        # --- Repo Cockpit thread callbacks (rct:verb:arg) ---
        if data.startswith("rct:"):
            caller_id = str(getattr(query.from_user, "id", ""))
            if not self._is_callback_user_authorized(
                caller_id,
                chat_id=query_chat_id,
                chat_type=str(query_chat_type) if query_chat_type is not None else None,
                thread_id=str(query_thread_id) if query_thread_id is not None else None,
                user_name=query_user_name,
            ):
                await query.answer(text="⛔ Tu n'es pas autorisé à utiliser ce bouton.")
                return
            await self._handle_thread_callback(query, data, caller_id)
            return

        # --- Repo Cockpit autonomy callbacks (rca:verb:task_id) ---
        if data.startswith("rca:"):
            caller_id = str(getattr(query.from_user, "id", ""))
            if not self._is_callback_user_authorized(
                caller_id,
                chat_id=query_chat_id,
                chat_type=str(query_chat_type) if query_chat_type is not None else None,
                thread_id=str(query_thread_id) if query_thread_id is not None else None,
                user_name=query_user_name,
            ):
                await query.answer(text="⛔ Tu n'es pas autorisé à utiliser ce bouton.")
                return
            await self._handle_autonomy_callback(query, data)
            return

        # --- Simple developer cockpit callbacks (dev:section) ---
        if data.startswith("dev:"):
            caller_id = str(getattr(query.from_user, "id", ""))
            if not self._is_callback_user_authorized(
                caller_id,
                chat_id=query_chat_id,
                chat_type=str(query_chat_type) if query_chat_type is not None else None,
                thread_id=str(query_thread_id) if query_thread_id is not None else None,
                user_name=query_user_name,
            ):
                await query.answer(text="⛔ Tu n'es pas autorisé à utiliser ce bouton.")
                return
            await self._handle_dev_callback(query, data)
            return

        # --- Scheduled jobs callbacks (job:verb:id) ---
        if data.startswith("job:"):
            caller_id = str(getattr(query.from_user, "id", ""))
            if not self._is_callback_user_authorized(
                caller_id,
                chat_id=query_chat_id,
                chat_type=str(query_chat_type) if query_chat_type is not None else None,
                thread_id=str(query_thread_id) if query_thread_id is not None else None,
                user_name=query_user_name,
            ):
                await query.answer(text="⛔ Tu n'es pas autorisé à utiliser ce bouton.")
                return
            await self._handle_jobs_callback(query, data)
            return

        # --- Model picker callbacks ---
        if data.startswith(("mp:", "mpg:", "mm:", "mc:", "mb", "mx", "mg:")):
            chat_id = str(query.message.chat_id) if query.message else None
            if chat_id:
                await self._handle_model_picker_callback(query, data, chat_id)
            return

        # --- Gmail-triage callbacks (gt:verb:arg) ---
        if data.startswith("gt:"):
            await self._handle_gmail_triage_callback(
                query,
                data,
                query_chat_id=query_chat_id,
                query_chat_type=query_chat_type,
                query_thread_id=query_thread_id,
                query_user_name=query_user_name,
            )
            return

        # --- Exec approval callbacks (ea:choice:id) ---
        if data.startswith("ea:"):
            parts = data.split(":", 2)
            if len(parts) == 3:
                choice = parts[1]  # once, session, always, deny
                try:
                    approval_id = int(parts[2])
                except (ValueError, IndexError):
                    await query.answer(text="Invalid approval data.")
                    return

                # Only authorized users may click approval buttons.
                caller_id = str(getattr(query.from_user, "id", ""))
                if not self._is_callback_user_authorized(
                    caller_id,
                    chat_id=query_chat_id,
                    chat_type=str(query_chat_type) if query_chat_type is not None else None,
                    thread_id=str(query_thread_id) if query_thread_id is not None else None,
                    user_name=query_user_name,
                ):
                    await query.answer(text="⛔ You are not authorized to approve commands.")
                    return

                session_key = self._approval_state.pop(approval_id, None)
                if not session_key:
                    await query.answer(text="This approval has already been resolved.")
                    return

                # Map choice to human-readable label
                label_map = {
                    "once": "✅ Approved once",
                    "session": "✅ Approved for session",
                    "always": "✅ Approved permanently",
                    "deny": "❌ Denied",
                }
                user_display = getattr(query.from_user, "first_name", "User")
                label = label_map.get(choice, "Resolved")

                await query.answer(text=label)

                # Edit message to show decision, remove buttons
                try:
                    await query.edit_message_text(
                        text=self.format_message(f"{label} by {user_display}"),
                        parse_mode=ParseMode.MARKDOWN_V2,
                        reply_markup=None,
                    )
                except Exception:
                    pass  # non-fatal if edit fails

                # Resolve the approval — unblocks the agent thread
                try:
                    from tools.approval import resolve_gateway_approval
                    count = resolve_gateway_approval(session_key, choice)
                    logger.info(
                        "Telegram button resolved %d approval(s) for session %s (choice=%s, user=%s)",
                        count, session_key, choice, user_display,
                    )
                except Exception as exc:
                    logger.error("Failed to resolve gateway approval from Telegram button: %s", exc)
                    count = 0

                # Resume the typing indicator — paused when the approval was
                # sent (gateway/run.py).  The text /approve and /deny paths
                # call resume_typing_for_chat here too; without it, typing
                # stays paused for the rest of the turn after an inline
                # button click.
                if count and query_chat_id is not None:
                    self.resume_typing_for_chat(str(query_chat_id))
            return

        # --- Slash-confirm callbacks (sc:choice:confirm_id) ---
        if data.startswith("sc:"):
            parts = data.split(":", 2)
            if len(parts) == 3:
                choice = parts[1]  # once, always, cancel
                confirm_id = parts[2]

                caller_id = str(getattr(query.from_user, "id", ""))
                if not self._is_callback_user_authorized(
                    caller_id,
                    chat_id=query_chat_id,
                    chat_type=str(query_chat_type) if query_chat_type is not None else None,
                    thread_id=str(query_thread_id) if query_thread_id is not None else None,
                    user_name=query_user_name,
                ):
                    await query.answer(text="⛔ You are not authorized to answer this prompt.")
                    return

                session_key = self._slash_confirm_state.pop(confirm_id, None)
                if not session_key:
                    await query.answer(text="This prompt has already been resolved.")
                    return

                label_map = {
                    "once": "✅ Approved once",
                    "always": "🔒 Always approve",
                    "cancel": "❌ Cancelled",
                }
                user_display = getattr(query.from_user, "first_name", "User")
                label = label_map.get(choice, "Resolved")

                await query.answer(text=label)

                try:
                    await query.edit_message_text(
                        text=self.format_message(f"{label} by {user_display}"),
                        parse_mode=ParseMode.MARKDOWN_V2,
                        reply_markup=None,
                    )
                except Exception:
                    pass

                # Resolve via the module-level primitive.  The runner stored
                # a handler keyed by session_key; we run it on the event
                # loop and (if it returns a string) send it as a follow-up
                # message in the same chat.
                try:
                    from tools import slash_confirm as _slash_confirm_mod
                    result_text = await _slash_confirm_mod.resolve(
                        session_key, confirm_id, choice,
                    )
                    if result_text and query.message:
                        # Inherit the prompt message's topic. Supergroup forums
                        # use message_thread_id; Telegram private DM-topic lanes
                        # need both the private topic id and the prompt reply anchor.
                        thread_id = getattr(query.message, "message_thread_id", None)
                        chat = getattr(query.message, "chat", None)
                        chat_type = getattr(chat, "type", None)
                        prompt_message_id = getattr(query.message, "message_id", None)
                        send_kwargs: Dict[str, Any] = {
                            "chat_id": int(query.message.chat_id),
                            "text": self.format_message(result_text),
                            "parse_mode": ParseMode.MARKDOWN_V2,
                            **self._link_preview_kwargs(),
                        }
                        chat_type_value = getattr(chat_type, "value", chat_type)
                        is_private_chat = str(chat_type_value).lower() in {
                            "private",
                            str(ChatType.PRIVATE).lower(),
                            str(getattr(ChatType.PRIVATE, "value", ChatType.PRIVATE)).lower(),
                        }
                        if thread_id is not None and is_private_chat and prompt_message_id is not None:
                            reply_to_id = int(prompt_message_id)
                            send_kwargs["reply_to_message_id"] = reply_to_id
                            send_kwargs.update(
                                self._thread_kwargs_for_send(
                                    str(query.message.chat_id),
                                    str(thread_id),
                                    {
                                        "thread_id": str(thread_id),
                                        "telegram_dm_topic_reply_fallback": True,
                                    },
                                    reply_to_message_id=reply_to_id,
                                    reply_to_mode=self._reply_to_mode
                                )
                            )
                        elif thread_id is not None:
                            send_kwargs.update(
                                self._thread_kwargs_for_send(
                                    str(query.message.chat_id),
                                    str(thread_id),
                                    {"thread_id": str(thread_id)},
                                    reply_to_mode=self._reply_to_mode
                                )
                            )
                        await self._send_message_with_thread_fallback(**send_kwargs)
                except Exception as exc:
                    logger.error("[%s] slash-confirm callback failed: %s", self.name, exc, exc_info=True)
            return

        # --- Clarify callbacks (cl:clarify_id:idx | cl:clarify_id:other) ---
        if data.startswith("cl:"):
            parts = data.split(":", 2)
            if len(parts) == 3:
                clarify_id = parts[1]
                choice_token = parts[2]

                caller_id = str(getattr(query.from_user, "id", ""))
                if not self._is_callback_user_authorized(
                    caller_id,
                    chat_id=query_chat_id,
                    chat_type=str(query_chat_type) if query_chat_type is not None else None,
                    thread_id=str(query_thread_id) if query_thread_id is not None else None,
                    user_name=query_user_name,
                ):
                    await query.answer(text="⛔ You are not authorized to answer this prompt.")
                    return

                session_key = self._clarify_state.get(clarify_id)
                if not session_key:
                    await query.answer(text="This prompt has already been resolved.")
                    return

                user_display = getattr(query.from_user, "first_name", "User")

                if choice_token == "other":
                    # Flip into text-capture mode and tell the user to type
                    # their answer.  The gateway's text-intercept will pick
                    # up the next message in this session and resolve the
                    # clarify.  Do NOT pop _clarify_state yet — we still
                    # need it if the user is slow to respond and the entry
                    # is cleared by something else.
                    try:
                        from tools.clarify_gateway import mark_awaiting_text
                        mark_awaiting_text(clarify_id)
                    except Exception as exc:
                        logger.warning("[%s] mark_awaiting_text failed: %s", self.name, exc)

                    await query.answer(text="✏️ Type your answer in the chat.")
                    try:
                        await query.edit_message_text(
                            text=f"❓ {query.message.text or ''}\n\n<i>Awaiting typed response from {_html.escape(user_display)}…</i>",
                            parse_mode=ParseMode.HTML,
                            reply_markup=None,
                        )
                    except Exception:
                        pass
                    return

                # Numeric choice → resolve immediately with the chosen text
                try:
                    idx = int(choice_token)
                except (ValueError, TypeError):
                    await query.answer(text="Invalid choice.")
                    return

                # Look up the choice text from the entry registered in the
                # clarify primitive.  Fall back to the index if the entry
                # has been cleaned up (race with timeout / session reset).
                resolved_text: Optional[str] = None
                try:
                    from tools.clarify_gateway import _entries as _clarify_entries  # type: ignore
                    entry = _clarify_entries.get(clarify_id)
                    if entry and entry.choices and 0 <= idx < len(entry.choices):
                        resolved_text = entry.choices[idx]
                except Exception:
                    resolved_text = None

                if resolved_text is None:
                    # Race: entry vanished. Echo the index as a number so
                    # the agent at least sees an intentional response
                    # rather than nothing.
                    resolved_text = f"choice {idx + 1}"

                # Pop state and resolve
                self._clarify_state.pop(clarify_id, None)
                try:
                    from tools.clarify_gateway import resolve_gateway_clarify
                    resolved = resolve_gateway_clarify(clarify_id, resolved_text)
                except Exception as exc:
                    logger.error("[%s] resolve_gateway_clarify failed: %s", self.name, exc)
                    resolved = False

                await query.answer(text=f"✓ {resolved_text[:60]}")
                try:
                    await query.edit_message_text(
                        text=f"❓ {_html.escape(query.message.text or '')}\n\n<b>{_html.escape(user_display)}:</b> {_html.escape(resolved_text)}",
                        parse_mode=ParseMode.HTML,
                        reply_markup=None,
                    )
                except Exception:
                    pass

                if resolved:
                    logger.info(
                        "Telegram clarify button resolved (id=%s, choice=%r, user=%s)",
                        clarify_id, resolved_text, user_display,
                    )
                else:
                    logger.warning(
                        "Telegram clarify button: resolve_gateway_clarify returned False (id=%s)",
                        clarify_id,
                    )
            return

        # --- Update prompt callbacks ---
        if not data.startswith("update_prompt:"):
            return
        answer = data.split(":", 1)[1]  # "y" or "n"
        caller_id = str(getattr(query.from_user, "id", ""))
        if not self._is_callback_user_authorized(
            caller_id,
            chat_id=query_chat_id,
            chat_type=str(query_chat_type) if query_chat_type is not None else None,
            thread_id=str(query_thread_id) if query_thread_id is not None else None,
            user_name=query_user_name,
        ):
            await query.answer(text="⛔ You are not authorized to answer update prompts.")
            return
        await query.answer(text=f"Sent '{answer}' to the update process.")
        # Edit the message to show the choice and remove buttons
        label = "Yes" if answer == "y" else "No"
        try:
            await query.edit_message_text(
                text=self.format_message(f"⚕ Update prompt answered: *{label}*"),
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=None,
            )
        except Exception:
            pass  # non-fatal if edit fails
        # Write the response file
        try:
            from hermes_constants import get_hermes_home
            home = get_hermes_home()
            response_path = home / ".update_response"
            tmp = response_path.with_suffix(".tmp")
            tmp.write_text(answer)
            tmp.replace(response_path)
            logger.info("Telegram update prompt answered '%s' by user %s",
                        answer, getattr(query.from_user, "id", "unknown"))
        except Exception as exc:
            logger.error("Failed to write update response from callback: %s", exc)

    # Maps `gt:<verb>` -> (script-name, extra-args, success-label, is_state).
    # Scripts live in ~/.hermes/scripts/gmail-triage/. `arg` from the callback
    # data is always passed as the first positional arg.
    # is_state=True means the verb is a sticky sender-rule change (mute, trust,
    # vip) that should leave the keyboard tappable for follow-on actions.
    # is_state=False is a per-email one-shot (send, archive, draft, spam) that
    # strips the keyboard on success.
    _GT_VERB_DISPATCH = {
        "send":         ("send-draft.sh",      [],         "✓ sent draft",         False),
        "archive":      ("archive.sh",         [],         "✓ archived",           False),
        "draft":        ("draft-blank.sh",     [],         "✓ drafted reply",      False),
        "spam":         ("spam.sh",            [],         "✓ marked spam",        False),
        "mute":         ("mute-add.sh",        ["email"],  "✓ muted",              True),
        "mute-domain":  ("mute-add.sh",        ["domain"], "✓ muted domain",       True),
        "trust":        ("trusted-ops-add.sh", ["email"],  "✓ trusted",            True),
        "trust-domain": ("trusted-ops-add.sh", ["domain"], "✓ trusted domain",     True),
        "vip":          ("vip-add.sh",         ["email"],  "✓ marked VIP",         True),
        "vip-domain":   ("vip-add.sh",         ["domain"], "✓ marked VIP domain",  True),
    }

    async def _handle_gmail_triage_callback(
        self,
        query,
        data: str,
        *,
        query_chat_id,
        query_chat_type,
        query_thread_id,
        query_user_name,
    ) -> None:
        """Dispatch a gmail-triage inline-button callback (gt:verb:arg)."""
        parts = data.split(":", 2)
        if len(parts) != 3:
            await query.answer(text="Invalid gmail-triage data.")
            return
        verb, arg = parts[1], parts[2]

        caller_id = str(getattr(query.from_user, "id", ""))
        if not self._is_callback_user_authorized(
            caller_id,
            chat_id=query_chat_id,
            chat_type=str(query_chat_type) if query_chat_type is not None else None,
            thread_id=str(query_thread_id) if query_thread_id is not None else None,
            user_name=query_user_name,
        ):
            await query.answer(text="⛔ You are not authorized to act on this email.")
            return

        entry = self._GT_VERB_DISPATCH.get(verb)
        if not entry:
            await query.answer(text=f"Unknown verb: {verb}")
            return
        script_name, extra_args, success_label, is_state_verb = entry

        script_path = _Path.home() / ".hermes" / "scripts" / "gmail-triage" / script_name
        if not script_path.exists():
            await query.answer(text=f"❌ {script_name} missing")
            logger.error("[%s] gmail-triage script missing: %s", self.name, script_path)
            return

        cmd = [str(script_path), arg, *extra_args]
        success = False
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=60,
            )
            if proc.returncode == 0:
                label = success_label
                success = True
                logger.info(
                    "[%s] gmail-triage callback ok: verb=%s arg=%s",
                    self.name, verb, arg,
                )
            else:
                stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
                last_line = stderr_text.splitlines()[-1] if stderr_text else f"exit {proc.returncode}"
                label = f"❌ {verb} failed: {last_line[:80]}"
                logger.error(
                    "[%s] gmail-triage callback failed: verb=%s arg=%s rc=%s stderr=%s",
                    self.name, verb, arg, proc.returncode, stderr_text,
                )
        except asyncio.TimeoutError:
            label = f"❌ {verb} timed out"
            logger.error("[%s] gmail-triage callback timed out: verb=%s arg=%s", self.name, verb, arg)
        except Exception as exc:
            label = f"❌ {verb} error: {exc}"
            logger.error(
                "[%s] gmail-triage callback exception: verb=%s arg=%s err=%s",
                self.name, verb, arg, exc, exc_info=True,
            )

        await query.answer(text=label)
        if not success:
            return

        user_display = getattr(query.from_user, "first_name", "User")
        original_text = (query.message.text or "") if query.message else ""
        appended = f"{original_text}\n— {label} by {user_display}"
        try:
            if is_state_verb:
                # Sticky state change: append confirmation, KEEP keyboard so
                # the user can stack further actions on this email.
                await query.edit_message_text(text=appended)
            else:
                # Per-email one-shot: strip keyboard so the action can't fire twice.
                await query.edit_message_text(text=appended, reply_markup=None)
        except Exception:
            pass

    def _missing_media_path_error(self, label: str, path: str) -> str:
        """Build an actionable file-not-found error for gateway MEDIA delivery.

        Paths like /workspace/... or /output/... often only exist inside the
        Docker sandbox, while the gateway process runs on the host.
        """
        error = f"{label} file not found: {path}"
        if path.startswith(("/workspace/", "/output/", "/outputs/")):
            error += (
                " (path may only exist inside the Docker sandbox. "
                "Bind-mount a host directory and emit the host-visible "
                "path in MEDIA: for gateway file delivery.)"
            )
        return error

    def _telegram_media_too_large_note(self, label: str, file_size: Any, max_bytes: int) -> str:
        limit_mb = max(1, max_bytes // (1024 * 1024))
        try:
            size_mb = int(file_size or 0) / (1024 * 1024)
            size_text = f"{size_mb:.1f} MB"
        except (TypeError, ValueError):
            size_text = "unknown size"
        return (
            f"[Telegram {label} skipped: file size {size_text} exceeds the "
            f"{limit_mb} MB limit. Ask the user to send a shorter voice note "
            "or a smaller audio file.]"
        )

    def _telegram_media_size_allowed(self, source: Any, label: str) -> tuple[bool, Optional[str]]:
        """Validate Telegram media size before downloading into memory."""
        max_bytes = int(getattr(self, "_max_doc_bytes", 20 * 1024 * 1024) or 20 * 1024 * 1024)
        file_size = getattr(source, "file_size", None)
        try:
            size = int(file_size or 0)
        except (TypeError, ValueError):
            size = 0
        if size <= 0:
            return True, None
        if size <= max_bytes:
            return True, None
        return False, self._telegram_media_too_large_note(label, size, max_bytes)

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """Send audio as a native Telegram voice message or audio file."""
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        try:
            if not os.path.exists(audio_path):
                return SendResult(success=False, error=self._missing_media_path_error("Audio", audio_path))

            with open(audio_path, "rb") as audio_file:
                ext = os.path.splitext(audio_path)[1].lower()
                # .ogg / .opus files -> send as voice (round playable bubble)
                if ext in {".ogg", ".opus"}:
                    _voice_thread = self._metadata_thread_id(metadata)
                    reply_to_id = self._reply_to_message_id_for_send(reply_to, metadata, reply_to_mode=self._reply_to_mode)
                    voice_thread_kwargs = self._thread_kwargs_for_send(
                        chat_id,
                        _voice_thread,
                        metadata,
                        reply_to_message_id=reply_to_id,
                        reply_to_mode=self._reply_to_mode
                    )
                    msg = await self._send_with_dm_topic_reply_anchor_retry(
                        self._bot.send_voice,
                        {
                            "chat_id": int(chat_id),
                            "voice": audio_file,
                            "caption": caption[:1024] if caption else None,
                            "reply_to_message_id": reply_to_id,
                            **voice_thread_kwargs,
                            **self._notification_kwargs(metadata),
                        },
                        metadata,
                        reply_to_id,
                        "voice",
                        reset_media=lambda: audio_file.seek(0),
                    )
                elif ext in {".mp3", ".m4a"}:
                    # Telegram's Bot API sendAudio only accepts MP3 / M4A.
                    _audio_thread = self._metadata_thread_id(metadata)
                    reply_to_id = self._reply_to_message_id_for_send(reply_to, metadata, reply_to_mode=self._reply_to_mode)
                    audio_thread_kwargs = self._thread_kwargs_for_send(
                        chat_id,
                        _audio_thread,
                        metadata,
                        reply_to_message_id=reply_to_id,
                        reply_to_mode=self._reply_to_mode
                    )
                    msg = await self._send_with_dm_topic_reply_anchor_retry(
                        self._bot.send_audio,
                        {
                            "chat_id": int(chat_id),
                            "audio": audio_file,
                            "caption": caption[:1024] if caption else None,
                            "reply_to_message_id": reply_to_id,
                            **audio_thread_kwargs,
                            **self._notification_kwargs(metadata),
                        },
                        metadata,
                        reply_to_id,
                        "audio",
                        reset_media=lambda: audio_file.seek(0),
                    )
                else:
                    # Formats Telegram can't play natively (.wav, .flac, ...)
                    # — fall back to document delivery instead of raising.
                    return await self.send_document(
                        chat_id=chat_id,
                        file_path=audio_path,
                        caption=caption,
                        reply_to=reply_to,
                        metadata=metadata,
                    )
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.error(
                "[%s] Failed to send Telegram voice/audio, falling back to base adapter: %s",
                self.name,
                e,
                exc_info=True,
            )
            return await super().send_voice(chat_id, audio_path, caption, reply_to, metadata=metadata)

    async def send_multiple_images(
        self,
        chat_id: str,
        images: List[tuple],
        metadata: Optional[Dict[str, Any]] = None,
        human_delay: float = 0.0,
    ) -> None:
        """Send a batch of images natively via Telegram's media group API.

        Telegram's ``send_media_group`` bundles up to 10 photos/videos into
        a single album. Larger batches are chunked. Animated GIFs cannot
        go into a media group (they require ``send_animation``), so they
        are peeled off and sent individually via the base default path.

        URL-based photos go into the group directly; local files are
        opened as byte streams. On failure the whole batch falls back to
        the base adapter's per-image loop.
        """
        if not self._bot:
            return
        if not images:
            return

        try:
            from telegram import InputMediaPhoto
        except Exception as exc:  # pragma: no cover - missing SDK
            logger.warning(
                "[%s] InputMediaPhoto unavailable, falling back to per-image send: %s",
                self.name, exc,
            )
            await super().send_multiple_images(chat_id, images, metadata, human_delay)
            return

        # Peel off animations — they need send_animation, not send_media_group
        animations: List[tuple] = []
        photos: List[tuple] = []
        for image_url, alt_text in images:
            if not image_url.startswith("file://") and self._is_animation_url(image_url):
                animations.append((image_url, alt_text))
            else:
                photos.append((image_url, alt_text))

        # Animations: route through the base default (per-image send_animation)
        if animations:
            await super().send_multiple_images(
                chat_id, animations, metadata, human_delay=human_delay,
            )

        if not photos:
            return

        from urllib.parse import unquote as _unquote
        _thread = self._metadata_thread_id(metadata)

        # Chunk into groups of 10 (Telegram's album limit)
        CHUNK = 10
        chunks = [photos[i:i + CHUNK] for i in range(0, len(photos), CHUNK)]

        for chunk_idx, chunk in enumerate(chunks):
            if human_delay > 0 and chunk_idx > 0:
                await asyncio.sleep(human_delay)

            media: List[Any] = []
            opened_files: List[Any] = []
            try:
                for image_url, alt_text in chunk:
                    caption = alt_text[:1024] if alt_text else None
                    if image_url.startswith("file://"):
                        local_path = _unquote(image_url[7:])
                        if not os.path.exists(local_path):
                            logger.warning(
                                "[%s] Skipping missing image in media group: %s",
                                self.name, local_path,
                            )
                            continue
                        fh = open(local_path, "rb")
                        opened_files.append(fh)
                        media.append(InputMediaPhoto(media=fh, caption=caption))
                    else:
                        media.append(InputMediaPhoto(media=image_url, caption=caption))

                if not media:
                    continue

                logger.info(
                    "[%s] Sending media group of %d photo(s) (chunk %d/%d)",
                    self.name, len(media), chunk_idx + 1, len(chunks),
                )
                reply_to_id = self._reply_to_message_id_for_send(None, metadata, reply_to_mode=self._reply_to_mode)
                thread_kwargs = self._thread_kwargs_for_send(
                    chat_id,
                    _thread,
                    metadata,
                    reply_to_message_id=reply_to_id,
                    reply_to_mode=self._reply_to_mode
                )

                def _reset_opened_files() -> None:
                    for fh in opened_files:
                        try:
                            fh.seek(0)
                        except Exception:
                            pass

                await self._send_with_dm_topic_reply_anchor_retry(
                    self._bot.send_media_group,
                    {
                        "chat_id": int(chat_id),
                        "media": media,
                        "reply_to_message_id": reply_to_id,
                        **thread_kwargs,
                        **self._notification_kwargs(metadata),
                    },
                    metadata,
                    reply_to_id,
                    "media group",
                    reset_media=_reset_opened_files,
                )
            except Exception as e:
                logger.warning(
                    "[%s] send_media_group failed (chunk %d/%d), falling back to per-image: %s",
                    self.name, chunk_idx + 1, len(chunks), e,
                    exc_info=True,
                )
                # Fallback: send each photo in this chunk individually
                await super().send_multiple_images(
                    chat_id, chunk, metadata, human_delay=human_delay,
                )
            finally:
                for fh in opened_files:
                    try:
                        fh.close()
                    except Exception:
                        pass

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """Send a local image file natively as a Telegram photo."""
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        try:
            if not os.path.exists(image_path):
                return SendResult(success=False, error=self._missing_media_path_error("Image", image_path))

            _thread = self._metadata_thread_id(metadata)
            reply_to_id = self._reply_to_message_id_for_send(reply_to, metadata, reply_to_mode=self._reply_to_mode)
            thread_kwargs = self._thread_kwargs_for_send(
                chat_id,
                _thread,
                metadata,
                reply_to_message_id=reply_to_id,
                reply_to_mode=self._reply_to_mode
            )
            with open(image_path, "rb") as image_file:
                msg = await self._send_with_dm_topic_reply_anchor_retry(
                    self._bot.send_photo,
                    {
                        "chat_id": int(chat_id),
                        "photo": image_file,
                        "caption": caption[:1024] if caption else None,
                        "reply_to_message_id": reply_to_id,
                        **thread_kwargs,
                        **self._notification_kwargs(metadata),
                    },
                    metadata,
                    reply_to_id,
                    "photo",
                    reset_media=lambda: image_file.seek(0),
                )
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            error_str = str(e)
            # Dimension-related errors are the expected case for valid image
            # files that Telegram just refuses as photos (screenshots, extreme
            # aspect ratios). Log at INFO because the document fallback is
            # the correct path. Any other send_photo failure also falls back
            # to document (rate limits, corrupt file markers, format edge
            # cases), but at WARNING because it's unexpected and worth
            # surfacing in logs.
            is_dim_error = (
                "Photo_invalid_dimensions" in error_str
                or "PHOTO_INVALID_DIMENSIONS" in error_str
            )
            if is_dim_error:
                logger.info(
                    "[%s] Image dimensions exceed Telegram photo limits, "
                    "sending as document: %s",
                    self.name,
                    image_path,
                )
            else:
                logger.warning(
                    "[%s] Failed to send Telegram local image as photo, "
                    "trying document fallback: %s",
                    self.name,
                    e,
                    exc_info=True,
                )
            # Fallback to sending as document (file) — no dimension limit,
            # only 50MB size limit. If even that fails, fall back to the
            # base adapter's text-only "Image: /path" rendering.
            try:
                return await self.send_document(
                    chat_id=chat_id,
                    file_path=image_path,
                    caption=caption,
                    file_name=os.path.basename(image_path),
                    reply_to=reply_to,
                    metadata=metadata,
                )
            except Exception as doc_err:
                logger.error(
                    "[%s] Failed to send Telegram local image as document, "
                    "falling back to base adapter: %s",
                    self.name,
                    doc_err,
                    exc_info=True,
                )
                return await super().send_image_file(chat_id, image_path, caption, reply_to, metadata=metadata)

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """Send a document/file natively as a Telegram file attachment."""
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        try:
            if not os.path.exists(file_path):
                return SendResult(success=False, error=self._missing_media_path_error("File", file_path))

            display_name = file_name or os.path.basename(file_path)
            _thread = self._metadata_thread_id(metadata)
            reply_to_id = self._reply_to_message_id_for_send(reply_to, metadata, reply_to_mode=self._reply_to_mode)
            thread_kwargs = self._thread_kwargs_for_send(
                chat_id,
                _thread,
                metadata,
                reply_to_message_id=reply_to_id,
                reply_to_mode=self._reply_to_mode
            )

            with open(file_path, "rb") as f:
                msg = await self._send_with_dm_topic_reply_anchor_retry(
                    self._bot.send_document,
                    {
                        "chat_id": int(chat_id),
                        "document": f,
                        "filename": display_name,
                        "caption": caption[:1024] if caption else None,
                        "reply_to_message_id": reply_to_id,
                        **thread_kwargs,
                        **self._notification_kwargs(metadata),
                    },
                    metadata,
                    reply_to_id,
                    "document",
                    reset_media=lambda: f.seek(0),
                )
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.warning("[%s] Failed to send document: %s", self.name, e, exc_info=True)
            return await super().send_document(chat_id, file_path, caption, file_name, reply_to, metadata=metadata)

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """Send a video natively as a Telegram video message."""
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        try:
            if not os.path.exists(video_path):
                return SendResult(success=False, error=self._missing_media_path_error("Video", video_path))

            _thread = self._metadata_thread_id(metadata)
            reply_to_id = self._reply_to_message_id_for_send(reply_to, metadata, reply_to_mode=self._reply_to_mode)
            thread_kwargs = self._thread_kwargs_for_send(
                chat_id,
                _thread,
                metadata,
                reply_to_message_id=reply_to_id,
                reply_to_mode=self._reply_to_mode
            )
            with open(video_path, "rb") as f:
                msg = await self._send_with_dm_topic_reply_anchor_retry(
                    self._bot.send_video,
                    {
                        "chat_id": int(chat_id),
                        "video": f,
                        "caption": caption[:1024] if caption else None,
                        "reply_to_message_id": reply_to_id,
                        **thread_kwargs,
                        **self._notification_kwargs(metadata),
                    },
                    metadata,
                    reply_to_id,
                    "video",
                    reset_media=lambda: f.seek(0),
                )
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.warning("[%s] Failed to send video: %s", self.name, e, exc_info=True)
            return await super().send_video(chat_id, video_path, caption, reply_to, metadata=metadata)

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send an image natively as a Telegram photo.

        Tries URL-based send first (fast, works for <5MB images).
        Falls back to downloading and uploading as file (supports up to 10MB).
        """
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        from tools.url_safety import is_safe_url
        if not is_safe_url(image_url):
            logger.warning("[%s] Blocked unsafe image URL (SSRF protection)", self.name)
            return await super().send_image(chat_id, image_url, caption, reply_to, metadata=metadata)

        try:
            # Telegram can send photos directly from URLs (up to ~5MB)
            _photo_thread = self._metadata_thread_id(metadata)
            reply_to_id = self._reply_to_message_id_for_send(reply_to, metadata, reply_to_mode=self._reply_to_mode)
            photo_thread_kwargs = self._thread_kwargs_for_send(
                chat_id,
                _photo_thread,
                metadata,
                reply_to_message_id=reply_to_id,
                reply_to_mode=self._reply_to_mode
            )
            msg = await self._send_with_dm_topic_reply_anchor_retry(
                self._bot.send_photo,
                {
                    "chat_id": int(chat_id),
                    "photo": image_url,
                    "caption": caption[:1024] if caption else None,
                    "reply_to_message_id": reply_to_id,
                    **photo_thread_kwargs,
                    **self._notification_kwargs(metadata),
                },
                metadata,
                reply_to_id,
                "URL photo",
            )
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.warning(
                "[%s] URL-based send_photo failed, trying file upload: %s",
                self.name,
                e,
                exc_info=True,
            )
            # Fallback: download and upload as file (supports up to 10MB)
            try:
                import httpx
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(image_url)
                    resp.raise_for_status()
                    image_data = resp.content

                upload_thread_kwargs = self._thread_kwargs_for_send(
                    chat_id,
                    _photo_thread,
                    metadata,
                    reply_to_message_id=reply_to_id,
                    reply_to_mode=self._reply_to_mode
                )
                msg = await self._send_with_dm_topic_reply_anchor_retry(
                    self._bot.send_photo,
                    {
                        "chat_id": int(chat_id),
                        "photo": image_data,
                        "caption": caption[:1024] if caption else None,
                        "reply_to_message_id": reply_to_id,
                        **upload_thread_kwargs,
                        **self._notification_kwargs(metadata),
                    },
                    metadata,
                    reply_to_id,
                    "uploaded photo",
                )
                return SendResult(success=True, message_id=str(msg.message_id))
            except Exception as e2:
                logger.error(
                    "[%s] File upload send_photo also failed: %s",
                    self.name,
                    e2,
                    exc_info=True,
                )
                # Final fallback: send URL as text
                return await super().send_image(chat_id, image_url, caption, reply_to, metadata=metadata)

    async def send_animation(
        self,
        chat_id: str,
        animation_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send an animated GIF natively as a Telegram animation (auto-plays inline)."""
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        try:
            _anim_thread = self._metadata_thread_id(metadata)
            reply_to_id = self._reply_to_message_id_for_send(reply_to, metadata, reply_to_mode=self._reply_to_mode)
            animation_thread_kwargs = self._thread_kwargs_for_send(
                chat_id,
                _anim_thread,
                metadata,
                reply_to_message_id=reply_to_id,
                reply_to_mode=self._reply_to_mode
            )
            msg = await self._send_with_dm_topic_reply_anchor_retry(
                self._bot.send_animation,
                {
                    "chat_id": int(chat_id),
                    "animation": animation_url,
                    "caption": caption[:1024] if caption else None,
                    "reply_to_message_id": reply_to_id,
                    **animation_thread_kwargs,
                    **self._notification_kwargs(metadata),
                },
                metadata,
                reply_to_id,
                "animation",
            )
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.error(
                "[%s] Failed to send Telegram animation, falling back to photo: %s",
                self.name,
                e,
                exc_info=True,
            )
            # Fallback: try as a regular photo
            return await self.send_image(chat_id, animation_url, caption, reply_to, metadata=metadata)

    async def send_typing(self, chat_id: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Send typing indicator."""
        if self._bot:
            _is_dm_topic: bool = False
            message_thread_id: Optional[int] = None
            try:
                _typing_thread = self._metadata_thread_id(metadata)
                _is_dm_topic = bool(metadata and metadata.get("telegram_dm_topic_reply_fallback"))
                message_thread_id = self._message_thread_id_for_typing(_typing_thread)
                await self._bot.send_chat_action(
                    chat_id=int(chat_id),
                    action="typing",
                    message_thread_id=message_thread_id,
                )
            except Exception as e:
                # For DM topic lanes, Telegram may reject message_thread_id.
                # Fall back to sending typing without thread_id so the typing
                # indicator at least appears in the main DM view.
                if _is_dm_topic and message_thread_id is not None:
                    try:
                        await self._bot.send_chat_action(
                            chat_id=int(chat_id),
                            action="typing",
                        )
                        return
                    except Exception:
                        pass
                # Typing failures are non-fatal; log at debug level only.
                logger.debug(
                    "[%s] Failed to send Telegram typing indicator: %s",
                    self.name,
                    e,
                    exc_info=True,
                )

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Get information about a Telegram chat."""
        if not self._bot:
            return {"name": "Unknown", "type": "dm"}

        try:
            chat = await self._bot.get_chat(int(chat_id))

            chat_type = "dm"
            if chat.type == ChatType.GROUP:
                chat_type = "group"
            elif chat.type == ChatType.SUPERGROUP:
                chat_type = "group"
                if chat.is_forum:
                    chat_type = "forum"
            elif chat.type == ChatType.CHANNEL:
                chat_type = "channel"

            return {
                "name": chat.title or chat.full_name or str(chat_id),
                "type": chat_type,
                "username": chat.username,
                "is_forum": getattr(chat, "is_forum", False),
            }
        except Exception as e:
            logger.error(
                "[%s] Failed to get Telegram chat info for %s: %s",
                self.name,
                chat_id,
                e,
                exc_info=True,
            )
            return {"name": str(chat_id), "type": "dm", "error": str(e)}

    def format_message(self, content: str) -> str:
        """
        Convert standard markdown to Telegram MarkdownV2 format.

        Protected regions (code blocks, inline code) are extracted first so
        their contents are never modified.  Standard markdown constructs
        (headers, bold, italic, links) are translated to MarkdownV2 syntax,
        and all remaining special characters are escaped.
        """
        if not content:
            return content

        placeholders: dict = {}
        counter = [0]

        def _ph(value: str) -> str:
            """Stash *value* behind a placeholder token that survives escaping."""
            key = f"\x00PH{counter[0]}\x00"
            counter[0] += 1
            placeholders[key] = value
            return key

        text = content

        # 0) Rewrite GFM-style pipe tables into Telegram-friendly row groups
        #    before the normal MarkdownV2 conversions run.
        text = _wrap_markdown_tables(text)

        # 1) Protect fenced code blocks (``` ... ```)
        #    Per MarkdownV2 spec, \ and ` inside pre/code must be escaped.
        def _protect_fenced(m):
            raw = m.group(0)
            # Split off opening ``` (with optional language) and closing ```
            open_end = raw.index('\n') + 1 if '\n' in raw[3:] else 3
            opening = raw[:open_end]
            body_and_close = raw[open_end:]
            body = body_and_close[:-3]
            body = body.replace('\\', '\\\\').replace('`', '\\`')
            return _ph(opening + body + '```')

        text = re.sub(
            r'(```(?:[^\n]*\n)?[\s\S]*?```)',
            _protect_fenced,
            text,
        )

        # 2) Protect inline code (`...`)
        #    Escape \ inside inline code per MarkdownV2 spec.
        text = re.sub(
            r'(`[^`]+`)',
            lambda m: _ph(m.group(0).replace('\\', '\\\\')),
            text,
        )

        # 3) Convert markdown links – escape the display text; inside the URL
        #    only ')' and '\' need escaping per the MarkdownV2 spec.
        def _convert_link(m):
            display = _escape_mdv2(m.group(1))
            url = m.group(2).replace('\\', '\\\\').replace(')', '\\)')
            return _ph(f'[{display}]({url})')

        text = re.sub(r'\[([^\]]+)\]\(([^()]*(?:\([^()]*\)[^()]*)*)\)', _convert_link, text)

        # 4) Convert markdown headers (## Title) → bold *Title*
        def _convert_header(m):
            inner = m.group(1).strip()
            # Strip redundant bold markers that may appear inside a header
            inner = re.sub(r'\*\*(.+?)\*\*', r'\1', inner)
            return _ph(f'*{_escape_mdv2(inner)}*')

        text = re.sub(
            r'^#{1,6}\s+(.+)$', _convert_header, text, flags=re.MULTILINE
        )

        # 5) Convert bold: **text** → *text* (MarkdownV2 bold)
        text = re.sub(
            r'\*\*(.+?)\*\*',
            lambda m: _ph(f'*{_escape_mdv2(m.group(1))}*'),
            text,
        )

        # 6) Convert italic: *text* (single asterisk) → _text_ (MarkdownV2 italic)
        #    [^*\n]+ prevents matching across newlines (which would corrupt
        #    bullet lists using * markers and multi-line content).
        text = re.sub(
            r'\*([^*\n]+)\*',
            lambda m: _ph(f'_{_escape_mdv2(m.group(1))}_'),
            text,
        )

        # 7) Convert strikethrough: ~~text~~ → ~text~ (MarkdownV2)
        text = re.sub(
            r'~~(.+?)~~',
            lambda m: _ph(f'~{_escape_mdv2(m.group(1))}~'),
            text,
        )

        # 8) Convert spoiler: ||text|| → ||text|| (protect from | escaping)
        text = re.sub(
            r'\|\|(.+?)\|\|',
            lambda m: _ph(f'||{_escape_mdv2(m.group(1))}||'),
            text,
        )

        # 9) Convert blockquotes: > at line start → protect > from escaping
        #    Handle both regular blockquotes (> text) and expandable blockquotes
        #    (Telegram MarkdownV2: **> for expandable start, || to end the quote)
        def _convert_blockquote(m):
            prefix = m.group(1)  # >, >>, >>>, **>, or **>> etc.
            content = m.group(2)
            # Check if content ends with || (expandable blockquote end marker)
            # In this case, preserve the trailing || unescaped for Telegram
            if prefix.startswith('**') and content.endswith('||'):
                return _ph(f'{prefix} {_escape_mdv2(content[:-2])}||')
            return _ph(f'{prefix} {_escape_mdv2(content)}')

        text = re.sub(
            r'^((?:\*\*)?>{1,3}) (.+)$',
            _convert_blockquote,
            text,
            flags=re.MULTILINE,
        )

        # 10) Escape remaining special characters in plain text
        text = _escape_mdv2(text)

        # 11) Restore placeholders in reverse insertion order so that
        #    nested references (a placeholder inside another) resolve correctly.
        for key in reversed(list(placeholders.keys())):
            text = text.replace(key, placeholders[key])

        # 12) Safety net: escape unescaped ( ) { } that slipped through
        #     placeholder processing.  Split the text into code/non-code
        #     segments so we never touch content inside ``` or ` spans.
        _code_split = re.split(r'(```[\s\S]*?```|`[^`]+`)', text)
        _safe_parts = []
        for _idx, _seg in enumerate(_code_split):
            if _idx % 2 == 1:
                # Inside code span/block — leave untouched
                _safe_parts.append(_seg)
            else:
                # Outside code — escape bare ( ) { }
                def _esc_bare(m, _seg=_seg):
                    s = m.start()
                    ch = m.group(0)
                    # Already escaped
                    if s > 0 and _seg[s - 1] == '\\':
                        return ch
                    # ( that opens a MarkdownV2 link [text](url)
                    if ch == '(' and s > 0 and _seg[s - 1] == ']':
                        return ch
                    # ) that closes a link URL
                    if ch == ')':
                        before = _seg[:s]
                        if '](http' in before or '](' in before:
                            # Check depth
                            depth = 0
                            for j in range(s - 1, max(s - 2000, -1), -1):
                                if _seg[j] == '(':
                                    depth -= 1
                                    if depth < 0:
                                        if j > 0 and _seg[j - 1] == ']':
                                            return ch
                                        break
                                elif _seg[j] == ')':
                                    depth += 1
                    return '\\' + ch
                _safe_parts.append(re.sub(r'[(){}]', _esc_bare, _seg))
        text = ''.join(_safe_parts)

        return text

    # ── Group mention gating ──────────────────────────────────────────────

    def _telegram_require_mention(self) -> bool:
        """Return whether group chats should require an explicit bot trigger."""
        configured = self.config.extra.get("require_mention")
        if configured is not None:
            if isinstance(configured, str):
                return configured.lower() in {"true", "1", "yes", "on"}
            return bool(configured)
        return os.getenv("TELEGRAM_REQUIRE_MENTION", "false").lower() in {"true", "1", "yes", "on"}

    def _telegram_observe_unmentioned_group_messages(self) -> bool:
        """Return whether skipped unmentioned group messages are stored as context.

        When enabled with ``require_mention``, Telegram matches the Yuanbao /
        OpenClaw-style group UX: observe ordinary group chatter in the session
        transcript, but only dispatch the agent when the bot is explicitly
        addressed.
        """
        configured = self.config.extra.get("observe_unmentioned_group_messages")
        if configured is None:
            configured = self.config.extra.get("ingest_unmentioned_group_messages")
        if configured is not None:
            if isinstance(configured, str):
                return configured.lower() in {"true", "1", "yes", "on"}
            return bool(configured)
        return os.getenv("TELEGRAM_OBSERVE_UNMENTIONED_GROUP_MESSAGES", "false").lower() in {"true", "1", "yes", "on"}

    def _telegram_guest_mode(self) -> bool:
        """Return whether non-allowlisted groups may trigger via direct @mention."""
        configured = self.config.extra.get("guest_mode")
        if configured is not None:
            if isinstance(configured, str):
                return configured.lower() in {"true", "1", "yes", "on"}
            return bool(configured)
        return os.getenv("TELEGRAM_GUEST_MODE", "false").lower() in {"true", "1", "yes", "on"}

    def _telegram_exclusive_bot_mentions(self) -> bool:
        """Return whether explicit @...bot mentions exclusively route group messages."""
        configured = self.config.extra.get("exclusive_bot_mentions")
        if configured is not None:
            if isinstance(configured, str):
                return configured.lower() in {"true", "1", "yes", "on"}
            return bool(configured)
        return os.getenv("TELEGRAM_EXCLUSIVE_BOT_MENTIONS", "true").lower() in {"true", "1", "yes", "on"}

    def _telegram_free_response_chats(self) -> set[str]:
        raw = self.config.extra.get("free_response_chats")
        if raw is None:
            raw = os.getenv("TELEGRAM_FREE_RESPONSE_CHATS", "")
        if isinstance(raw, list):
            return {str(part).strip() for part in raw if str(part).strip()}
        return {part.strip() for part in str(raw).split(",") if part.strip()}

    def _telegram_allowed_chats(self) -> set[str]:
        """Return the whitelist of group/supergroup chat IDs the bot will respond in.

        When non-empty, group messages from chats NOT in this set are
        silently ignored unless ``guest_mode`` is enabled and the bot is
        explicitly @mentioned.  DMs are never filtered.
        Empty set means no restriction (fully backward compatible).
        """
        raw = self.config.extra.get("allowed_chats")
        if raw is None:
            raw = os.getenv("TELEGRAM_ALLOWED_CHATS", "")
        if isinstance(raw, list):
            return {str(part).strip() for part in raw if str(part).strip()}
        return {part.strip() for part in str(raw).split(",") if part.strip()}

    def _telegram_group_allowed_chats(self) -> set[str]:
        """Return Telegram chats authorized at group scope."""
        raw = self.config.extra.get("group_allowed_chats")
        if raw is None:
            raw = os.getenv("TELEGRAM_GROUP_ALLOWED_CHATS", "")
        if isinstance(raw, list):
            return {str(part).strip() for part in raw if str(part).strip()}
        return {part.strip() for part in str(raw).split(",") if part.strip()}

    def _telegram_observe_allowed_chats(self) -> set[str]:
        """Chats where observed group context may use a shared source.

        ``group_allowed_chats`` is the gateway authorization allowlist for
        user-less group sources.  ``allowed_chats`` remains an optional response
        gate; when set, observed context must satisfy both lists.
        """
        group_allowed = self._telegram_group_allowed_chats()
        if not group_allowed:
            return set()
        response_allowed = self._telegram_allowed_chats()
        if response_allowed:
            return group_allowed & response_allowed
        return group_allowed

    def _telegram_allowed_topics(self) -> set[str]:
        """Return the whitelist of Telegram forum topic IDs this bot handles.

        When non-empty, group/supergroup messages from other topics are
        silently ignored. DMs are never filtered by topic. Telegram may omit
        ``message_thread_id`` for the forum General topic, so ``None`` is
        treated as topic ``1`` for matching purposes.
        """
        raw = self.config.extra.get("allowed_topics")
        if raw is None:
            raw = os.getenv("TELEGRAM_ALLOWED_TOPICS", "")
        if isinstance(raw, list):
            return {str(part).strip() for part in raw if str(part).strip()}
        return {part.strip() for part in str(raw).split(",") if part.strip()}

    def _telegram_ignored_threads(self) -> set[int]:
        raw = self.config.extra.get("ignored_threads")
        if raw is None:
            raw = os.getenv("TELEGRAM_IGNORED_THREADS", "")

        if isinstance(raw, list):
            values = raw
        else:
            values = str(raw).split(",")

        ignored: set[int] = set()
        for value in values:
            text = str(value).strip()
            if not text:
                continue
            try:
                ignored.add(int(text))
            except (TypeError, ValueError):
                logger.warning("[%s] Ignoring invalid Telegram thread id: %r", self.name, value)
        return ignored

    def _compile_mention_patterns(self) -> List[re.Pattern]:
        """Compile optional regex wake-word patterns for group triggers."""
        patterns = self.config.extra.get("mention_patterns")
        if patterns is None:
            raw = os.getenv("TELEGRAM_MENTION_PATTERNS", "").strip()
            if raw:
                try:
                    loaded = json.loads(raw)
                except Exception:
                    loaded = [part.strip() for part in raw.splitlines() if part.strip()]
                    if not loaded:
                        loaded = [part.strip() for part in raw.split(",") if part.strip()]
                patterns = loaded

        if patterns is None:
            return []
        if isinstance(patterns, str):
            patterns = [patterns]
        if not isinstance(patterns, list):
            logger.warning(
                "[%s] telegram mention_patterns must be a list or string; got %s",
                self.name,
                type(patterns).__name__,
            )
            return []

        compiled: List[re.Pattern] = []
        for pattern in patterns:
            if not isinstance(pattern, str) or not pattern.strip():
                continue
            try:
                compiled.append(re.compile(pattern, re.IGNORECASE))
            except re.error as exc:
                logger.warning("[%s] Invalid Telegram mention pattern %r: %s", self.name, pattern, exc)
        if compiled:
            logger.info("[%s] Loaded %d Telegram mention pattern(s)", self.name, len(compiled))
        return compiled

    def _is_group_chat(self, message: Message) -> bool:
        chat = getattr(message, "chat", None)
        if not chat:
            return False
        chat_type = str(getattr(chat, "type", "")).split(".")[-1].lower()
        return chat_type in {"group", "supergroup"}

    def _is_reply_to_bot(self, message: Message) -> bool:
        if not self._bot or not getattr(message, "reply_to_message", None):
            return False
        reply_user = getattr(message.reply_to_message, "from_user", None)
        return bool(reply_user and getattr(reply_user, "id", None) == getattr(self._bot, "id", None))

    @staticmethod
    def _extract_bot_mention_usernames(message: Message) -> set[str]:
        """Extract explicit Telegram bot usernames mentioned in text/captions.

        Telegram bot usernames are 5-32 characters and must end in "bot".
        Entity mentions are authoritative. The raw-text fallback is intentionally narrow so
        entity-less mobile/client variants still work without treating email
        addresses or arbitrary substrings as bot mentions.
        """
        mentioned_bot_usernames: set[str] = set()

        def _iter_sources():
            yield getattr(message, "text", None) or "", getattr(message, "entities", None) or []
            yield getattr(message, "caption", None) or "", getattr(message, "caption_entities", None) or []

        for source_text, entities in _iter_sources():
            for entity in entities:
                entity_type = str(getattr(entity, "type", "")).split(".")[-1].lower()
                if entity_type not in {"mention", "bot_command"}:
                    continue
                offset = int(getattr(entity, "offset", -1))
                length = int(getattr(entity, "length", 0))
                if offset < 0 or length <= 0:
                    continue

                entity_text = source_text[offset:offset + length].strip()
                if entity_type == "mention":
                    handle = entity_text.lstrip("@").lower()
                    if re.fullmatch(r"[a-z0-9_]{2,29}bot", handle, re.IGNORECASE):
                        mentioned_bot_usernames.add(handle)
                    continue

                # Telegram emits /cmd@botname as one bot_command entity, not as
                # a separate mention entity. Treat that suffix as an explicit
                # bot address for exclusive multi-bot routing even when the
                # group has require_mention/free-response disabled.
                at_index = entity_text.find("@")
                if at_index < 0:
                    continue
                command_target = entity_text[at_index + 1:].strip().lower()
                if re.fullmatch(r"[a-z0-9_]{2,29}bot", command_target, re.IGNORECASE):
                    mentioned_bot_usernames.add(command_target)

        # Entity-less fallback for older/client-specific updates. If Telegram
        # supplied entities for a source, trust them and do not regex-rescue
        # malformed/URL/code spans that the server did not mark as mentions.
        for raw_text, entities in _iter_sources():
            if not raw_text or entities:
                continue
            for match in re.finditer(r"(?i)(?<![A-Za-z0-9_`/])@([A-Za-z0-9_]{2,29}bot)\b", raw_text):
                mentioned_bot_usernames.add(match.group(1).lower())

        return mentioned_bot_usernames

    def _message_mentions_bot(self, message: Message) -> bool:
        if not self._bot:
            return False

        bot_username = (getattr(self._bot, "username", None) or "").lstrip("@").lower()
        bot_id = getattr(self._bot, "id", None)
        expected = f"@{bot_username}" if bot_username else None

        def _iter_sources():
            yield getattr(message, "text", None) or "", getattr(message, "entities", None) or []
            yield getattr(message, "caption", None) or "", getattr(message, "caption_entities", None) or []

        # Telegram parses mentions server-side and emits MessageEntity objects
        # (type=mention for @username, type=text_mention for @FirstName targeting
        # a user without a public username). Those entities are authoritative:
        # raw substring matches like "foo@hermes_bot.example" are not mentions
        # (bug #12545). Entities also correctly handle @handles inside URLs, code
        # blocks, and quoted text, where a regex scan would over-match.
        for source_text, entities in _iter_sources():
            for entity in entities:
                entity_type = str(getattr(entity, "type", "")).split(".")[-1].lower()
                if entity_type == "mention" and expected:
                    offset = int(getattr(entity, "offset", -1))
                    length = int(getattr(entity, "length", 0))
                    if offset < 0 or length <= 0:
                        continue
                    if source_text[offset:offset + length].strip().lower() == expected:
                        return True
                elif entity_type == "text_mention":
                    user = getattr(entity, "user", None)
                    if user and getattr(user, "id", None) == bot_id:
                        return True
                elif entity_type == "bot_command" and expected:
                    # Telegram's official group-disambiguation form for slash
                    # commands (``/cmd@botname``) is emitted as a single
                    # ``bot_command`` entity covering the whole span — there
                    # is no accompanying ``mention`` entity. Treat it as a
                    # direct address to this bot when the ``@botname`` suffix
                    # matches. This is the form Telegram's own command menu
                    # autocomplete produces in groups, so dropping it at the
                    # mention gate would break /new, /reset, /help, ... for
                    # every group that has ``require_mention`` enabled (#15415).
                    offset = int(getattr(entity, "offset", -1))
                    length = int(getattr(entity, "length", 0))
                    if offset < 0 or length <= 0:
                        continue
                    command_text = source_text[offset:offset + length]
                    at_index = command_text.find("@")
                    if at_index < 0:
                        continue
                    if command_text[at_index:].strip().lower() == expected:
                        return True
        if bot_username and re.fullmatch(r"[a-z0-9_]{2,29}bot", bot_username, re.IGNORECASE):
            return bot_username in self._extract_bot_mention_usernames(message)
        return False

    def _explicit_bot_mentions_exclude_self(self, message: Message) -> bool:
        """Return True when explicit bot handles target other bots, not this one.

        Telegram groups can contain several Hermes bot profiles. A message like
        ``@bot3 hi @bot4`` must not wake ``@bot1`` through reply/wake-word
        fallbacks. Treat explicit bot-handle mentions as an exclusive routing
        hint: if at least one @...bot username is present and none matches this
        adapter's own bot username, this adapter should ignore the message.

        MessageEntity values are preferred, but some Telegram clients expose
        selected bot handles as plain text in group messages. The raw-text
        fallback is intentionally limited to usernames ending in "bot", which
        Telegram requires for bot accounts.
        """
        if not self._bot:
            return False

        bot_username = (getattr(self._bot, "username", None) or "").lstrip("@").lower()
        if not bot_username:
            return False

        mentioned_bot_usernames = self._extract_bot_mention_usernames(message)
        return bool(mentioned_bot_usernames) and bot_username not in mentioned_bot_usernames

    def _message_matches_mention_patterns(self, message: Message) -> bool:
        if not self._mention_patterns:
            return False
        for candidate in (getattr(message, "text", None), getattr(message, "caption", None)):
            if not candidate:
                continue
            for pattern in self._mention_patterns:
                if pattern.search(candidate):
                    return True
        return False

    def _is_guest_mention(self, message: Message) -> bool:
        """Return True for the narrow guest-mode bypass: explicit bot mention.

        The caller (:meth:`_should_process_message`) has already verified
        the message is a group chat, so that check is not repeated here.
        """
        return self._telegram_guest_mode() and self._message_mentions_bot(message)

    def _clean_bot_trigger_text(self, text: Optional[str]) -> Optional[str]:
        if not text or not self._bot or not getattr(self._bot, "username", None):
            return text
        username = re.escape(self._bot.username)
        cleaned = re.sub(rf"(?i)@{username}\b[,:\-]*\s*", "", text).strip()
        return cleaned or text

    def _should_observe_unmentioned_group_message(self, message: Message) -> bool:
        """Return True when a group message should be stored but not dispatched."""
        if not self._telegram_observe_unmentioned_group_messages():
            return False
        if not self._is_group_chat(message):
            return False

        thread_id = getattr(message, "message_thread_id", None)
        allowed_topics = self._telegram_allowed_topics()
        if allowed_topics:
            topic_id = str(thread_id) if thread_id is not None else self._GENERAL_TOPIC_THREAD_ID
            if topic_id not in allowed_topics:
                return False

        if thread_id is not None:
            try:
                if int(thread_id) in self._telegram_ignored_threads():
                    return False
            except (TypeError, ValueError):
                return False

        chat_id_str = str(getattr(getattr(message, "chat", None), "id", ""))
        if self._telegram_exclusive_bot_mentions() and self._explicit_bot_mentions_exclude_self(message):
            return False

        allowed = self._telegram_observe_allowed_chats()
        # Observed context is shared at chat/topic scope so a later trigger from
        # another user can see it.  Require an explicit chat allowlist; that
        # keeps shared observed history limited to operator-approved groups and
        # lets gateway authorization pass even after the shared session source
        # drops the per-sender user_id.
        if not allowed or chat_id_str not in allowed:
            return False

        # Only observe messages skipped by the require_mention gate.  If the
        # message would be processed normally, let the dispatcher handle it;
        # if require_mention is disabled, every group message is a request.
        if chat_id_str in self._telegram_free_response_chats():
            return False
        if not self._telegram_require_mention():
            return False
        if self._is_reply_to_bot(message):
            return False
        if self._message_mentions_bot(message):
            return False
        if self._message_matches_mention_patterns(message):
            return False
        return True

    def _telegram_group_observe_shared_source(self, source):
        """Return a chat/topic-scoped source for observed Telegram group context."""
        return dataclasses.replace(source, user_id=None, user_name=None, user_id_alt=None)

    def _telegram_group_observe_attributed_text(self, event: MessageEvent) -> str:
        user_id = event.source.user_id or "unknown"
        sender = event.source.user_name or user_id
        return f"[{sender}|{user_id}]\n{event.text or ''}"

    def _telegram_group_observe_channel_prompt(self) -> str:
        username = getattr(getattr(self, "_bot", None), "username", None) or "unknown"
        bot_id = getattr(getattr(self, "_bot", None), "id", None) or "unknown"
        return (
            "You are handling a Telegram group chat message.\n"
            f"- Your identity: user_id={bot_id}, @-mention name in this group=@{username}\n"
            "- observed Telegram group context may be provided in a separate context-only block "
            "before the current message; it is not necessarily addressed to you.\n"
            "- Treat only the current new message as a request explicitly directed at you, "
            "and use observed context only when the current message asks for it."
        )

    def _apply_telegram_group_observe_attribution(self, event: MessageEvent) -> MessageEvent:
        """Align triggered group turns with observed-history attribution."""
        if not self._telegram_observe_unmentioned_group_messages():
            return event
        raw_message = getattr(event, "raw_message", None)
        if not raw_message or not self._is_group_chat(raw_message):
            return event
        chat_id_str = str(getattr(getattr(raw_message, "chat", None), "id", ""))
        allowed = self._telegram_observe_allowed_chats()
        if not allowed or chat_id_str not in allowed:
            return event
        shared_source = self._telegram_group_observe_shared_source(event.source)
        observe_prompt = self._telegram_group_observe_channel_prompt()
        channel_prompt = f"{event.channel_prompt}\n\n{observe_prompt}" if event.channel_prompt else observe_prompt
        if event.message_type == MessageType.COMMAND:
            return dataclasses.replace(
                event,
                source=shared_source,
                channel_prompt=channel_prompt,
            )
        return dataclasses.replace(
            event,
            text=self._telegram_group_observe_attributed_text(event),
            source=shared_source,
            channel_prompt=channel_prompt,
        )

    def _media_message_type(self, msg: Message) -> MessageType:
        """Classify a Telegram media message into a MessageType."""
        if msg.sticker:
            return MessageType.STICKER
        if msg.photo:
            return MessageType.PHOTO
        if msg.video:
            return MessageType.VIDEO
        if msg.audio:
            return MessageType.AUDIO
        if msg.voice:
            return MessageType.VOICE
        return MessageType.DOCUMENT

    async def _cache_observed_media(self, msg: Message, event: MessageEvent) -> None:
        """Cache an unmentioned group attachment and annotate the observed text.

        Passive group traffic, so downloads are bounded by the same
        ``_max_doc_bytes`` limit as the addressed document path. Oversized or
        unsupported attachments are noted in the transcript without downloading.
        """
        from gateway.platforms.base import cache_media_bytes

        source, filename, mime, kind = self._observed_media_source(msg)
        if source is None:
            return

        max_bytes = getattr(self, "_max_doc_bytes", 20 * 1024 * 1024)
        file_size = getattr(source, "file_size", None)
        try:
            size = int(file_size or 0)
        except (TypeError, ValueError):
            size = 0
        if not (0 < size <= max_bytes):
            limit_mb = max_bytes // (1024 * 1024)
            event.text = self._append_observed_note(
                event.text,
                f"[Observed Telegram attachment too large or unverifiable. Maximum: {limit_mb} MB.]",
            )
            logger.info("[Telegram] Observed group attachment skipped (size=%s)", file_size)
            return

        try:
            file_obj = await source.get_file()
            data = bytes(await file_obj.download_as_bytearray())
            if not filename:
                filename = os.path.basename(getattr(file_obj, "file_path", "") or "")
            cached = cache_media_bytes(data, filename=filename, mime_type=mime, default_kind=kind)
        except Exception as exc:
            logger.warning("[Telegram] Failed to cache observed group media: %s", exc, exc_info=True)
            return

        if cached is None:
            event.text = self._append_observed_note(
                event.text, "[Observed Telegram attachment: unsupported type, not cached.]"
            )
            return

        event.media_urls = [cached.path]
        event.media_types = [cached.media_type]
        if cached.kind == "image":
            event.message_type = MessageType.PHOTO
        elif cached.kind == "video":
            event.message_type = MessageType.VIDEO
        event.text = self._append_observed_note(event.text, cached.context_note())
        logger.info("[Telegram] Cached observed group %s at %s", cached.kind, cached.path)

    async def _cache_replied_media(self, msg: Any, event: MessageEvent) -> None:
        """Cache media from the message this turn replies to, if any."""
        from gateway.platforms.base import cache_media_bytes

        reply_msg = getattr(msg, "reply_to_message", None)
        if reply_msg is None:
            return
        source, filename, mime, kind = self._observed_media_source(reply_msg)
        if source is None:
            return

        max_bytes = getattr(self, "_max_doc_bytes", 20 * 1024 * 1024)
        file_size = getattr(source, "file_size", None)
        try:
            size = int(file_size or 0)
        except (TypeError, ValueError):
            size = 0
        if not (0 < size <= max_bytes):
            return

        try:
            file_obj = await source.get_file()
            data = bytes(await file_obj.download_as_bytearray())
            if not filename:
                filename = os.path.basename(getattr(file_obj, "file_path", "") or "")
            cached = cache_media_bytes(data, filename=filename, mime_type=mime, default_kind=kind)
        except Exception as exc:
            logger.warning("[Telegram] Failed to cache replied-to media: %s", exc, exc_info=True)
            return

        if cached is None:
            return

        event.media_urls.append(cached.path)
        event.media_types.append(cached.media_type)
        if len(event.media_urls) == 1:
            if cached.kind == "image":
                event.message_type = MessageType.PHOTO
            elif cached.kind == "video":
                event.message_type = MessageType.VIDEO
        event.text = self._append_observed_note(
            event.text,
            f"[Replied-to {cached.kind} '{cached.display_name}' saved at: {cached.path}]",
        )
        logger.info("[Telegram] Cached replied-to %s at %s", cached.kind, cached.path)

    def _observed_media_source(self, msg: Message):
        """Return (telegram_file_source, filename, mime, default_kind) or Nones."""
        if msg.photo:
            return msg.photo[-1], "", "", "image"
        if msg.video:
            return msg.video, "", "video/mp4", "video"
        if msg.voice:
            return msg.voice, "voice.ogg", "audio/ogg", "audio"
        if msg.audio:
            return msg.audio, getattr(msg.audio, "file_name", "") or "", "", "audio"
        if msg.document:
            doc = msg.document
            return doc, doc.file_name or "", (doc.mime_type or "").lower(), None
        return None, "", "", None

    @staticmethod
    def _append_observed_note(existing: Optional[str], note: str) -> str:
        if not note:
            return existing or ""
        if not existing:
            return note
        return f"{existing}\n\n{note}"

    def _observe_unmentioned_group_message(
        self,
        message: Message,
        msg_type: MessageType,
        update_id: Optional[int] = None,
        event: Optional[MessageEvent] = None,
    ) -> None:
        """Append skipped group chatter to the target session without dispatching."""
        store = getattr(self, "_session_store", None)
        if not store:
            return
        try:
            event = event or self._build_message_event(message, msg_type, update_id=update_id)
            shared_source = self._telegram_group_observe_shared_source(event.source)
            session_entry = store.get_or_create_session(shared_source)
            entry = {
                "role": "user",
                "content": self._telegram_group_observe_attributed_text(event),
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "observed": True,
            }
            if event.message_id:
                entry["message_id"] = str(event.message_id)
            store.append_to_transcript(session_entry.session_id, entry)
            adapter_name = getattr(self, "name", "telegram")
            logger.info(
                "[%s] Telegram group message observed (no bot trigger): chat=%s from=%s",
                adapter_name,
                getattr(getattr(message, "chat", None), "id", "unknown"),
                event.source.user_id or "unknown",
            )
        except Exception as exc:
            adapter_name = getattr(self, "name", "telegram")
            logger.warning("[%s] Failed to observe Telegram group message: %s", adapter_name, exc)

    def _should_process_message(self, message: Message, *, is_command: bool = False) -> bool:
        """Apply Telegram group trigger rules.

        DMs remain unrestricted. Group/supergroup messages are accepted when:
        - the chat passes the ``allowed_chats`` whitelist (when set), or
          ``guest_mode`` is enabled and the bot is explicitly mentioned
        - the chat is explicitly allowlisted in ``free_response_chats``
        - ``require_mention`` is disabled
        - the message replies to the bot
        - the bot is @mentioned
        - the text/caption matches a configured regex wake-word pattern

        When ``allowed_chats`` is non-empty, it remains a hard gate except for
        the narrow ``guest_mode`` bypass: group/supergroup messages that
        explicitly @mention this bot. Replies and regex wake words do not bypass
        ``allowed_chats``. When ``require_mention`` is enabled, slash commands are not given
        special treatment — they must pass the same mention/reply checks
        as any other group message.  Users can still trigger commands via
        the Telegram bot menu (``/command@botname``) or by explicitly
        mentioning the bot (``@botname /command``), both of which are
        recognised as mentions by :meth:`_message_mentions_bot`.
        """
        if not self._is_group_chat(message):
            return True

        thread_id = getattr(message, "message_thread_id", None)
        allowed_topics = self._telegram_allowed_topics()
        if allowed_topics:
            topic_id = str(thread_id) if thread_id is not None else self._GENERAL_TOPIC_THREAD_ID
            if topic_id not in allowed_topics:
                return False

        # Check ignored_threads first — applies to both groups and DM topics
        if thread_id is not None:
            try:
                if int(thread_id) in self._telegram_ignored_threads():
                    return False
            except (TypeError, ValueError):
                logger.warning("[%s] Ignoring non-numeric Telegram message_thread_id: %r", self.name, thread_id)

        if not self._is_group_chat(message):
            # Root DM (non-topic): ignore if ignore_root_dm is configured
            if thread_id is None and self.config.extra.get("ignore_root_dm", False):
                chat_id = str(getattr(getattr(message, "chat", None), "id", ""))
                if not is_command and chat_id in self._dm_topic_chat_ids:
                    return False
            return True

        chat_id_str = str(getattr(getattr(message, "chat", None), "id", ""))

        if self._telegram_exclusive_bot_mentions() and self._explicit_bot_mentions_exclude_self(message):
            return False

        # Resolve guest-mode mention bypass once so _message_mentions_bot
        # is not called redundantly in the normal flow below.
        guest_mention = self._is_guest_mention(message)

        # allowed_chats check (whitelist). When set, group messages from chats
        # outside the whitelist are ignored unless guest_mode permits this
        # exact message as an explicit direct mention. DMs are excluded above.
        allowed = self._telegram_allowed_chats()
        if allowed and chat_id_str not in allowed:
            return guest_mention

        if guest_mention:
            return True
        if chat_id_str in self._telegram_free_response_chats():
            return True
        if not self._telegram_require_mention():
            return True
        if self._is_reply_to_bot(message):
            return True
        # When guest_mode is True, _is_guest_mention already called
        # _message_mentions_bot above — skip the redundant second call.
        if not self._telegram_guest_mode() and self._message_mentions_bot(message):
            return True
        return self._message_matches_mention_patterns(message)

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


    def _cockpit_api_sync(self, method: str, path: str, payload: dict | None = None, timeout: int = 20) -> dict:
        """Call local Repo Cockpit backend without consuming LLM quota."""
        url = "http://127.0.0.1:8765" + path
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = _urlrequest.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
        try:
            with _urlrequest.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except _urlerror.HTTPError as exc:
            return {"ok": False, "error_code": exc.code, "description": exc.read().decode("utf-8", "replace")[:1200]}
        except Exception as exc:
            return {"ok": False, "description": str(exc)}

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
        mode = normalize_cockpit_mode(mode)
        if mode == "autopilot":
            return "Autopilot"
        if mode == "pilote":
            return "Pilote"
        return "Ask review"

    def _mode_note(self, mode: str) -> str:
        mode = normalize_cockpit_mode(mode)
        if mode == "autopilot":
            return "peut merger automatiquement seulement après PR, gates, secret scan et review indépendante high"
        if mode == "pilote":
            return "cadre d'abord Architect/Deploy, pose les questions critiques, puis avance en autonomie avec PR et gates"
        return "prépare, teste et ouvre une PR, puis attend ta validation avant merge"

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
        base = os.getenv(
            "REPO_COCKPIT_URL",
            "https://cockpit.134.122.73.242.sslip.io/?v=20260620-immediate-close",
        )
        parsed = urllib.parse.urlsplit(base)
        clean_path = "/" + path.lstrip("/")
        existing = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        existing.update({k: v for k, v in params.items() if v is not None})
        existing["v"] = str(int(time.time()))
        return urllib.parse.urlunsplit((
            parsed.scheme,
            parsed.netloc,
            clean_path,
            urllib.parse.urlencode(existing),
            parsed.fragment,
        ))

    def _new_chat_keyboard(self, mode: str) -> InlineKeyboardMarkup:
        mode = normalize_cockpit_mode(mode)
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton(("✓ Ask review" if mode == "ask_review" else "Ask review"), callback_data="rcn:mode:ask_review"),
                InlineKeyboardButton(("✓ Pilote" if mode == "pilote" else "Pilote"), callback_data="rcn:mode:pilote"),
            ],
            [
                InlineKeyboardButton(("✓ Autopilot" if mode == "autopilot" else "Autopilot"), callback_data="rcn:mode:autopilot"),
            ],
            [
                InlineKeyboardButton("Projet GitHub existant", callback_data=f"rcn:existing:{mode}"),
            ],
            [
                InlineKeyboardButton("Start from scratch", callback_data=f"rcn:scratch:{mode}"),
            ],
            [
                InlineKeyboardButton("Annuler", callback_data="rcn:cancel"),
            ],
        ])


    def _pilot_default_reasoning(self, user_id: str, origin: str | None = None, intent: str | None = None) -> str:
        prefs = self._get_cockpit_llm_prefs(user_id)
        selected = str(prefs.get("reasoning_effort") or "medium").lower()
        if selected in {"high", "xhigh"}:
            return selected
        if origin == "from_scratch" or intent in {"deploy", "review_harden"}:
            return "high"
        return selected if selected in {"low", "medium"} else "medium"

    def _pilot_intent_title(self, intent: str | None) -> str:
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

    def _pilot_existing_intent_keyboard(self, mode: str = "pilote") -> InlineKeyboardMarkup:
        mode = normalize_cockpit_mode(mode)
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("Comprendre / auditer le repo", callback_data=f"rcn:intent:audit_repo:{mode}")],
            [InlineKeyboardButton("Modifier / ajouter une feature", callback_data=f"rcn:intent:feature_work:{mode}")],
            [InlineKeyboardButton("Corriger un bug", callback_data=f"rcn:intent:debug_fix:{mode}")],
            [InlineKeyboardButton("Déployer / vérifier prod", callback_data=f"rcn:intent:deploy:{mode}")],
            [InlineKeyboardButton("Refactor / sécuriser", callback_data=f"rcn:intent:review_harden:{mode}")],
            [InlineKeyboardButton("Je ne sais pas", callback_data=f"rcn:intent:pilot_discovery:{mode}")],
            [InlineKeyboardButton("Retour", callback_data=f"rcn:mode:{mode}"), InlineKeyboardButton("Annuler", callback_data="rcn:cancel")],
        ])

    def _pilot_waiting_prompt_text(self, *, origin: str, intent: str, repo: str | None = None, user_id: str = "") -> str:
        reasoning = self._pilot_default_reasoning(user_id, origin, intent) if user_id else "high"
        lines = [
            "<b>🧭 Pilote prêt</b>",
            "",
            f"Source : <b>{'Start from scratch' if origin == 'from_scratch' else 'Projet GitHub existant'}</b>",
            f"Route : <b>{_html.escape(self._pilot_intent_title(intent))}</b>",
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
        full_name = str(repo.get("nameWithOwner") or repo.get("name") or "Repo")
        name = full_name.split("/", 1)[-1]
        clean = re.sub(r"\s+", " ", name).strip() or "Repo"
        if len(clean) > 30:
            clean = clean[:29].rstrip() + "…"
        visibility = "privé" if repo.get("isPrivate") else "public"
        return f"{clean} · {visibility}"

    def _repo_new_chat_keyboard(self, user_id: str, mode: str, repos: list[dict], cockpit_url: str) -> InlineKeyboardMarkup:
        mode = normalize_cockpit_mode(mode)
        rows: list[list[InlineKeyboardButton]] = []
        for index, repo in enumerate(repos[:8]):
            if not isinstance(repo, dict) or not repo.get("nameWithOwner"):
                continue
            rows.append([
                InlineKeyboardButton(
                    self._repo_button_label(repo),
                    callback_data=f"rcnr:{mode}:{index}",
                )
            ])
        if not rows:
            rows.append([InlineKeyboardButton("Actualiser les repos", callback_data=f"rcn:existing:{mode}")])
        button_kwargs = (
            {"web_app": WebAppInfo(url=cockpit_url)}
            if WebAppInfo is not None
            else {"url": cockpit_url}
        )
        rows.append([InlineKeyboardButton("Mini App liste complète", **button_kwargs)])
        rows.append([
            InlineKeyboardButton("Actualiser", callback_data=f"rcn:existing:{mode}"),
            InlineKeyboardButton("Annuler", callback_data="rcn:cancel"),
        ])
        return InlineKeyboardMarkup(rows)

    def _repo_selected_text(self, repo: str, mode: str, thread_id: str | None = None) -> str:
        mode = normalize_cockpit_mode(mode)
        lines = [
            "<b>✅ Repo sélectionné</b>",
            "",
            f"Repo : <code>{_html.escape(repo)}</code>",
            f"Mode : <b>{_html.escape(self._mode_title(mode))}</b>",
        ]
        if thread_id:
            lines.append(f"Conversation : <code>{_html.escape(str(thread_id))}</code>")
        lines.extend([
            "",
            "Prochaine étape : envoie ta tâche directement dans ce chat.",
        ])
        return "\n".join(lines)

    def _repo_selected_keyboard(self, mode: str) -> InlineKeyboardMarkup:
        mode = normalize_cockpit_mode(mode)
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Changer repo", callback_data=f"rcn:existing:{mode}"),
                InlineKeyboardButton("Ask review", callback_data="rcn:mode:ask_review"),
            ],
            [
                InlineKeyboardButton("Pilote", callback_data="rcn:mode:pilote"),
                InlineKeyboardButton("Autopilot", callback_data="rcn:mode:autopilot"),
            ],
            [
                InlineKeyboardButton("Annuler", callback_data="rcn:cancel"),
            ],
        ])

    def _new_chat_text(self, mode: str, selected_repo: str | None = None) -> str:
        mode = normalize_cockpit_mode(mode)
        repo_line = f"Repo actuel : <code>{_html.escape(selected_repo)}</code>" if selected_repo else "Repo actuel : <i>aucun repo sélectionné</i>"
        return (
            "<b>🧭 Nouveau chat Hermes</b>\n\n"
            f"Mode : <b>{_html.escape(self._mode_title(mode))}</b>\n"
            f"Effet : {_html.escape(self._mode_note(mode))}.\n"
            f"{repo_line}\n\n"
            "Choisis si ce clavardage part d'un repo GitHub existant ou d'un nouveau projet."
        )

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
        text = (
            "<b>✅ Nouveau projet créé</b>\n\n"
            f"Projet : <code>{_html.escape(data.get('title',''))}</code>\n"
            f"Repo : <code>{_html.escape(data.get('repo') or '')}</code>\n"
            f"Mode : <b>{_html.escape(self._mode_title(data.get('mode','ask_review')))}</b>\n"
            f"Thread : <code>{_html.escape(data.get('thread_id',''))}</code>\n\n"
            "Tu peux maintenant écrire la tâche à réaliser dans ce chat."
        )
        await self._send_cockpit_text(msg, text, role="sticky")
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
        if not tasks:
            return await self._send_cockpit_text(msg, "<b>📋 Tâches</b>\n\nAucune tâche.")
        lines = ["<b>📋 Tâches Repo Cockpit</b>", ""]
        for t in tasks:
            lines.append(f"<code>{_html.escape(t.get('id',''))}</code> · <b>{_html.escape(t.get('status',''))}</b> · {_html.escape(t.get('repo',''))}")
        lines.append("\nDétail : <code>/task ID</code>")
        await self._send_cockpit_text(msg, "\n".join(lines))

    def _pending_pr_label(self, item: dict) -> str:
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

    def _format_pending_prs(self, data: dict) -> str:
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

    def _pending_prs_keyboard(self, data: dict) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        for item in (data.get("prs") or [])[:5]:
            task_id = str(item.get("task_id") or "")
            pr_url = str(item.get("pr_url") or "")
            preview_url = str(item.get("preview_url") or "")
            label = self._pending_pr_label(item)
            if pr_url.startswith(("https://", "http://")):
                rows.append([InlineKeyboardButton(f"PR {label}", url=pr_url)])
            if preview_url.startswith(("https://", "http://")):
                rows.append([InlineKeyboardButton(f"Preview {label}", url=preview_url)])
            if task_id.startswith("op_"):
                rows.append([
                    InlineKeyboardButton(f"Status {label}", callback_data=f"rca:status:{task_id}"),
                    InlineKeyboardButton(f"Runs {label}", callback_data=f"rca:runs:{task_id}"),
                ])
                rows.append([InlineKeyboardButton(f"Résumé {label}", callback_data=f"rca:prsum:{task_id}")])
        rows.append([InlineKeyboardButton("Rafraîchir PRs", callback_data="rca:prs")])
        rows.append([InlineKeyboardButton("Threads", callback_data="rct:list:all")])
        return InlineKeyboardMarkup(rows)

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
        branch = (
            pr.get("branch")
            or pr.get("head")
            or result.get("branch")
            or ((result.get("branch_result") or {}) if isinstance(result.get("branch_result"), dict) else {}).get("effective_branch")
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
                lines.append(f"{self._status_badge(status)} <code>{_html.escape(phase[:70])}</code> · {_html.escape(status)}")
        lines.extend([
            "",
            "Pour continuer dans ce chat : écris une nouvelle demande. Hermes utilisera le projet/thread actif.",
            "Pour changer de mode ou de projet : <code>/new</code> ou <code>/conv</code>.",
        ])
        return "\n".join(lines)

    async def _send_task_command(self, msg: Message, args: str = "") -> None:
        raw = (args or "").strip()
        task_id = raw.split()[0] if raw else ""
        if not task_id:
            return await self._send_cockpit_text(
                msg,
                "Usage : <code>/task décris la tâche</code>\nDétail : <code>/task op_xxx</code>",
                role="preview",
            )
        if not task_id.startswith("op_"):
            user_id = str(getattr(getattr(msg, "from_user", None), "id", "") or getattr(msg, "chat_id", ""))
            pending = await asyncio.to_thread(
                self._cockpit_api_sync,
                "GET",
                f"/api/internal/tasks/pilot-pending/{user_id}",
                None,
                20,
            )
            if pending.get("ok") and pending.get("pending") and (pending.get("task") or {}).get("id"):
                pilot_task_id = str((pending.get("task") or {}).get("id"))
                resumed = await asyncio.to_thread(
                    self._cockpit_api_sync,
                    "POST",
                    f"/api/internal/tasks/{pilot_task_id}/pilot-answer",
                    {
                        "telegram_user_id": user_id,
                        "chat_id": str(getattr(msg, "chat_id", "")),
                        "answer": raw,
                    },
                    30,
                )
                if resumed.get("ok"):
                    await self._send_cockpit_text(
                        msg,
                        "<b>🧭 Réponse Pilote reçue</b>\n\n"
                        f"Tâche : <code>{_html.escape(pilot_task_id)}</code>\n"
                        "Statut : <code>queued_plan</code>\n\n"
                        "Je relance le worker avec ce contexte.",
                        role="sticky",
                    )
                    asyncio.create_task(self._run_autopilot_worker_after_task_create(msg, pilot_task_id))
                    return
                return await self._send_cockpit_text(
                    msg,
                    "<b>Réponse Pilote non appliquée</b>\n\n<code>"
                    + _html.escape(str(resumed.get("description") or resumed))[:1000]
                    + "</code>",
                    role="preview",
                )
            return await self._create_task_from_thread_command(msg, raw)
        data = await asyncio.to_thread(self._cockpit_api_sync, "GET", f"/api/tasks/{task_id}", None, 20)
        if data.get("ok") is False or data.get("detail"):
            return await self._send_cockpit_text(msg, f"❌ Tâche introuvable : <code>{_html.escape(task_id)}</code>")
        result = data.get("result") or {}
        pr = ((result.get("pr") or {}).get("pr_url")) if isinstance(result, dict) else None
        lines = [f"<b>📌 Tâche { _html.escape(data.get('id','')) }</b>", "", f"Repo : <code>{_html.escape(data.get('repo',''))}</code>", f"Statut : <b>{_html.escape(data.get('status',''))}</b>", f"Phase : <code>{_html.escape(str(data.get('current_phase') or ''))}</code>", f"Approval : <code>{_html.escape(str(data.get('approval_status') or ''))}</code>", f"Mode : {_html.escape(data.get('mode',''))}", f"Complexité : {_html.escape(data.get('complexity',''))}"]
        if data.get("plan_md"):
            lines.append("\n<b>Plan</b>\n<pre><code>" + _html.escape(str(data.get("plan_md"))[:1200]) + "</code></pre>")
        if pr: lines.append(f"PR : { _html.escape(pr) }")
        if data.get("preview_url"): lines.append(f"Preview : {_html.escape(data.get('preview_url'))}")
        if data.get("deployment_url"): lines.append(f"Deploy : {_html.escape(data.get('deployment_url'))}")
        lines.append(f"Resume : <code>{_html.escape(data.get('resume_md_path',''))}</code>")
        await self._send_cockpit_text(msg, "\n".join(lines))

    def _audit_task_text(self, active: dict, args: str = "") -> str:
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

    def _format_audit_started(self, *, job_id: str, task: dict, active: dict) -> str:
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
            f"Mode : <b>{_html.escape(self._mode_title(mode))}</b>",
            f"Phase : <code>{_html.escape(phase)}</code>",
            "",
            "Je lance le worker en arrière-plan en dry-run. Le chat reste disponible.",
            f"Suivi : <code>/status {_html.escape(task_id)}</code> · <code>/runs {_html.escape(task_id)}</code>",
        ]
        return "\n".join(lines)

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
            lines = [
                "<b>🔎 Audit Repo Cockpit terminé</b>",
                "",
                f"Job : <code>{_html.escape(job_id)}</code>",
                f"Tâche : <code>{_html.escape(task_id)}</code>",
                f"Worker : <code>{_html.escape(status)}</code>",
                "",
                f"Suivi : <code>/status {_html.escape(task_id)}</code> · <code>/runs {_html.escape(task_id)}</code>",
            ]
            await self._send_cockpit_text(msg, "\n".join(lines), role="progress")
        except Exception as exc:
            await self._send_cockpit_text(
                msg,
                "<b>🔎 Audit Repo Cockpit bloqué</b>\n\n"
                f"Job : <code>{_html.escape(job_id)}</code>\n"
                f"Tâche : <code>{_html.escape(task_id)}</code>\n\n"
                "<code>" + _html.escape(str(exc))[:1000] + "</code>",
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
        value = str(status or "unknown")
        if value in {"passed", "ready", "done", "completed", "fixed", "ok", "success", "approved"} or value.startswith("running"):
            return "✅"
        if value.startswith("blocked") or value in {"failed", "error", "worsened", "rolled_back", "denied"}:
            return "🚨"
        if value in {"queued", "pending"} or value.startswith("queued"):
            return "⏳"
        return "•"

    def _latest_items(self, data: dict, key: str, limit: int = 3) -> list[dict]:
        items = data.get(key) or []
        if not isinstance(items, list):
            return []
        return items[:limit]

    def _status_counts(self, items: list[dict], key: str = "status") -> str:
        counts: dict[str, int] = {}
        for item in items:
            status = str(item.get(key) or "unknown")
            counts[status] = counts.get(status, 0) + 1
        if not counts:
            return "0"
        ordered = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
        return ", ".join(f"{count} {status}" for status, count in ordered[:4])

    def _evaluation_summary_line(self, data: dict) -> str:
        summary = data.get("evaluation_summary") or {}
        suites = summary.get("suites") if isinstance(summary, dict) else {}
        if not isinstance(suites, dict) or not suites:
            return ""
        parts = []
        for suite, item in sorted(suites.items()):
            if not isinstance(item, dict):
                continue
            total = int(item.get("total") or 0)
            passed = int(item.get("passed") or 0)
            if total:
                parts.append(f"{suite} {passed}/{total}")
        return ", ".join(parts[:3])

    def _cost_summary_line(self, data: dict) -> str:
        summary = data.get("cost_summary") or {}
        if not isinstance(summary, dict):
            return ""
        task = summary.get("task") if isinstance(summary.get("task"), dict) else {}
        daily = summary.get("daily") if isinstance(summary.get("daily"), dict) else {}
        task_cost = float(task.get("total_cost_usd") or 0.0)
        daily_cost = float(daily.get("total_cost_usd") or 0.0)
        calls = int(task.get("calls") or 0)
        if not task_cost and not daily_cost and not calls:
            return ""
        def fmt(value: float) -> str:
            return f"${value:.4f}" if 0 < value < 0.01 else f"${value:.2f}"
        return f"Aujourd'hui {fmt(daily_cost)} · task {fmt(task_cost)} · appels {calls}"

    def _selfops_summary_line(self, data: dict) -> str:
        event = data.get("selfops") or {}
        if not isinstance(event, dict):
            return ""
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        sample = payload.get("sample") if isinstance(payload.get("sample"), dict) else {}
        evaluation = payload.get("evaluation") if isinstance(payload.get("evaluation"), dict) else {}
        metrics = sample.get("metrics") if isinstance(sample.get("metrics"), dict) else {}
        if not metrics:
            return ""
        overall = evaluation.get("overall") or event.get("decision") or "unknown"
        return (
            f"{overall} · disque {metrics.get('disk_root_pct', '?')}% · "
            f"RAM {metrics.get('ram_used_pct', '?')}% · load {metrics.get('load15_per_cpu', '?')}"
        )

    def _cost_guard_line(self, data: dict) -> str:
        guard = data.get("cost_guard") or {}
        if not isinstance(guard, dict):
            return ""
        status = str(guard.get("status") or "ok")
        if status == "ok":
            return ""
        reasons = ", ".join(str(item) for item in (guard.get("reasons") or [])) or status
        task = guard.get("task") if isinstance(guard.get("task"), dict) else {}
        daily = guard.get("daily") if isinstance(guard.get("daily"), dict) else {}
        task_cost = float(task.get("total_cost_usd") or 0.0)
        daily_cost = float(daily.get("total_cost_usd") or 0.0)
        return f"{status} · {reasons} · jour ${daily_cost:.2f} · task ${task_cost:.2f}"

    def _telemetry_summary_line(self, data: dict) -> str:
        summary = data.get("telemetry_summary") or {}
        if not isinstance(summary, dict) or not summary.get("events"):
            return ""
        return (
            f"{summary.get('events', 0)} events · repairs {summary.get('repairs', 0)} · "
            f"échecs {summary.get('failures', 0)} · escalades {summary.get('escalations', 0)}"
        )

    def _short_observation_label(self, item: dict) -> str:
        signature = str(item.get("signature") or "")
        source = str(item.get("source") or "")
        status = str(item.get("status") or "")
        label = signature or source or item.get("id") or "observation"
        return f"{status} · {label}" if status else str(label)

    def _format_autonomy_status(self, data: dict) -> str:
        task = data.get("task") or {}
        status = str(task.get("status") or "")
        error_events = data.get("error_events") or []
        task_runs = data.get("task_runs") if isinstance(data.get("task_runs"), list) else []
        repairs = data.get("repair_attempts") if isinstance(data.get("repair_attempts"), list) else []
        observations = data.get("runtime_observations") if isinstance(data.get("runtime_observations"), list) else []
        approvals = data.get("approvals") if isinstance(data.get("approvals"), list) else []
        latest_error = {}
        if self._status_is_problem(status):
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
            label = "Preview non validée" if self._preview_is_blocked(status) else "Preview"
            lines.append(f"{label} : {_html.escape(str(preview))}")
        lines.extend([
            "",
            "<b>Vue rapide</b>",
            f"Runs : <code>{_html.escape(self._status_counts(task_runs))}</code>",
            f"Repairs : <code>{_html.escape(self._status_counts(repairs))}</code>",
            f"Observations : <code>{len(observations)}</code>",
            f"Approvals : <code>{_html.escape(self._status_counts(approvals))}</code>",
        ])
        eval_line = self._evaluation_summary_line(data)
        if eval_line:
            lines.append(f"Evals : <code>{_html.escape(eval_line)}</code>")
        cost_line = self._cost_summary_line(data)
        if cost_line:
            lines.append(f"Coût : <code>{_html.escape(cost_line)}</code>")
        cost_guard_line = self._cost_guard_line(data)
        if cost_guard_line:
            lines.append(f"Budget : <code>{_html.escape(cost_guard_line)}</code>")
        telemetry_summary_line = self._telemetry_summary_line(data)
        if telemetry_summary_line:
            lines.append(f"Historique : <code>{_html.escape(telemetry_summary_line)}</code>")
        selfops_line = self._selfops_summary_line(data)
        if selfops_line:
            lines.append(f"VPS : <code>{_html.escape(selfops_line)}</code>")
        if latest_error:
            lines.extend([
                "",
                "<b>Dernière erreur classée</b>",
                f"Catégorie : <code>{_html.escape(str(latest_error.get('category') or ''))}</code>",
                f"Runbook : <code>{_html.escape(str(latest_error.get('runbook') or ''))}</code>",
                f"Humain requis : <code>{_html.escape(str(latest_error.get('human_action_required') or False))}</code>",
            ])
        provider_checks = self._latest_items(data, "provider_checks")
        if provider_checks:
            lines.extend(["", "<b>Provider checks</b>"])
            for item in provider_checks:
                lines.append(
                    f"{self._status_badge(item.get('status'))} {_html.escape(str(item.get('provider') or ''))}/"
                    f"{_html.escape(str(item.get('check_name') or ''))} : <code>{_html.escape(str(item.get('status') or ''))}</code>"
                )
        smokes = self._latest_items(data, "smoke_tests")
        if smokes:
            lines.extend(["", "<b>Smoke tests</b>"])
            for item in smokes:
                lines.append(f"{self._status_badge(item.get('status'))} <code>{_html.escape(str(item.get('status') or ''))}</code> · {_html.escape(str(item.get('url') or ''))[:120]}")
        if task_runs:
            lines.extend(["", "<b>Runs récents</b>"])
            for item in self._latest_items(data, "task_runs"):
                phase = str(item.get("phase") or item.get("id") or "")
                run_status = str(item.get("status") or "")
                lines.append(f"{self._status_badge(run_status)} <code>{_html.escape(phase[:70])}</code> · {_html.escape(run_status)}")
        if repairs:
            lines.extend(["", "<b>Réparations</b>"])
            for item in self._latest_items(data, "repair_attempts"):
                runbook = str(item.get("runbook") or item.get("id") or "")
                repair_status = str(item.get("status") or "")
                attempt = item.get("attempt")
                suffix = f" · tentative {attempt}" if attempt else ""
                lines.append(f"{self._status_badge(repair_status)} <code>{_html.escape(runbook[:70])}</code> · {_html.escape(repair_status)}{_html.escape(suffix)}")
        if observations:
            lines.extend(["", "<b>Observations runtime</b>"])
            for item in self._latest_items(data, "runtime_observations"):
                label = self._short_observation_label(item)
                lines.append(f"{self._status_badge(item.get('status'))} <code>{_html.escape(label[:90])}</code>")
        telemetry = self._latest_items(data, "telemetry_events", 4)
        if telemetry:
            lines.extend(["", "<b>Telemetry</b>"])
            for item in telemetry:
                kind = str(item.get("kind") or "event")
                source = str(item.get("source") or "")
                decision = str(item.get("decision") or item.get("action") or "")
                suffix = f" · {decision}" if decision else ""
                lines.append(f"• <code>{_html.escape(kind[:40])}</code> {_html.escape(source[:40])}{_html.escape(suffix[:60])}")
        if approvals:
            pending = [item for item in approvals if str(item.get("status") or "") == "pending"]
            shown = pending or approvals
            lines.extend(["", "<b>Approvals</b>"])
            for item in shown[:3]:
                label = str(item.get("approval_type") or item.get("id") or "")
                approval_status = str(item.get("status") or "")
                lines.append(f"{self._status_badge(approval_status)} <code>{_html.escape(label[:70])}</code> · {_html.escape(approval_status)}")
        runbooks = self._latest_items(data, "runbooks_applied")
        if runbooks:
            lines.extend(["", "<b>Runbooks</b>"])
            for item in runbooks:
                lines.append(f"{self._status_badge(item.get('status'))} <code>{_html.escape(str(item.get('runbook') or ''))}</code> · {_html.escape(str(item.get('status') or ''))}")
        lines.append("\nDétail technique : <code>/runs " + _html.escape(str(task.get("id") or data.get("task_id") or "")) + "</code>")
        return "\n".join(lines)

    def _format_runs_status(self, data: dict) -> str:
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
            items = self._latest_items(data, section, 6)
            if not items:
                continue
            lines.extend(["", f"<b>{_html.escape(title)}</b>"])
            for item in items:
                label = str(item.get(name_key) or item.get("check_name") or item.get("id") or "")
                status = str(item.get("status") or "")
                lines.append(f"{self._status_badge(status)} <code>{_html.escape(label)[:80]}</code> · <b>{_html.escape(status)}</b>")
        return "\n".join(lines)

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
        return status in {
            "blocked_deploy",
            "blocked_smoke",
            "blocked_release_gate",
            "blocked_pr_required",
            "blocked_review_required",
            "blocked_tests",
        }

    def _status_is_problem(self, status: str) -> bool:
        value = str(status or "")
        return value.startswith("blocked") or value in {"failed", "error"}

    def _autonomy_keyboard(self, data: dict, view: str = "status") -> InlineKeyboardMarkup:
        task = data.get("task") or {}
        task_id = str(task.get("id") or data.get("task_id") or "")
        status = str(task.get("status") or "")
        preview = str(task.get("preview_url") or task.get("deployment_url") or "")
        rows: list[list[InlineKeyboardButton]] = []
        if preview.startswith(("https://", "http://")) and not self._preview_is_blocked(status):
            rows.append([InlineKeyboardButton("Ouvrir preview", url=preview)])
        if task_id:
            if view == "runs":
                rows.append([
                    InlineKeyboardButton("Status", callback_data=f"rca:status:{task_id}"),
                    InlineKeyboardButton("Rafraîchir", callback_data=f"rca:runs:{task_id}"),
                ])
            else:
                rows.append([
                    InlineKeyboardButton("Runs", callback_data=f"rca:runs:{task_id}"),
                    InlineKeyboardButton("Rafraîchir", callback_data=f"rca:status:{task_id}"),
                ])
        rows.append([InlineKeyboardButton("Threads", callback_data="rct:list:all")])
        return InlineKeyboardMarkup(rows)

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
        parent_task_id: str | None = None,
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
        if parent_task_id:
            payload["parent_task_id"] = str(parent_task_id)
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
                    prefer_v2=True,
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
        if decision.action in {"learn_policy", "policy"}:
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

        if decision.action == "switch_repo" or decision.intent == "switch_repo":
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
        telegram_chat_type = str(getattr(chat, "type", "")).split(".")[-1].lower()
        chat_type = "dm"
        if telegram_chat_type in {"group", "supergroup"}:
            chat_type = "group"
        elif telegram_chat_type == "channel":
            chat_type = "channel"

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
