"""Telegram transport, sending, rich-message, and media mixin."""

from __future__ import annotations

import asyncio
import dataclasses
import html as _html
import inspect
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path as _Path
from typing import Any, Dict, List, Optional

from gateway.config import Platform
from gateway.platforms.base import (
    SendResult,
    SessionSource,
    cache_audio_from_bytes,
    cache_document_from_bytes,
    cache_image_from_bytes,
    cache_video_from_bytes,
    resolve_proxy_url,
    SUPPORTED_DOCUMENT_TYPES,
    SUPPORTED_IMAGE_DOCUMENT_TYPES,
    SUPPORTED_VIDEO_TYPES,
    utf16_len,
)
from gateway.platforms.telegram_formatting import _TABLE_SEPARATOR_RE, _strip_mdv2
from gateway.platforms.telegram_network import TelegramFallbackTransport, discover_fallback_ips, parse_fallback_ip_env
from gateway.repo_cockpit_text import normalize_cockpit_mode
from utils import atomic_replace

logger = logging.getLogger("gateway.platforms.telegram")
MAX_COMMANDS_PER_SCOPE = 30

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


class _TelegramAttrProxy:
    def __init__(self, module_name: str, attr_name: str):
        self.module_name = module_name
        self.attr_name = attr_name

    def _target(self) -> Any:
        import importlib

        module = importlib.import_module(self.module_name)
        return getattr(module, self.attr_name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._target()(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target(), name)

    def __bool__(self) -> bool:
        try:
            return bool(self._target())
        except Exception:
            return False


class _TelegramAvailableProxy:
    def __bool__(self) -> bool:
        from gateway.platforms import telegram as telegram_mod

        return bool(telegram_mod.TELEGRAM_AVAILABLE)


Application = _TelegramAttrProxy("gateway.platforms.telegram", "Application")
CallbackQueryHandler = _TelegramAttrProxy("gateway.platforms.telegram", "CallbackQueryHandler")
ChatType = _TelegramAttrProxy("gateway.platforms.telegram", "ChatType")
HTTPXRequest = _TelegramAttrProxy("gateway.platforms.telegram", "HTTPXRequest")
InlineKeyboardButton = _TelegramAttrProxy("gateway.platforms.telegram", "InlineKeyboardButton")
InlineKeyboardMarkup = _TelegramAttrProxy("gateway.platforms.telegram", "InlineKeyboardMarkup")
InputMediaPhoto = _TelegramAttrProxy("telegram", "InputMediaPhoto")
LinkPreviewOptions = _TelegramAttrProxy("gateway.platforms.telegram", "LinkPreviewOptions")
ParseMode = _TelegramAttrProxy("gateway.platforms.telegram", "ParseMode")
TelegramMessageHandler = _TelegramAttrProxy("gateway.platforms.telegram", "TelegramMessageHandler")
Update = _TelegramAttrProxy("gateway.platforms.telegram", "Update")
filters = _TelegramAttrProxy("gateway.platforms.telegram", "filters")
TELEGRAM_AVAILABLE = _TelegramAvailableProxy()
Message = Any

try:
    from telegram.error import BadRequest, NetworkError, TimedOut
except Exception:  # pragma: no cover - optional dependency fallback
    BadRequest = NetworkError = TimedOut = Exception  # type: ignore[misc,assignment]


class TelegramTransportMixin:
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
            web_app_data_filter = getattr(getattr(filters, "StatusUpdate", None), "WEB_APP_DATA", None)
            if web_app_data_filter is not None and hasattr(self, "_handle_web_app_data"):
                self._app.add_handler(TelegramMessageHandler(
                    web_app_data_filter,
                    self._handle_web_app_data
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
        if data.startswith(("mp:", "mpg:", "mm:", "mc:", "mr:", "mrr", "mrm", "ma", "mb", "mx", "mg:")):
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
