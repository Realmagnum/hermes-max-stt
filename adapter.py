"""
MAX messenger (max.ru) Platform Adapter for Hermes Agent.

A plugin-based gateway adapter that supports both long-polling and webhook
for receiving messages, and the Max Bot REST API for sending responses.

Architecture:
- Inbound:  Long polling (GET /updates) OR Webhook (POST /max/webhook) → MessageEvent
- Outbound: httpx → POST /messages (with chunking for >4000 chars)
- STT:      Voice messages auto-downloaded → local path → faster-whisper transcription
- Files:    Two-step upload (POST /uploads → PUT file → token → send)
- Streaming: edit_message via PUT /messages

Configuration in ~/.hermes/.env:
  MAX_BOT_TOKEN (required)
  MAX_WEBHOOK_HOST, MAX_WEBHOOK_PORT, MAX_WEBHOOK_PATH
  MAX_WEBHOOK_SECRET, MAX_ALLOWED_USERS, MAX_ALLOW_ALL_USERS
  MAX_STT_ENABLED (default: true)
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import socket as _socket
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from gateway.config import PlatformConfig, Platform
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
    SUPPORTED_DOCUMENT_TYPES,
    cache_audio_from_bytes,
    cache_image_from_bytes,
    cache_document_from_bytes,
)

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────

MAX_API_BASE = "https://platform-api.max.ru"
MAX_MESSAGE_LENGTH = 4000
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
POLL_TIMEOUT = 5  # seconds
POLL_ERROR_DELAY = 5.0
WEBHOOK_MAX_BODY_BYTES = 1_048_576  # 1 MB
UPLOAD_DELAY = 2.0

DEFAULT_WEBHOOK_HOST = "0.0.0.0"  # nosec B104 — webhook server must accept external callbacks from MAX API; protected by reverse proxy (TLS) + rate limiting + secret verification
DEFAULT_WEBHOOK_PORT = 8646
DEFAULT_WEBHOOK_PATH = "/max/webhook"

# STT
AUDIO_CACHE_DIR = Path(
    os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))
) / "audio_cache"

# Ensure cache dir exists with restricted permissions (voice messages are private)
AUDIO_CACHE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
DEFAULT_STT_ENABLED = True

# ── Helpers ──────────────────────────────────────────────────────────────
import json as _json


def _safe_url_for_log(url: str) -> str:
    """Strip credentials from URL for logging."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.password:
        return url.replace(parsed.password, "***")
    if parsed.username and parsed.username != parsed.hostname:
        return url.replace(parsed.username, "***")
    return url


def _find_audio_url_direct(obj: Any, depth: int = 0) -> Optional[str]:
    """Recursively search for an audio/voice download URL in a MAX update.

    Searches common MAX fields: message.attachments, .voice, .audio,
    body.attachments, and any dict containing type=voice/audio with a URL.
    """
    if depth > 8:
        return None
    if isinstance(obj, dict):
        # Check type+url pattern (standard MAX attachment)
        atype = str(obj.get("type", "")).lower()
        url = obj.get("url") or obj.get("download_url") or ""
        if atype in ("voice", "audio") and isinstance(url, str) and url.startswith("http"):
            return url
        # Check payload.url pattern
        payload = obj.get("payload")
        if isinstance(payload, dict):
            url = payload.get("url") or payload.get("download_url") or ""
            if isinstance(url, str) and url.startswith("http"):
                return url
        # Recurse into all values
        for key in ("attachments", "voice", "audio", "message", "body", "payload"):
            val = obj.get(key)
            found = _find_audio_url_direct(val, depth + 1)
            if found:
                return found
        for val in obj.values():
            if isinstance(val, (dict, list)):
                found = _find_audio_url_direct(val, depth + 1)
                if found:
                    return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_audio_url_direct(item, depth + 1)
            if found:
                return found
    return None


def _parse_list(value: str) -> List[str]:
    """Parse comma-separated string into trimmed list."""
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _is_group(chat_id: str) -> bool:
    """MAX group chats have negative IDs, DMs have positive."""
    try:
        return int(chat_id) < 0
    except (ValueError, TypeError):
        return False


def _verify_raw_secret(body: bytes, secret: str, secret_header: Optional[str]) -> bool:
    """Constant-time comparison of webhook secret.

    Max sends the raw secret in X-Max-Bot-Api-Secret header (not HMAC).
    Uses secrets.compare for timing-safe string comparison.
    """
    import secrets
    del body  # kept for API compatibility
    if not secret:
        return True
    if not secret_header:
        return False
    return secrets.compare_digest(str(secret), str(secret_header))


def _coerce_bool(value: Any, default: bool = False) -> bool:
    """Coerce env/config strings to bool."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


# ── MaxAdapter ───────────────────────────────────────────────────────────

class MaxAdapter(BasePlatformAdapter):
    """MAX messenger platform adapter with STT voice transcription."""

    def __init__(self, config: PlatformConfig):
        try:
            platform = Platform("max")
        except ValueError:
            # Platform 'max' is not in the Enum (typical in unit tests where the plugin isn't registered)
            # We can register a pseudo-member dynamically to make it work.
            try:
                pseudo = object.__new__(Platform)
                pseudo._value_ = "max"
                pseudo._name_ = "MAX"
                Platform._value2member_map_["max"] = pseudo
                Platform._member_map_["MAX"] = pseudo
                platform = pseudo
            except Exception:
                platform = list(Platform)[0]
        super().__init__(config=config, platform=platform)
        extra = getattr(config, "extra", {}) or {}

        # Token
        self._token: str = (
            os.getenv("MAX_BOT_TOKEN", "")
            or getattr(config, "token", "")
            or extra.get("token", "")
        )

        # STT
        self._stt_enabled: bool = _coerce_bool(
            os.getenv("MAX_STT_ENABLED")
            or extra.get("stt_enabled", DEFAULT_STT_ENABLED),
            DEFAULT_STT_ENABLED,
        )

        # Table-as-image (requires Pillow)
        self._table_as_image: bool = _coerce_bool(
            os.getenv("MAX_TABLE_AS_IMAGE")
            or extra.get("table_as_image", False),
            False,
        )
        self._table_image_dir: Path = AUDIO_CACHE_DIR.parent / "table_images"
        if self._table_as_image:
            self._table_image_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

        # Cross-platform session commands (/sessions, /resume across all platforms)
        self._cross_session: bool = _coerce_bool(
            os.getenv("MAX_CROSS_SESSION")
            or extra.get("cross_session", True),  # enabled by default
            True,
        )

        # Webhook settings
        self._webhook_host: str = (
            os.getenv("MAX_WEBHOOK_HOST")
            or str(extra.get("host", DEFAULT_WEBHOOK_HOST))
        )
        self._webhook_port: int = int(
            os.getenv("MAX_WEBHOOK_PORT")
            or extra.get("port", DEFAULT_WEBHOOK_PORT)
        )
        self._webhook_path: str = (
            os.getenv("MAX_WEBHOOK_PATH")
            or str(extra.get("path", DEFAULT_WEBHOOK_PATH))
        )
        self._webhook_secret: str = (
            os.getenv("MAX_WEBHOOK_SECRET")
            or str(extra.get("webhook_secret", ""))
        )
        self._webhook_url: str = (
            os.getenv("MAX_WEBHOOK_URL")
            or str(extra.get("webhook_url", ""))
        )
        # Use webhook if URL is explicitly configured
        self._use_webhook: bool = bool(self._webhook_url)

        # Access control
        self.allowed_users: list = extra.get("allowed_users", [])
        self._allowed_users_set: set = set()
        for u in self.allowed_users:
            if isinstance(u, (int, str)):
                self._allowed_users_set.add(str(u))
        # Also parse from env
        env_allowed = _parse_list(os.getenv("MAX_ALLOWED_USERS", ""))
        self._allowed_users_set.update(env_allowed)
        self._allow_all_users: bool = _coerce_bool(
            os.getenv("MAX_ALLOW_ALL_USERS")
            or extra.get("allow_all_users", False),
            False,
        )

        # Group access control
        self._group_policy: str = extra.get("group_policy", "allowlist")
        self._group_allow_from: List[str] = _parse_list(
            os.getenv("MAX_GROUP_ALLOWED_USERS", "")
            or str(extra.get("group_allow_from", ""))
        )
        self._group_allow_chats: List[str] = _parse_list(
            os.getenv("MAX_GROUP_ALLOWED_CHATS", "")
            or str(extra.get("group_allow_chats", ""))
        )

        # Runtime state
        self._http_client: Optional[httpx.AsyncClient] = None
        self._webhook_runner: Any = None  # aiohttp.web.AppRunner
        self._webhook_site: Any = None
        self._webhook_app: Any = None
        self._message_queue: asyncio.Queue[MessageEvent] = asyncio.Queue()
        self._poll_task: Optional[asyncio.Task] = None
        self._background_tasks: set[asyncio.Task] = set()
        self._stop: asyncio.Event = asyncio.Event()
        self._running: bool = False

        # Dedup: mid → timestamp (max 5000 entries to prevent memory exhaustion)
        self._seen_msgs: Dict[str, float] = {}
        self._SEEN_MSGS_MAX = 5000
        # DM routing: chat_id → user_id
        self._dm_user_ids: Dict[str, str] = {}

        # Interactive button state tracking
        self._exec_approval_state: Dict[str, str] = {}   # approval_id → session_key
        self._slash_confirm_state: Dict[str, str] = {}   # confirm_id → session_key
        self._clarify_state: Dict[str, str] = {}          # clarify_id → session_key
        self._model_picker_state: Dict[str, dict] = {}    # chat_id → picker state

    # ═════════════════════════════════════════════════════════════════════
    # Connection lifecycle
    # ═════════════════════════════════════════════════════════════════════

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        """Connect to Max: verify token, start polling or webhook."""
        if not self._token:
            self._set_fatal_error("no_token", "MAX_BOT_TOKEN not configured", retryable=False)
            return False

        # SECURITY: Do NOT follow redirects blindly — Authorization header
        # (token) would be forwarded to any redirect target (token leak).
        # Redirects with Authorization are disabled; if the Max API ever
        # needs redirects, add a limited-redirects transport for known domains.
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            headers={"Authorization": self._token},
            follow_redirects=False,
        )

        # Verify token with /me
        try:
            resp = await self._http_client.get(f"{MAX_API_BASE}/me", timeout=httpx.Timeout(10.0))
            if resp.status_code == 401:
                await self._http_client.aclose()
                self._http_client = None
                self._set_fatal_error("invalid_token", "MAX bot token is invalid", retryable=False)
                return False
            if resp.status_code == 200:
                d = resp.json()
                logger.info("MAX: connected as @%s (id=%s)", d.get("username", "?"), d.get("user_id"))
            else:
                logger.warning("MAX: /me returned %s", resp.status_code)
        except Exception as e:
            await self._http_client.aclose()
            self._http_client = None
            self._set_fatal_error("conn_fail", str(e), retryable=True)
            return False

        if self._use_webhook:
            return await self._start_webhook()
        return await self._start_polling()

    async def disconnect(self) -> None:
        """Shut down the adapter."""
        self._running = False
        self._stop.set()

        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

        # Cancel background tasks
        for task in list(self._background_tasks):
            task.cancel()
        self._background_tasks.clear()

        if self._webhook_runner:
            try:
                await self._webhook_runner.cleanup()
            except Exception:
                pass
            self._webhook_runner = None
            self._webhook_app = None

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

        self._mark_disconnected()
        logger.info("MAX: disconnected")

    # ═════════════════════════════════════════════════════════════════════
    # Long polling
    # ═════════════════════════════════════════════════════════════════════

    async def _start_polling(self) -> bool:
        self._stop.clear()

        # ═══════════════════════════════════════════════════════════════
        # Auto-clean stale webhook subscriptions
        #
        # MAX API does NOT support simultaneous webhook + long polling.
        # If a webhook subscription exists (from a previous run or manual
        # registration), /updates returns empty — all messages go to the
        # webhook URL instead.
        #
        # We proactively delete any active webhook subscription when
        # starting in long-polling mode so the user doesn't get stuck
        # with a "dead" subscription pointing at an old/stale URL.
        # ═══════════════════════════════════════════════════════════════
        try:
            sub_resp = await self._http_client.get(
                f"{MAX_API_BASE}/subscriptions",
                timeout=httpx.Timeout(5.0),
            )
            if sub_resp.status_code == 200:
                data = sub_resp.json()
                subs = data.get("subscriptions", [])
                if subs:
                    for sub in subs:
                        url = sub.get("url", "")
                        if url:
                            logger.info(
                                "MAX: cleaning stale webhook subscription: %s",
                                url,
                            )
                            del_resp = await self._http_client.request(
                                "DELETE",
                                f"{MAX_API_BASE}/subscriptions?url={url}",
                                timeout=httpx.Timeout(5.0),
                            )
                            if del_resp.status_code == 200:
                                logger.info(
                                    "MAX: stale webhook subscription deleted: %s",
                                    url,
                                )
                            else:
                                logger.warning(
                                    "MAX: failed to delete stale subscription %s: HTTP %s",
                                    url, del_resp.status_code,
                                )
        except Exception as e:
            logger.debug(
                "MAX: webhook cleanup skipped (non-fatal): %s", e,
            )

        self._mark_connected()
        self._background_tasks.add(asyncio.create_task(self._poll_loop()))
        self._poll_task = asyncio.create_task(self._queue_poll_loop())
        logger.info("MAX: long polling started")
        return True

    async def _poll_loop(self) -> None:
        """Long poll /updates with marker-based pagination."""
        last_marker = 0
        errs = 0
        while not self._stop.is_set():
            try:
                url = f"{MAX_API_BASE}/updates?timeout={POLL_TIMEOUT}&limit=100"
                if last_marker:
                    url += f"&marker={last_marker}"
                resp = await self._http_client.get(url, timeout=httpx.Timeout(POLL_TIMEOUT + 10))
                if resp.status_code == 200:
                    data = resp.json()
                    for u in data.get("updates", []):
                        event = await self._build_event(u)
                        if event is not None:
                            await self._message_queue.put(event)
                    marker = data.get("marker", 0)
                    if marker:
                        last_marker = marker
                    errs = 0
                else:
                    errs += 1
            except asyncio.CancelledError:
                break
            except Exception:
                errs += 1
                await asyncio.sleep(min(POLL_ERROR_DELAY * (2 ** min(errs - 1, 4)), 60))

    # ═════════════════════════════════════════════════════════════════════
    # Webhook server
    # ═════════════════════════════════════════════════════════════════════

    async def _start_webhook(self) -> bool:
        """Start aiohttp webhook server."""
        try:
            from aiohttp import web
        except ImportError:
            self._set_fatal_error("no_aiohttp", "aiohttp not installed", retryable=False)
            return False

        # Port-in-use check
        try:
            with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                sock.connect(("127.0.0.1", self._webhook_port))
            self._set_fatal_error("port_in_use", f"Port {self._webhook_port} already in use", retryable=False)
            return False
        except (ConnectionRefusedError, OSError):
            pass  # Port is free

        # Port-in-use check
        try:
            with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                sock.connect(("127.0.0.1", self._webhook_port))
            self._set_fatal_error("port_in_use", f"Port {self._webhook_port} already in use", retryable=False)
            return False
        except (ConnectionRefusedError, OSError):
            pass  # Port is free

        secret = self._webhook_secret
        path = self._webhook_path

        app = web.Application()

        async def health_handler(req: web.Request) -> web.Response:
            return web.json_response({"status": "ok"})

        # Rate limiter for webhook (per-IP, in-memory, cleaned every 5 min)
        _webhook_hits: Dict[str, list] = {}
        _WEBHOOK_LIMIT = 30   # max requests
        _WEBHOOK_WINDOW = 10  # per 10 seconds

        async def webhook_handler(req: web.Request) -> web.Response:
            # Simple per-IP rate limiting
            now = time.monotonic()
            peer = req.remote or "unknown"
            hits = _webhook_hits.get(peer, [])
            hits[:] = [t for t in hits if now - t < _WEBHOOK_WINDOW]
            if len(hits) >= _WEBHOOK_LIMIT:
                logger.warning("MAX: webhook rate limit exceeded for %s", peer)
                return web.Response(status=429)
            hits.append(now)
            _webhook_hits[peer] = hits
            # Periodic cleanup
            if len(_webhook_hits) > 1000:
                _webhook_hits = {k: v for k, v in _webhook_hits.items()
                                 if any(now - t < _WEBHOOK_WINDOW for t in v)}

            # Verify secret
            if secret:
                body = await req.read()
                sig = req.headers.get("X-Max-Bot-Api-Secret", "")
                if not _verify_raw_secret(body, secret, sig):
                    logger.warning("MAX: webhook secret verification failed")
                    return web.Response(status=403)
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    return web.Response(status=400, text="invalid json")
            else:
                try:
                    payload = await req.json()
                except Exception:
                    return web.Response(status=400, text="invalid json")

            event = await self._build_event(payload)
            if event is not None:
                await self._message_queue.put(event)
            return web.Response(text="ok")

        app.router.add_get("/health", health_handler)
        app.router.add_post(path, webhook_handler)

        self._webhook_app = app
        self._webhook_runner = web.AppRunner(app)
        await self._webhook_runner.setup()
        site = web.TCPSite(self._webhook_runner, self._webhook_host, self._webhook_port)
        await site.start()
        logger.info("MAX: webhook on %s:%s%s", self._webhook_host, self._webhook_port, path)

        # Auto-register webhook if URL is set
        if self._webhook_url:
            try:
                body: Dict[str, Any] = {
                    "url": self._webhook_url,
                    "update_types": ["message_created", "message_callback", "bot_started", "bot_added"],
                }
                if secret:
                    body["secret"] = secret
                resp = await self._http_client.post(
                    f"{MAX_API_BASE}/subscriptions",
                    json=body,
                    timeout=httpx.Timeout(10.0),
                )
                if resp.status_code == 200:
                    d = resp.json()
                    logger.info("MAX: webhook registered%s", "" if d.get("success") else f" — {d.get('message')}")
            except Exception as e:
                logger.error("MAX: webhook register failed: %s", e)

        # Start poll loop for draining the queue
        self._poll_task = asyncio.create_task(self._queue_poll_loop())
        self._mark_connected()
        return True

    async def _queue_poll_loop(self) -> None:
        """Drain the message queue and dispatch to the gateway runner."""
        while self._running:
            try:
                event = await asyncio.wait_for(self._message_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            if not self._running:
                break
            try:
                task = asyncio.create_task(self.handle_message(event))
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
            except Exception:
                logger.exception("MAX: failed to enqueue event")

    # ═════════════════════════════════════════════════════════════════════
    # Update processing
    # ═════════════════════════════════════════════════════════════════════

    async def _build_event(self, payload: Dict[str, Any]) -> Optional[MessageEvent]:
        """Parse a Max Update object into a MessageEvent."""
        update_type = payload.get("update_type", "")

        if update_type == "bot_started":
            user = payload.get("user", {})
            cid = str(payload.get("chat_id", ""))
            uid = str(user.get("user_id", ""))
            payload_text = payload.get("payload", "")
            self._dm_user_ids[cid] = uid
            source = self.build_source(
                chat_id=f"user:{uid}",
                chat_name=user.get("name", uid),
                chat_type="dm",
                user_id=uid,
                user_name=user.get("name", uid),
            )
            return MessageEvent(
                text=f"/start {payload_text}".strip(),
                message_type=MessageType.TEXT,
                source=source,
                raw_message=payload,
                message_id=f"start_{uid}",
            )

        if update_type == "bot_added":
            cid = str(payload.get("chat_id", ""))
            uid = str((payload.get("user") or {}).get("user_id", ""))
            source = self.build_source(
                chat_id=f"chat:{cid}",
                chat_name=cid,
                chat_type="group",
                user_id=uid,
                user_name=uid,
            )
            return MessageEvent(
                text="/start",
                message_type=MessageType.TEXT,
                source=source,
                internal=True,
            )

        if update_type in ("message_created", "message_edited", "message_updated"):
            return await self._on_message_created(payload)

        if update_type == "message_callback":
            return await self._on_callback(payload)

        # Unknown update types (voice messages may arrive with a special type)
        if update_type and update_type not in (
            "message_created", "message_edited", "message_updated",
            "message_callback", "bot_started", "bot_added",
        ):
            logger.info("MAX: unhandled update_type=%r keys=%s", update_type, list(payload.keys()))

        return None

    async def _on_message_created(self, update: dict) -> Optional[MessageEvent]:
        """Process message_created update. Returns MessageEvent or None."""
        message = update.get("message", {}) or {}
        body = message.get("body") or {}
        sender = message.get("sender") or update.get("user") or {}
        recipient = message.get("recipient") or {}

        # Skip bot messages
        if sender.get("is_bot") is True:
            return None

        user_id = str(
            sender.get("user_id")
            or update.get("user_id")
            or message.get("user_id")
            or ""
        )
        user_name = (
            sender.get("name")
            or sender.get("first_name")
            or sender.get("username")
            or user_id
        )

        text = (body.get("text") or message.get("text") or "").strip()

        chat = update.get("chat", {}) or {}
        chat_id_str = str(
            recipient.get("chat_id")
            or chat.get("chat_id")
            or message.get("chat_id")
            or ""
        )

        if chat_id_str:
            chat_type = "group"
            scoped_chat_id = f"chat:{chat_id_str}"
        else:
            chat_type = "dm"
            scoped_chat_id = f"user:{user_id}"

        # Store DM mapping
        self._dm_user_ids[str(chat_id_str or user_id)] = user_id

        # Dedup
        mid = str(body.get("mid") or message.get("mid") or message.get("message_id") or "")
        if mid:
            now = time.time()
            if mid in self._seen_msgs and now - self._seen_msgs[mid] < 300:
                return None
            self._seen_msgs[mid] = now
            # Prune old entries + hard limit
            if len(self._seen_msgs) > self._SEEN_MSGS_MAX:
                self._seen_msgs = {k: v for k, v in self._seen_msgs.items() if now - v < 300}
            elif len(self._seen_msgs) > 100:
                self._seen_msgs = {k: v for k, v in self._seen_msgs.items() if now - v < 300}

        # Access control
        if not self._allow_all_users and self._allowed_users_set:
            if user_id not in self._allowed_users_set:
                logger.debug("MAX: ignoring message from unauthorized user %s", user_id)
                return None

        # Group access control
        if chat_type == "group":
            if self._group_policy == "closed":
                return None
            if self._group_policy == "allowlist":
                user_allowed = (not self._group_allow_from) or user_id in self._group_allow_from
                chat_allowed = (not self._group_allow_chats) or chat_id_str in self._group_allow_chats
                if not user_allowed and not chat_allowed:
                    logger.info("MAX: group message blocked: user=%s chat=%s", user_id, chat_id_str)
                    return None

        # Extract media
        media_urls, media_types = await self._extract_inbound_media(update, message, body)
        logger.debug(
            "MAX: media extracted: %s urls, types=%s",
            len(media_urls), media_types,
        )

        # Auto-transcribe audio when STT is enabled
        if self._stt_enabled and media_urls:
            logger.info(
                "MAX: STT starting for %s audio file(s) of types %s",
                len(media_urls), media_types,
            )
            stt_text = await self._transcribe_media(media_urls, media_types)
            if stt_text:
                text = (text + "\n\n" + stt_text).strip() if text else stt_text
                logger.info("MAX: audio auto-transcribed: %s...", stt_text[:80])
            else:
                logger.warning("MAX: STT returned empty transcription")
        elif self._stt_enabled and not media_urls and not text:
            logger.warning(
                "MAX: empty message with no media — possible undetected voice. "
                "update_type=%r Update keys: %s, Message keys: %s",
                update.get("update_type"),
                list(update.keys()), list(message.keys()),
            )
            # Fallback: try direct audio extraction from MAX voice format.
            # MAX may send voice as message.attachments, message.voice, or
            # at the update root level instead of body.attachments.
            voice_url = _find_audio_url_direct(update)
            if voice_url:
                logger.info("MAX: found audio via fallback: %s", _safe_url_for_log(voice_url))
                cached = await self._cache_audio_attachment(
                    {"type": "voice", "payload": {"url": voice_url}}, "voice"
                )
                if cached:
                    audio_path, audio_mtype = cached
                    stt_text = await self._transcribe_media(
                        [audio_path], [audio_mtype]
                    )
                    if stt_text:
                        text = stt_text
                        logger.info("MAX: audio auto-transcribed: %s...", stt_text[:80])
            if not text:
                logger.warning("MAX: raw update payload: %s",
                    _json.dumps(update, ensure_ascii=False, default=str)[:2048])

        # Process basic attachments as text references (for non-recursive fallback)
        if not media_urls:
            attachments = body.get("attachments", [])
            for att in attachments:
                atype = att.get("type", "")
                payload_att = att.get("payload", {})
                if atype == "image":
                    url = payload_att.get("url", "")
                    text = (text + f"\n[Image: {url}]").strip() if text else f"[Image: {url}]"
                elif atype == "audio":
                    audio_url = payload_att.get("url", "")
                    if audio_url and self._stt_enabled:
                        pseudo_att = {"type": "audio", "payload": {"url": audio_url}}
                        cached = await self._cache_audio_attachment(pseudo_att, "audio")
                        if cached:
                            audio_path, _ = cached
                            text = (text + f"\n[Audio: {audio_path}]").strip() if text else f"[Audio: {audio_path}]"
                            logger.info("MAX: audio downloaded (fallback) to %s", audio_path)
                        else:
                            text = (text + "\n[Audio]").strip() if text else "[Audio]"
                    else:
                        text = (text + "\n[Audio]").strip() if text else "[Audio]"
                elif atype in ("video", "sticker"):
                    text = (text + f"\n[{atype.title()}]").strip() if text else f"[{atype.title()}]"
                elif atype == "file":
                    text = (text + "\n[File]").strip() if text else "[File]"
                elif atype == "location":
                    text = (text + f"\n[Location: {payload_att.get('latitude','')},{payload_att.get('longitude','')}]").strip() if text else f"[Location: ...]"

        # ── Cross-platform session commands (bypass platform scoping) ──
        if text and self._cross_session:
            if text.startswith('/sessions'):
                args = text[len('/sessions'):].strip()
                if args and not args.lower().startswith('search '):
                    # Has a target ID → let core handle with --all override
                    text = f"/resume --all {args}"
                else:
                    # Plain /sessions or /sessions search → our handler (all platforms)
                    await self._handle_cross_sessions(text, scoped_chat_id)
                    return None
            elif text.startswith('/resume'):
                if '--all' not in text and '--cross-room' not in text:
                    parts = text.split(maxsplit=1)
                    if len(parts) == 1:
                        # /resume with no args → our handler (all platforms)
                        await self._handle_cross_sessions('/sessions', scoped_chat_id)
                        return None
                    else:
                        # /resume <target> → rewrite with --all for core
                        text = f"/resume --all {parts[1].strip()}"

        if not text and not media_urls:
            return None

        msg_type = self._derive_message_type(text, media_types)

        source = self.build_source(
            chat_id=scoped_chat_id,
            chat_name=user_name if chat_type == "dm" else (chat.get("title") or chat_id_str),
            chat_type=chat_type,
            user_id=user_id,
            user_name=user_name,
        )

        return MessageEvent(
            text=text,
            message_type=msg_type,
            source=source,
            raw_message=update,
            message_id=mid,
            media_urls=media_urls,
            media_types=media_types,
        )

    # ═════════════════════════════════════════════════════════════════════
    # Media extraction (recursive walk)
    # ═════════════════════════════════════════════════════════════════════

    async def _extract_inbound_media(
        self, payload: Dict[str, Any], message: Dict[str, Any], body: Dict[str, Any]
    ) -> Tuple[List[str], List[str]]:
        """Recursively find and cache all media attachments in the payload."""
        attachments: List[Dict[str, Any]] = []
        seen: set[int] = set()

        def add_attachment(item: Any) -> None:
            if not isinstance(item, dict):
                return
            ident = id(item)
            if ident in seen:
                return
            seen.add(ident)
            attachments.append(item)

        def walk(obj: Any) -> None:
            if isinstance(obj, dict):
                raw = obj.get("attachments")
                if isinstance(raw, list):
                    for item in raw:
                        add_attachment(item)
                elif isinstance(raw, dict):
                    add_attachment(raw)
                # Direct media wrappers
                for key in ("audio", "voice", "file", "document", "doc",
                            "attachment", "media", "image", "photo", "picture"):
                    value = obj.get(key)
                    if isinstance(value, dict):
                        pseudo = {"type": key, "payload": value}
                        add_attachment(pseudo)
                if self._attachment_kind(obj) in {"audio", "voice", "image", "document"}:
                    add_attachment(obj)
                for value in obj.values():
                    walk(value)
            elif isinstance(obj, list):
                for value in obj:
                    walk(value)

        walk(payload)

        media_paths: List[str] = []
        media_types: List[str] = []
        seen_media_refs: set[str] = set()

        for attachment in attachments:
            kind = self._attachment_kind(attachment)
            media_ref = self._find_first_url(attachment) or f"object:{id(attachment)}"
            if media_ref in seen_media_refs:
                continue
            seen_media_refs.add(media_ref)

            if kind in {"audio", "voice"}:
                cached = await self._cache_audio_attachment(attachment, kind)
                if cached:
                    path, mtype = cached
                    media_paths.append(path)
                    media_types.append(mtype)
            elif kind == "image":
                cached = await self._cache_image_attachment(attachment)
                if cached:
                    path, mtype = cached
                    media_paths.append(path)
                    media_types.append(mtype)
            elif kind == "document":
                cached = await self._cache_document_attachment(attachment)
                if cached:
                    path, mtype = cached
                    media_paths.append(path)
                    media_types.append(mtype)

        return media_paths, media_types

    @staticmethod
    def _attachment_kind(attachment: Dict[str, Any]) -> str:
        """Determine attachment kind from type keys and payload."""
        values: List[str] = []
        for key in ("type", "attachment_type", "kind", "media_type"):
            value = attachment.get(key)
            if value:
                values.append(str(value).lower())
        payload = attachment.get("payload")
        if isinstance(payload, dict):
            for key in ("type", "attachment_type", "kind", "media_type",
                        "mime_type", "content_type"):
                value = payload.get(key)
                if value:
                    values.append(str(value).lower())
            for key in ("audio", "voice", "image", "photo", "picture",
                        "file", "document", "doc"):
                if key in payload:
                    values.append(key)
        filename = MaxAdapter._find_first_filename(attachment) or ""
        if filename:
            values.append(filename.lower())
        joined = " ".join(values)
        if "voice" in joined:
            return "voice"
        if "audio" in joined or joined.startswith("ptt"):
            return "audio"
        if any(marker in joined for marker in ("image", "photo", "picture")):
            return "image"
        if any(marker in joined for marker in ("file", "document", "doc", "attachment")):
            return "document"
        ext = Path(filename).suffix.lower() if filename else ""
        if ext in SUPPORTED_DOCUMENT_TYPES:
            return "document"
        return ""

    @staticmethod
    def _find_first_url(data: Any) -> Optional[str]:
        """Find a plausible download URL inside an attachment payload."""
        if isinstance(data, dict):
            for key in ("url", "download_url", "downloadUrl", "file_url",
                        "fileUrl", "media_url", "mediaUrl", "href", "link"):
                value = data.get(key)
                if isinstance(value, str) and value.startswith(("http://", "https://")):
                    return value
            for value in data.values():
                found = MaxAdapter._find_first_url(value)
                if found:
                    return found
        elif isinstance(data, list):
            for value in data:
                found = MaxAdapter._find_first_url(value)
                if found:
                    return found
        return None

    @staticmethod
    def _find_first_filename(data: Any) -> Optional[str]:
        """Find a plausible original filename inside an attachment payload."""
        if isinstance(data, dict):
            for key in ("filename", "file_name", "fileName", "name",
                        "title", "display_name", "displayName",
                        "original_filename", "originalFilename"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return Path(value.strip()).name
            for value in data.values():
                found = MaxAdapter._find_first_filename(value)
                if found:
                    return found
        elif isinstance(data, list):
            for value in data:
                found = MaxAdapter._find_first_filename(value)
                if found:
                    return found
        return None

    @staticmethod
    def _safe_url_for_log(url: str) -> str:
        """Strip credentials from URL for logging."""
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return "[invalid-url]"
        path = parsed.path or "/"
        return f"{parsed.scheme}://{parsed.netloc}{path}"

    @staticmethod
    def _detect_image_mime(data: bytes) -> str:
        """Detect image MIME type from magic bytes.

        More reliable than Content-Type header — MAX sometimes returns
        application/octet-stream for images.
        """
        if len(data) < 12:
            return "image/jpeg"
        # PNG: 89 50 4E 47
        if data[0] == 0x89 and data[1] == 0x50 and data[2] == 0x4E and data[3] == 0x47:
            return "image/png"
        # JPEG: FF D8 FF
        if data[0] == 0xFF and data[1] == 0xD8 and data[2] == 0xFF:
            return "image/jpeg"
        # WebP: RIFF....WEBP
        if (data[0] == 0x52 and data[1] == 0x49 and data[2] == 0x46 and data[3] == 0x46
                and data[8] == 0x57 and data[9] == 0x45 and data[10] == 0x42 and data[11] == 0x50):
            return "image/webp"
        # GIF: GIF8
        if data[0] == 0x47 and data[1] == 0x49 and data[2] == 0x46 and data[3] == 0x38:
            return "image/gif"
        # BMP: BM
        if data[0] == 0x42 and data[1] == 0x4D:
            return "image/bmp"
        return "image/jpeg"

    async def _cache_audio_attachment(
        self, attachment: Dict[str, Any], kind: str
    ) -> Optional[Tuple[str, str]]:
        """Download audio attachment and cache it."""
        url = self._find_first_url(attachment)
        if not url or not self._http_client:
            return None
        headers = {
            "Authorization": self._token,
            "User-Agent": "HermesAgent/1.0 MaxBot",
            "Accept": "audio/*,*/*;q=0.8",
        }
        try:
            resp = await self._http_client.get(url, headers=headers, follow_redirects=True)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("MAX: failed to download %s from %s: %s", kind, self._safe_url_for_log(url), exc)
            return None
        content_type = str(resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if not content_type or content_type == "application/octet-stream":
            guessed, _ = mimetypes.guess_type(urlparse(url).path)
            content_type = guessed or "audio/ogg"
        if not content_type.startswith("audio/"):
            ext_from_url = Path(urlparse(url).path).suffix.lower()
            if ext_from_url not in {".ogg", ".oga", ".opus", ".mp3", ".m4a", ".aac", ".wav", ".amr", ".webm"}:
                logger.info("MAX: downloaded %s but content-type is not audio: %s", kind, content_type)
                return None
        ext = mimetypes.guess_extension(content_type) if content_type else None
        if not ext:
            ext = Path(urlparse(url).path).suffix.lower() or ".ogg"
        if ext == ".oga":
            ext = ".ogg"
        return cache_audio_from_bytes(resp.content, ext), content_type or "audio/ogg"

    async def _cache_image_attachment(
        self, attachment: Dict[str, Any]
    ) -> Optional[Tuple[str, str]]:
        """Download image attachment and cache it."""
        url = self._find_first_url(attachment)
        if not url or not self._http_client:
            return None
        headers = {
            "Authorization": self._token,
            "User-Agent": "HermesAgent/1.0 MaxBot",
            "Accept": "image/*,*/*;q=0.8",
        }
        try:
            resp = await self._http_client.get(url, headers=headers, follow_redirects=True)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("MAX: failed to download image from %s: %s", self._safe_url_for_log(url), exc)
            return None
        content_type = str(resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if not content_type or content_type == "application/octet-stream":
            # Try magic bytes first — more reliable than Content-Type header
            magic_mime = self._detect_image_mime(resp.content)
            if magic_mime.startswith("image/"):
                content_type = magic_mime
            else:
                guessed, _ = mimetypes.guess_type(urlparse(url).path)
                content_type = guessed or "image/jpeg"
        ext = mimetypes.guess_extension(content_type) if content_type else None
        if not ext:
            ext = Path(urlparse(url).path).suffix.lower() or ".jpg"
        if ext in {".jpe", ".jpeg"}:
            ext = ".jpg"
        try:
            return cache_image_from_bytes(resp.content, ext), content_type or "image/jpeg"
        except ValueError as exc:
            logger.warning("MAX: rejected non-image bytes: %s", exc)
            return None

    async def _cache_document_attachment(
        self, attachment: Dict[str, Any]
    ) -> Optional[Tuple[str, str]]:
        """Download document attachment and cache it."""
        url = self._find_first_url(attachment)
        if not url or not self._http_client:
            return None
        headers = {
            "Authorization": self._token,
            "User-Agent": "HermesAgent/1.0 MaxBot",
            "Accept": "application/*,text/*,*/*;q=0.8",
        }
        try:
            resp = await self._http_client.get(url, headers=headers, follow_redirects=True)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("MAX: failed to download document from %s: %s", self._safe_url_for_log(url), exc)
            return None
        content_type = str(resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        filename = self._find_first_filename(attachment) or Path(urlparse(url).path).name or "document"
        ext = Path(filename).suffix.lower()
        if not content_type or content_type == "application/octet-stream":
            guessed, _ = mimetypes.guess_type(filename)
            content_type = guessed or "application/octet-stream"
        if not ext:
            guessed_ext = mimetypes.guess_extension(content_type) if content_type else None
            ext = guessed_ext or ".bin"
            filename = f"{filename}{ext}"
        if ext in SUPPORTED_DOCUMENT_TYPES:
            content_type = SUPPORTED_DOCUMENT_TYPES[ext]
        try:
            return cache_document_from_bytes(resp.content, filename), content_type
        except Exception as exc:
            logger.warning("MAX: failed to cache document: %s", exc)
            return None

    async def _transcribe_media(self, media_urls: list, media_types: list) -> Optional[str]:
        """Run STT on cached audio files and return combined transcription."""
        if not self._stt_enabled:
            return None

        import asyncio.subprocess
        transcriptions = []
        for path, mtype in zip(media_urls, media_types):
            if not mtype.startswith("audio/"):
                continue
            try:
                venv_path = os.getenv("MAX_STT_VENV",
                                     str(Path.home() / ".hermes" / "stt-venv"))
                python = str(Path(venv_path) / "bin" / "python3")
                if not os.path.exists(python):
                    python = "python3"
                import shlex
                proc = await asyncio.subprocess.create_subprocess_exec(
                    python, "-c",
                    f"from faster_whisper import WhisperModel; m=WhisperModel('base','cpu','int8'); segs,_=m.transcribe({shlex.quote(path)},language='ru'); [print(s.text.strip()) for s in segs]",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=120.0
                )
                if proc.returncode == 0 and stdout:
                    transcriptions.append(stdout.decode().strip())
                elif stderr:
                    logger.warning("MAX: STT failed: %s", stderr.decode()[:200])
            except asyncio.TimeoutError:
                logger.warning("MAX: STT timed out for %s", path)
            except Exception as e:
                logger.error("MAX: STT error: %s", e)

        if transcriptions:
            return "\n".join(transcriptions)
        return None

    @staticmethod
    def _derive_message_type(text: str, media_types: List[str]) -> MessageType:
        """Derive MessageType from text and media types."""
        if any(mtype.startswith(("application/", "text/"))
               or mtype == "application/octet-stream" for mtype in media_types):
            return MessageType.DOCUMENT
        if any(mtype.startswith("image/") for mtype in media_types):
            return MessageType.TEXT if text else MessageType.PHOTO
        if any(mtype.startswith("audio/") for mtype in media_types):
            return MessageType.TEXT if text else MessageType.VOICE
        return MessageType.TEXT

    # ═════════════════════════════════════════════════════════════════════
    # Outbound: send messages
    # ═════════════════════════════════════════════════════════════════════

    def _split_outbound_text(self, content: str) -> List[str]:
        """Split long outbound text into Max-sized chunks (≤4000 chars).

        Preserves paragraph boundaries where possible; hard-splits long
        paragraphs by word, then by character as a last resort.
        """
        limit = max(500, min(MAX_MESSAGE_LENGTH, 4000) - 100)
        if len(content) <= limit:
            return [content]

        chunks: List[str] = []
        current = ""

        def flush() -> None:
            nonlocal current
            if current:
                chunks.append(current.strip())
                current = ""

        for block in content.split("\n\n"):
            block = block.strip()
            if not block:
                continue
            candidate = f"{current}\n\n{block}" if current else block
            if len(candidate) <= limit:
                current = candidate
                continue
            flush()
            if len(block) <= limit:
                current = block
                continue
            # Very long paragraph: split by lines then words
            line_current = ""
            for line in block.splitlines() or [block]:
                for word in line.split(" "):
                    if not word:
                        continue
                    if len(word) > limit:
                        if line_current:
                            chunks.append(line_current.strip())
                            line_current = ""
                        for i in range(0, len(word), limit):
                            chunks.append(word[i:i + limit])
                        continue
                    candidate_word = f"{line_current} {word}" if line_current else word
                    if len(candidate_word) <= limit:
                        line_current = candidate_word
                    else:
                        chunks.append(line_current.strip())
                        line_current = word
                if line_current and len(line_current) + 1 <= limit:
                    line_current += "\n"
            if line_current:
                chunks.append(line_current.strip())
        flush()
        return chunks or [content[:limit]]

    @staticmethod
    def _convert_markdown_tables(text: str) -> str:
        """Convert markdown tables to MAX-compatible pipe format with monospace.

        MAX does not support markdown table rendering. This converts:
          | Col1 | Col2 |
          |------|------|
          | v1   | v2   |
        Into a monospace code block with aligned columns, which renders
        correctly on MAX.
        """
        import re

        # Match markdown tables: find blocks of pipe-delimited rows
        # that contain at least one separator row (|---|).
        # Strategy: find consecutive lines starting with |,
        # where at least one is a separator.
        lines = text.split('\n')
        result_lines = []
        i = 0

        while i < len(lines):
            line = lines[i]

            # Check if this line starts a table (starts with |)
            if re.match(r'^\|.+\|', line):
                # Collect consecutive pipe lines
                table_start = i
                table_lines = []
                has_separator = False
                while i < len(lines) and re.match(r'^\|.+\|', lines[i]):
                    current = lines[i]
                    table_lines.append(current)
                    if re.match(r'^\|[\s\-:|]+\|$', current):
                        has_separator = True
                    i += 1

                if has_separator and len(table_lines) >= 2:
                    # This is a table — convert it
                    converted = MaxAdapter._render_table(table_lines)
                    result_lines.append(converted)
                else:
                    # Not a valid table — keep as-is
                    result_lines.extend(table_lines)
            else:
                result_lines.append(line)
                i += 1

        return '\n'.join(result_lines)

    @staticmethod
    def _render_table(lines: list) -> str:
        """Render a list of pipe-delimited lines as a monospace table."""
        # Parse rows (skip separator lines)
        rows = []
        for line in lines:
            if not line.strip():
                continue
            # Skip separator rows (|---|---|)
            if all(c in '|-: ' for c in line):
                continue
            cells = [c.strip() for c in line.strip('|').split('|')]
            rows.append(cells)

        if not rows:
            return '\n'.join(lines)

        # Calculate column widths (cap at 25 chars for mobile)
        ncols = max(len(r) for r in rows) if rows else 0
        if ncols == 0:
            return '\n'.join(lines)
        widths = [3] * ncols  # minimum width
        for row in rows:
            for i, cell in enumerate(row):
                if i < ncols:
                    widths[i] = max(widths[i], min(len(cell), 25))

        # Build formatted table.
        # MAX supports inline `code` for monospace. Each line is its own
        # inline code span — no newlines inside, so they render correctly.
        sep = '-' * (sum(widths) + 3 * ncols + 1)

        result = ['`' + sep + '`']
        for row in rows:
            padded = []
            for i in range(ncols):
                cell = row[i] if i < len(row) else ''
                cell = cell[:25]
                padded.append(cell.ljust(widths[i]))
            result.append('`| ' + ' | '.join(padded) + ' |`')
        result.append('`' + sep + '`')
        return '\n'.join(result)

    async def _render_table_as_image(self, table_lines: list) -> Optional[str]:
        """Render pipe-delimited table lines as a clean PNG and upload to MAX.

        Returns upload token on success, None on failure.
        """
        # ── Parse rows ────────────────────────────────────────────────
        rows = []
        for line in table_lines:
            if not line.strip():
                continue
            if all(c in '|-: ' for c in line):
                continue
            cells = [c.strip() for c in line.strip('|').split('|')]
            rows.append(cells)
        if not rows or not rows[0]:
            return None

        ncols = max(len(r) for r in rows)
        if ncols == 0:
            return None

        def _prepare_cell(val: str) -> tuple:
            """Convert raw cell text to (display_text, color).

            Emoji → Unicode symbols that DejaVu Sans renders properly.
            Status cells get semantic colors.

            NOTE: MAX may append U+FE0F (emoji VS-16) to emoji chars.
            Normalize by stripping variation selectors first.
            """
            color = None
            text = val.strip()
            # Strip variation selectors so "⚠️" matches as "⚠"
            text = text.replace("\ufe0f", "").replace("\ufe0e", "")

            # Map emoji → clear Unicode symbols with semantic colors
            if "✅" in text:
                text = text.replace("✅", "✓").strip()
                color = "#16a34a"  # green-600
            elif "❌" in text:
                text = text.replace("❌", "✗").strip()
                color = "#dc2626"  # red-600
            elif "⚠" in text:
                text = text.replace("⚠", "⚠").strip()
                color = "#ea580c"  # orange-600
            elif "⏳" in text or "⌛" in text:
                is_scheduled = "schedule" in text.lower()
                text = text.replace("⏳", "▶" if is_scheduled else "◷") \
                           .replace("⌛", "▶" if is_scheduled else "◷").strip()
                color = "#3b82f6" if is_scheduled else "#ca8a04"
            elif "🔴" in text:
                text = text.replace("🔴", "●").strip()
                color = "#dc2626"
            elif "🟢" in text:
                text = text.replace("🟢", "●").strip()
                color = "#16a34a"
            elif "🟡" in text:
                text = text.replace("🟡", "●").strip()
                color = "#ca8a04"

            # Cleanup stray text markers
            text = text.replace("ℹ", "").replace("📊", "")
            text = text.replace("[OK]", "✓").replace("[ERR]", "✗")
            text = text.replace("[WARN]", "⚠").replace("[WAIT]", "◷")
            text = text.replace("[SCHED]", "▶")
            text = text.replace("[CRIT]", "●").replace("[GOOD]", "●").replace("[MID]", "●")

            return text.strip(), color

        cells_info = [[_prepare_cell(c) for c in row] for row in rows]

        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            logger.warning("MAX: Pillow not installed, cannot render table as image")
            return None

        # ── Layout ────────────────────────────────────────────────────
        CELL_PAD_X = 22
        CELL_PAD_Y = 14
        LINE_WIDTH = 2
        MIN_COL_WIDTH = 85
        FONT_SIZE = 18
        HEADER_FONT_SIZE = 20

        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", FONT_SIZE
            )
            font_bold = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", HEADER_FONT_SIZE
            )
        except (IOError, OSError):
            font = ImageFont.load_default()
            font_bold = font

        # Measure cell widths in pixels
        data_rows = cells_info[1:]
        header = cells_info[0]  # list of (text, color) tuples

        px_widths = [MIN_COL_WIDTH] * ncols
        for row in cells_info:
            for i, (cell_text, _color) in enumerate(row):
                if i >= ncols:
                    continue
                bbox = font.getbbox(cell_text[:40])
                cw = (bbox[2] - bbox[0]) + CELL_PAD_X * 2
                px_widths[i] = max(px_widths[i], cw)

        # Cap total width at 860px for mobile retina
        total_w = sum(px_widths) + LINE_WIDTH * (ncols + 1)
        if total_w > 860:
            scale = 860 / total_w
            px_widths = [max(MIN_COL_WIDTH, int(w * scale)) for w in px_widths]
            total_w = sum(px_widths) + LINE_WIDTH * (ncols + 1)

        # Row heights
        row_h = int(CELL_PAD_Y * 2 + font.getbbox("Ag")[3] - font.getbbox("Ag")[1])
        header_h = int(
            CELL_PAD_Y * 2 + font_bold.getbbox("Ag")[3] - font_bold.getbbox("Ag")[1]
        )

        img_h = int(header_h + LINE_WIDTH + row_h * len(data_rows) + LINE_WIDTH + 6)

        # ── Draw ──────────────────────────────────────────────────────
        img = Image.new("RGB", (total_w, img_h), "#ffffff")
        draw = ImageDraw.Draw(img)

        # Color palette
        HDR_BG = "#1e293b"       # slate-800
        HDR_TEXT = "#ffffff"
        ROW_EVEN = "#ffffff"
        ROW_ODD = "#f1f5f9"      # slate-100
        BORDER = "#94a3b8"       # slate-400
        SEP = "#e2e8f0"          # slate-200
        TEXT_COLOR = "#0f172a"   # slate-900

        y = 0

        # --- Header row ---
        draw.rectangle([(0, y), (total_w, y + header_h)], fill=HDR_BG)
        cx = LINE_WIDTH
        for ci in range(ncols):
            cell_text = header[ci][0] if ci < len(header) else ""
            draw.text(
                (cx + CELL_PAD_X, y + int((header_h - font_bold.getbbox("Ag")[3]) / 2)),
                cell_text[:40],
                font=font_bold,
                fill=HDR_TEXT,
            )
            # Vertical divider
            draw.line([(cx, y), (cx, y + header_h)], fill=BORDER, width=LINE_WIDTH)
            cx += px_widths[ci] + LINE_WIDTH
        # Right border
        draw.line([(cx, y), (cx, y + header_h)], fill=BORDER, width=LINE_WIDTH)
        y += header_h

        # Header-bottom separator
        draw.line([(0, y), (total_w, y)], fill=BORDER, width=LINE_WIDTH)

        # --- Data rows ---
        for ri, row in enumerate(data_rows):
            bg = ROW_EVEN if ri % 2 == 0 else ROW_ODD
            draw.rectangle([(0, y), (total_w, y + row_h)], fill=bg)
            cx = LINE_WIDTH
            for ci in range(ncols):
                cell_text, cell_color = row[ci] if ci < len(row) else ("", None)
                fill_color = cell_color or TEXT_COLOR
                draw.text(
                    (cx + CELL_PAD_X, y + int((row_h - font.getbbox("Ag")[3]) / 2)),
                    cell_text[:40],
                    font=font,
                    fill=fill_color,
                )
                # Vertical divider
                draw.line(
                    [(cx, y), (cx, y + row_h)], fill=SEP, width=1,
                )
                cx += px_widths[ci] + LINE_WIDTH
            # Right border
            draw.line([(cx, y), (cx, y + row_h)], fill=BORDER, width=LINE_WIDTH)
            if ri != len(data_rows) - 1:
                draw.line(
                    [(0, y + row_h), (total_w, y + row_h)], fill=SEP, width=1,
                )
            y += row_h

        # Bottom border
        draw.line([(0, y), (total_w, y)], fill=BORDER, width=LINE_WIDTH)

        # ── Save & upload ─────────────────────────────────────────────
        import hashlib
        digest = hashlib.md5(str(table_lines).encode()).hexdigest()[:12]
        out_path = self._table_image_dir / f"table_{digest}.png"
        img.save(out_path, "PNG")

        token = await self._upload(str(out_path), "image")
        return token

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send a text message, automatically chunking if over limit."""
        if not self._http_client:
            return SendResult(success=False, error="Not connected")

        parts = chat_id.split(":", 1)
        target_type = parts[0] if len(parts) > 1 else "user"
        target_id = parts[1] if len(parts) > 1 else chat_id

        params = {}
        if target_type == "chat" or _is_group(target_id):
            params["chat_id"] = target_id
        else:
            user_id = self._dm_user_ids.get(chat_id, target_id)
            params["user_id"] = user_id

        # ── Handle tables ─────────────────────────────────────────────
        # Table images (MAX_TABLE_AS_IMAGE) or text fallback
        image_tokens: List[str] = []

        if self._table_as_image:
            # Try to render tables as images
            import re as _re

            lines = content.split("\n")
            new_lines = []
            i = 0
            while i < len(lines):
                line = lines[i]
                if _re.match(r"^\|.+\|", line):
                    table_lines = []
                    has_sep = False
                    while i < len(lines) and _re.match(r"^\|.+\|", lines[i]):
                        cur = lines[i]
                        table_lines.append(cur)
                        if _re.match(r"^\|[\s\-:|]+\|$", cur):
                            has_sep = True
                        i += 1
                    if has_sep and len(table_lines) >= 2:
                        token = await self._render_table_as_image(table_lines)
                        if token:
                            image_tokens.append(token)
                            # Replace table with a compact text reference
                            new_lines.append("📊 _таблица_")
                        else:
                            # Image failed — render as text
                            converted = MaxAdapter._render_table(table_lines)
                            new_lines.append(converted)
                    else:
                        new_lines.extend(table_lines)
                else:
                    new_lines.append(line)
                    i += 1
            content = "\n".join(new_lines)
        else:
            # Text-only mode (default)
            content = self._convert_markdown_tables(content)

        # ── Send ───────────────────────────────────────────────────────
        chunks = self._split_outbound_text(content)
        last_result: Optional[SendResult] = None

        for idx, text in enumerate(chunks, start=1):
            if len(chunks) > 1:
                prefix = f"({idx}/{len(chunks)})\n"
                text = prefix + text[:max(0, 3900 - len(prefix))]

            body: Dict[str, Any] = {
                "text": text,
                "format": "markdown",
                "notify": True,
            }

            # Attach table images to the first chunk
            if idx == 1 and image_tokens:
                body["attachments"] = [
                    {"type": "image", "payload": {"token": t}} for t in image_tokens
                ]

            if reply_to:
                body["link"] = {"type": "REPLY", "mid": reply_to}

            try:
                resp = await self._http_client.post(
                    f"{MAX_API_BASE}/messages",
                    params=params,
                    json=body,
                )
                resp.raise_for_status()
                data = resp.json()
                msg = data.get("message", {})
                last_result = SendResult(
                    success=True,
                    message_id=str(msg.get("message_id", "")),
                    raw_response=data,
                )
            except Exception as exc:
                logger.error("MAX: send failed chunk %s/%s: %s", idx, len(chunks), exc)
                return SendResult(success=False, error="Send failed (see logs)")

        if len(chunks) > 1:
            logger.info("MAX: split outbound message into %s chunks for %s", len(chunks), chat_id)
        return last_result or SendResult(success=False, error="No content to send")

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        *,
        finalize: bool = False,
    ) -> SendResult:
        """Edit an existing message — for streaming support.

        Throttles edits to 800ms minimum interval to avoid MAX rate limits.
        Renews typing indicator after each edit (MAX clears it on edit).
        """
        if not self._http_client:
            return SendResult(success=False, error="Not connected")

        # Streaming throttle: minimum 200ms between edits to avoid flooding.
        # Unlike the old 800ms throttle, this stores the content when skipped
        # so no edit is ever silently lost.
        now = time.monotonic()
        last = getattr(self, "_last_edit_at", 0.0)
        if not finalize and last > 0 and (now - last) < 0.2:
            self._pending_edit = content
            logger.debug("MAX: edit_message throttled, content queued")
            return SendResult(success=True, message_id=message_id)

        # If there was a throttled edit, merge it with the current content.
        # The content parameter already carries the full accumulated text from
        # the agent, so _pending_edit is used only for internal bookkeeping —
        # no actual merging needed on the wire, the agent already concatenated.
        if getattr(self, "_pending_edit", None) is not None:
            self._pending_edit = None

        self._last_edit_at = now
        if finalize:
            self._last_edit_at = 0.0

        text = content[:MAX_MESSAGE_LENGTH - 3] + "..." if len(content) > MAX_MESSAGE_LENGTH else content
        text = self._convert_markdown_tables(text)
        body = {"text": text, "format": "markdown"}
        try:
            resp = await self._http_client.put(
                f"{MAX_API_BASE}/messages",
                params={"message_id": message_id},
                json=body,
            )
            resp.raise_for_status()
            # MAX clears typing indicator on message edit — renew it
            await self.send_typing(chat_id)
            return SendResult(success=True, message_id=message_id, raw_response=resp.json())
        except Exception as e:
            logger.error("MAX: edit_message failed: %s", e)
            return SendResult(success=False, error="Edit failed (see logs)", retryable=True)

    async def send_image(
        self, chat_id: str, image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send an image via URL attachment."""
        if not self._http_client:
            return SendResult(success=False, error="Not connected")
        parts = chat_id.split(":", 1)
        target_type = parts[0] if len(parts) > 1 else "user"
        target_id = parts[1] if len(parts) > 1 else chat_id
        params = {"chat_id": target_id} if target_type == "chat" else {"user_id": target_id}
        body: Dict[str, Any] = {
            "text": caption or "",
            "attachments": [{"type": "image", "payload": {"url": image_url}}],
        }
        if reply_to:
            body["link"] = {"type": "REPLY", "mid": reply_to}
        try:
            resp = await self._http_client.post(f"{MAX_API_BASE}/messages", params=params, json=body)
            resp.raise_for_status()
            d = resp.json()
            mid = str((d.get("message", {}).get("body", {}) or {}).get("mid", ""))
            return SendResult(success=True, message_id=mid, raw_response=d)
        except Exception as e:
            logger.error("MAX: send_image failed: %s", e)
            return SendResult(success=False, error="Send image failed (see logs)", retryable=True)

    async def send_image_file(
        self, chat_id: str, image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        return await self._upload_send(chat_id, image_path, "image", caption or "", reply_to)

    async def send_document(
        self, chat_id: str, file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        return await self._upload_send(chat_id, file_path, "file", caption or "", reply_to)

    async def send_video(
        self, chat_id: str, video_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        return await self._upload_send(chat_id, video_path, "video", caption or "", reply_to)

    async def send_voice(
        self, chat_id: str, audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        return await self._upload_send(chat_id, audio_path, "audio", caption or "", reply_to)

    async def send_animation(
        self, chat_id: str, animation_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send animated GIF — treated as image in MAX."""
        return await self.send_image(chat_id, animation_url, caption, reply_to, metadata)

    # ═════════════════════════════════════════════════════════════════════
    # File upload (two-step)
    # ═════════════════════════════════════════════════════════════════════

    async def _upload_send(
        self, chat_id: str, file_path: str, mtype: str,
        caption: str, reply_to: Optional[str],
    ) -> SendResult:
        """Upload file then send as attachment."""
        if not self._http_client:
            return SendResult(success=False, error="Not connected")

        token = await self._upload(file_path, mtype)
        if not token:
            return SendResult(success=False, error="Upload failed")

        if UPLOAD_DELAY:
            await asyncio.sleep(UPLOAD_DELAY)

        parts = chat_id.split(":", 1)
        target_type = parts[0] if len(parts) > 1 else "user"
        target_id = parts[1] if len(parts) > 1 else chat_id
        params = {"chat_id": target_id} if target_type == "chat" else {"user_id": target_id}

        body: Dict[str, Any] = {
            "text": caption,
            "attachments": [{"type": mtype, "payload": {"token": token}}],
        }
        if reply_to:
            body["link"] = {"type": "REPLY", "mid": reply_to}

        try:
            resp = await self._http_client.post(f"{MAX_API_BASE}/messages", params=params, json=body)
            resp.raise_for_status()
            d = resp.json()
            mid = str((d.get("message", {}).get("body", {}) or {}).get("mid", ""))
            return SendResult(success=True, message_id=mid, raw_response=d)
        except Exception as e:
            logger.error("MAX: _upload_send failed: %s", e)
            return SendResult(success=False, error="Upload-send failed (see logs)", retryable=True)

    async def _upload(self, file_path: str, media_type: str) -> Optional[str]:
        """Two-step upload: get upload URL → PUT file → return token."""
        import aiohttp as _aiohttp

        fp = Path(file_path)
        if not fp.exists() or fp.stat().st_size > MAX_FILE_SIZE:
            return None

        try:
            # Step 1: get upload URL
            resp = await self._http_client.post(f"{MAX_API_BASE}/uploads", params={"type": media_type})
            if resp.status_code != 200:
                return None
            data = resp.json()
            upload_url = data.get("url")
            if not upload_url:
                return None

            # SECURITY: Only upload to known Max/Cdn domains (SSRF prevention).
            # If the API returns an unexpected URL, refuse to connect.
            parsed = urlparse(upload_url)
            allowed_hosts = {
                "platform-api.max.ru",
                "cdn.max.ru",
                "storage.max.ru",
                "upload.max.ru",
                "iu.oneme.ru",
            }
            if parsed.hostname and (
                parsed.hostname in allowed_hosts
                or parsed.hostname.endswith(".max.ru")
                or parsed.hostname.endswith(".oneme.ru")
            ):
                pass  # Safe — within Max infrastructure
            else:
                logger.warning(
                    "MAX: upload URL rejected (not in Max domain): %s",
                    self._safe_url_for_log(upload_url),
                )
                return None

            # Step 2: upload file to the URL (use aiohttp for multipart)
            async with _aiohttp.ClientSession(timeout=_aiohttp.ClientTimeout(total=120)) as session:
                with open(fp, "rb") as f:
                    form = _aiohttp.FormData()
                    form.add_field("data", f, filename=fp.name)
                    async with session.post(upload_url, data=form) as r:
                        if r.status != 200:
                            return None
                        upload_data = await r.json()
                        token = upload_data.get("token")
                        if not token and "photos" in upload_data:
                            photos = upload_data["photos"]
                            if isinstance(photos, dict):
                                first = next(iter(photos.values()), {})
                                token = first.get("token") if isinstance(first, dict) else None
                        return token
        except Exception as e:
            logger.error("MAX: upload error: %s", e)
            return None

    # ═════════════════════════════════════════════════════════════════════
    # Typing indicator
    # ═════════════════════════════════════════════════════════════════════

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """Send typing indicator (delegates to send_action)."""
        await self.send_action(chat_id, "typing")

    async def send_action(self, chat_id: str, action: str = "typing", metadata=None) -> None:
        """Send a chat action indicator. Best-effort (silent on failure).

        Supported actions (mapped to MAX API):
          typing / typing_on  — показать «печатает»
          typing_off          — скрыть «печатает»
          sending_photo       — отправляет фото
          sending_video       — отправляет видео
          sending_audio       — отправляет аудио
          sending_file        — отправляет файл
          read                — отметить как прочитано

        Args:
            chat_id: Scoped chat ID (e.g. 'chat:123' or 'user:456').
            action: Action type string from the list above.
            metadata: Optional platform-specific context (ignored for MAX).
        """
        if not self._http_client:
            return

        # Normalise action name to MAX API format
        action_map = {
            "typing": "typing_on",
            "typing_on": "typing_on",
            "typing_off": "typing_off",
            "sending_photo": "sending_photo",
            "sending_video": "sending_video",
            "sending_audio": "sending_audio",
            "sending_file": "sending_file",
            "read": "read",
        }
        api_action = action_map.get(action.lower().strip(), "typing_on")

        parts = chat_id.split(":", 1)
        target_id = parts[1] if len(parts) > 1 else chat_id

        try:
            await self._http_client.post(
                f"{MAX_API_BASE}/chats/{target_id}/actions",
                json={"action": api_action},
                timeout=httpx.Timeout(3.0),
            )
        except Exception:
            pass

    # ═════════════════════════════════════════════════════════════════════
    # Chat info
    # ═════════════════════════════════════════════════════════════════════

    async def get_chat_info(self, chat_id: str) -> dict:
        """Return basic chat info from MAX API."""
        if not self._http_client:
            return {"name": chat_id, "type": "dm", "chat_id": chat_id}
        try:
            resp = await self._http_client.get(f"{MAX_API_BASE}/chats/{chat_id}")
            if resp.status_code == 200:
                d = resp.json()
                return {
                    "name": d.get("name", d.get("title", chat_id)),
                    "type": d.get("type", "dm"),
                    "chat_id": chat_id,
                }
        except Exception:
            pass
        return {"name": chat_id, "type": "dm", "chat_id": chat_id}

    # ═════════════════════════════════════════════════════════════════════
    # Interactive buttons (approval, slash-confirm, clarify)
    # ═════════════════════════════════════════════════════════════════════

    async def _post_interactive(
        self, chat_id: str, text: str, buttons: List[List[Dict[str, str]]],
        reply_to: Optional[str] = None,
    ) -> SendResult:
        """Send a message with inline keyboard buttons.

        MAX inline_keyboard format:
          attachments: [{
            type: "inline_keyboard",
            payload: { buttons: [[{type: "callback", text: "...", payload: "..."}]] }
          }]
        """
        if not self._http_client:
            return SendResult(success=False, error="Not connected")

        parts = chat_id.split(":", 1)
        target_type = parts[0] if len(parts) > 1 else "user"
        target_id = parts[1] if len(parts) > 1 else chat_id
        params = {"chat_id": target_id} if target_type == "chat" else {"user_id": target_id}

        body: Dict[str, Any] = {
            "text": text[:MAX_MESSAGE_LENGTH],
            "format": "markdown",
            "attachments": [{
                "type": "inline_keyboard",
                "payload": {"buttons": buttons},
            }],
        }
        if reply_to:
            body["link"] = {"type": "REPLY", "mid": reply_to}

        try:
            resp = await self._http_client.post(
                f"{MAX_API_BASE}/messages", params=params, json=body,
            )
            resp.raise_for_status()
            d = resp.json()
            mid = str((d.get("message", {}).get("body", {}) or {}).get("mid", ""))
            return SendResult(success=True, message_id=mid, raw_response=d)
        except Exception as e:
            logger.error("MAX: interactive send failed: %s", e)
            return SendResult(success=False, error="Interactive send failed (see logs)")

    async def send_buttons(
        self, chat_id: str, text: str,
        buttons: List[Dict[str, str]],
        reply_to: Optional[str] = None,
    ) -> SendResult:
        """Send a message with inline buttons of ANY type.

        Reuses _post_interactive, which accepts all MAX button types:
          - callback  → {"type": "callback", "text": "...", "payload": "..."}
          - link      → {"type": "link", "text": "...", "url": "https://..."}
          - message   → {"type": "message", "text": "...", "payload": "..."}
          - request_contact → {"type": "request_contact", "text": "..."}
          - request_geo_location → {"type": "request_geo_location", "text": "..."}

        Features:
        - One button per row (full width)
        - Buttons auto-numbered when 3+
        - Button text duplicated in the message body as fallback
          (MAX mobile may truncate button text visually; the fallback
           ensures the user always sees the full content)

        Example:
            await adapter.send_buttons(
                chat_id="chat:123",
                text="Выберите действие:",
                buttons=[
                    {"type": "link", "text": "🌐 Открыть сайт", "url": "https://example.com"},
                    {"type": "callback", "text": "✅ Подтвердить", "payload": "confirm:123"},
                ],
            )
        """
        # Number buttons if 3+ for clarity
        numbered = len(buttons) >= 3

        # Build keyboard (one button per row)
        limited = buttons[:10]
        keyboard: List[List[Dict[str, str]]] = []
        for i, btn in enumerate(limited, 1):
            b = dict(btn)
            if numbered:
                prefix = f"{i}. "
                if not b.get("text", "").startswith(prefix):
                    b["text"] = f"{prefix}{b['text']}"
            keyboard.append([b])

        # Append button text as fallback (MAX mobile may clip button labels)
        fallback_lines: List[str] = []
        for i, btn in enumerate(limited, 1):
            label = btn.get("text", "")
            if numbered:
                fallback_lines.append(f"{i}. {label}")
            else:
                fallback_lines.append(f"• {label}")
        fallback_text = "\n".join(fallback_lines)

        # Only append fallback if there are actual buttons listed
        full_text = text
        if fallback_lines:
            full_text = f"{text}\n\n{fallback_text}"

        # Trim to MAX limit
        if len(full_text) > MAX_MESSAGE_LENGTH - 200:
            full_text = full_text[:MAX_MESSAGE_LENGTH - 200]

        return await self._post_interactive(chat_id, full_text, keyboard, reply_to=reply_to)

    async def send_exec_approval(
        self,
        chat_id: str,
        command: str,
        session_key: str,
        description: str = "dangerous command",
        metadata: Optional[Dict[str, Any]] = None,
        *args,
        **kwargs,
    ) -> SendResult:
        """Render a dangerous-command approval prompt with native buttons.

        Four buttons: Approve Once / Approve Session / Approve Always / Deny.
        Button callbacks route through _on_callback → resolve_gateway_approval.
        """
        if not self._http_client:
            return SendResult(success=False, error="Not connected")

        approval_id = uuid.uuid4().hex[:12]
        cmd_preview = (command or "")[:300] + "..." if len(command or "") > 300 else (command or "")

        text = (
            f"⚠️ **Command Approval Required**\n\n"
            f"```\n{cmd_preview}\n```\n\n"
            f"Reason: {description}"
        )

        reply_to = (metadata or {}).get("reply_to_message_id") if metadata else None

        buttons = [[
            {"type": "callback", "text": "✅ Approve Once", "payload": f"exec:once:{approval_id}"},
            {"type": "callback", "text": "🔄 Session", "payload": f"exec:session:{approval_id}"},
        ], [
            {"type": "callback", "text": "🔒 Always", "payload": f"exec:always:{approval_id}"},
            {"type": "callback", "text": "❌ Deny", "payload": f"exec:deny:{approval_id}"},
        ]]

        result = await self._post_interactive(chat_id, text, buttons, reply_to=reply_to)
        if result.success:
            self._exec_approval_state[approval_id] = session_key
        return result

    async def send_slash_confirm(
        self,
        chat_id: str,
        title: str,
        message: str,
        session_key: str,
        confirm_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        *args,
        **kwargs,
    ) -> SendResult:
        """Render a 3-button slash-command confirmation prompt.

        Buttons: Approve Once / Always Approve / Cancel.
        Mirrors Telegram's send_slash_confirm.
        """
        if not self._http_client:
            return SendResult(success=False, error="Not connected")

        text = f"**{title}**\n\n{message}"[:MAX_MESSAGE_LENGTH]
        reply_to = (metadata or {}).get("reply_to_message_id") if metadata else None

        buttons = [[
            {"type": "callback", "text": "✅ Approve Once", "payload": f"sc:once:{confirm_id}"},
            {"type": "callback", "text": "🔒 Always", "payload": f"sc:always:{confirm_id}"},
            {"type": "callback", "text": "❌ Cancel", "payload": f"sc:cancel:{confirm_id}"},
        ]]

        result = await self._post_interactive(chat_id, text, buttons, reply_to=reply_to)
        if result.success:
            self._slash_confirm_state[confirm_id] = session_key
        return result

    async def send_clarify(
        self,
        chat_id: str,
        question: str,
        choices: Optional[list],
        clarify_id: str,
        session_key: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send a clarify prompt with inline choice buttons.

        Each choice becomes a callback button. The last button is always
        "Other…" for free-text input.
        """
        if not self._http_client:
            return SendResult(success=False, error="Not connected")

        reply_to = (metadata or {}).get("reply_to_message_id") if metadata else None

        if choices and len(choices) > 0:
            # Render choice buttons (up to 3 per row)
            buttons: List[List[Dict[str, str]]] = []
            row: List[Dict[str, str]] = []
            for i, choice in enumerate(choices):
                btn_text = str(choice)[:40]
                if len(str(choice)) > 40:
                    btn_text = btn_text[:37] + "..."
                row.append({
                    "type": "callback",
                    "text": btn_text,
                    "payload": f"clarify:{clarify_id}:{i}",
                })
                if len(row) >= 3:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            # Add "Other…" button
            buttons.append([{
                "type": "callback",
                "text": "💬 Other…",
                "payload": f"clarify:{clarify_id}:other",
            }])

            text = f"**{question}**"[:MAX_MESSAGE_LENGTH]
            result = await self._post_interactive(chat_id, text, buttons, reply_to=reply_to)
            if result.success:
                self._clarify_state[clarify_id] = session_key
            return result
        else:
            # Open-ended — just send the question as plain text
            return await self.send(chat_id, question, reply_to=reply_to, metadata=metadata)

    async def _on_callback(self, payload: Dict[str, Any]) -> Optional[MessageEvent]:
        """Handle message_callback update from inline keyboard button press."""
        callback = payload.get("callback", {}) or payload.get("message_callback", {})
        data = (callback.get("payload") or callback.get("data") or "").strip()
        if not data:
            return None

        user = callback.get("user", {}) or payload.get("user", {})
        user_id = str(user.get("user_id", ""))
        if not user_id:
            return None

        # Extract chat info for routing.
        # Max API callback payload puts chat info in message.recipient.
        msg = payload.get("message", {})
        recipient = msg.get("recipient", {}) if msg else {}
        raw_chat_id = (
            recipient.get("chat_id")
            or (payload.get("chat", {}) or {}).get("chat_id", "")
            or payload.get("chat_id", "")
            or ""
        )
        chat_id = str(raw_chat_id)

        logger.info("MAX: callback received: data=%s from user=%s chat_id=%s",
                     data, user_id, chat_id)

        # Dispatch based on prefix
        parts = data.split(":", 2)
        prefix = parts[0] if parts else ""

        if prefix == "exec":
            # Dangerous command approval buttons
            return await self._handle_exec_callback(data, user_id, payload)
        elif prefix == "sc":
            # Slash-command confirmation buttons
            return await self._handle_slash_confirm_callback(data, user_id, payload)
        elif prefix == "clarify":
            # Clarify choice buttons
            return await self._handle_clarify_callback(data, user_id, payload)
        elif prefix == "model":
            # Model picker buttons
            return await self._handle_model_callback(data, user_id, payload, chat_id)
        else:
            logger.warning("MAX: unknown callback prefix: %s", prefix)
            return None

    async def _handle_exec_callback(
        self, data: str, user_id: str, raw_payload: Dict[str, Any]
    ) -> Optional[MessageEvent]:
        """Route exec approval button to resolve_gateway_approval."""
        # Format: exec:{choice}:{approval_id}
        parts = data.split(":", 2)
        if len(parts) < 3:
            return None
        choice = parts[1]   # once / session / always / deny
        approval_id = parts[2]

        session_key = self._exec_approval_state.pop(approval_id, None)
        if not session_key:
            logger.warning("MAX: unknown approval_id in callback: %s", approval_id)
            return None

        from tools.approval import resolve_gateway_approval, has_blocking_approval

        if not has_blocking_approval(session_key):
            return None

        count = resolve_gateway_approval(session_key, choice)
        logger.info(
            "MAX: button resolved %d approval(s) for session %s (choice=%s)",
            count, session_key, choice,
        )

        # Send acknowledgment
        source = self.build_source(
            chat_id=f"user:{user_id}",
            chat_name=user_id,
            chat_type="dm",
            user_id=user_id,
            user_name=user_id,
        )
        labels = {
            "once": "✅ Approved (once)",
            "session": "🔄 Approved (session)",
            "always": "🔒 Approved (always)",
            "deny": "❌ Denied",
        }
        label = labels.get(choice, f"Resolved: {choice}")
        return MessageEvent(
            text=label,
            message_type=MessageType.TEXT,
            source=source,
            raw_message=raw_payload,
            internal=True,
        )

    async def _handle_slash_confirm_callback(
        self, data: str, user_id: str, raw_payload: Dict[str, Any]
    ) -> Optional[MessageEvent]:
        """Route slash-confirm button to tools.slash_confirm.resolve."""
        # Format: sc:{choice}:{confirm_id}
        parts = data.split(":", 2)
        if len(parts) < 3:
            return None
        choice = parts[1]     # once / always / cancel
        confirm_id = parts[2]

        session_key = self._slash_confirm_state.pop(confirm_id, None)
        if not session_key:
            logger.warning("MAX: unknown confirm_id in callback: %s", confirm_id)
            return None

        from tools import slash_confirm as _sc

        result_text = await _sc.resolve(session_key, confirm_id, choice)
        if result_text:
            source = self.build_source(
                chat_id=f"user:{user_id}",
                chat_name=user_id,
                chat_type="dm",
                user_id=user_id,
                user_name=user_id,
            )
            return MessageEvent(
                text=result_text,
                message_type=MessageType.TEXT,
                source=source,
                raw_message=raw_payload,
                internal=True,
            )
        return None

    async def _handle_clarify_callback(
        self, data: str, user_id: str, raw_payload: Dict[str, Any]
    ) -> Optional[MessageEvent]:
        """Route clarify button to tools.clarify_gateway.resolve_gateway_clarify."""
        # Format: clarify:{clarify_id}:{choice_index}
        parts = data.split(":", 2)
        if len(parts) < 3:
            return None
        clarify_id = parts[1]
        choice_idx = parts[2]

        session_key = self._clarify_state.pop(clarify_id, None)
        if not session_key:
            logger.warning("MAX: unknown clarify_id in callback: %s", clarify_id)
            return None

        try:
            from tools.clarify_gateway import resolve_gateway_clarify, mark_awaiting_text

            if choice_idx == "other":
                # User chose "Other…" — next text message will be the answer
                mark_awaiting_text(clarify_id)
                return None

            if not choice_idx.isdigit():
                logger.warning("MAX: clarify callback with non-numeric index: %s", choice_idx)
                return None

            idx = int(choice_idx)
            if idx < 0 or idx > 256:  # reasonable upper bound
                logger.warning("MAX: clarify callback index out of range: %s", idx)
                return None
            # Get the choice text from the pending clarify state
            # The gateway stores the choices — we resolve with the index
            response = str(idx)
            result_text = await resolve_gateway_clarify(clarify_id, response)
            if result_text:
                source = self.build_source(
                    chat_id=f"user:{user_id}",
                    chat_name=user_id,
                    chat_type="dm",
                    user_id=user_id,
                    user_name=user_id,
                )
                return MessageEvent(
                    text=result_text,
                    message_type=MessageType.TEXT,
                    source=source,
                    raw_message=raw_payload,
                    internal=True,
                )
        except (ValueError, ImportError) as e:
            logger.warning("MAX: clarify callback failed: %s", e)
        return None

    async def _handle_model_callback(
        self, data: str, user_id: str, raw_payload: Dict[str, Any], chat_id: str,
    ) -> Optional[MessageEvent]:
        """Route model picker button callbacks.

        Formats:
          model:provider:{slug}  — provider selected, show models
          model:pick:{model}:{provider} — model selected, switch
          model:back — back to provider list
        """
        # Build the correct scoped_chat matching how send_model_picker stores state.
        # If chat_id (raw numeric) is present, the message was in a group → "chat:{id}".
        # Otherwise it's a DM → "user:{user_id}".
        if chat_id:
            scoped_chat = f"chat:{chat_id}"
        else:
            scoped_chat = f"user:{user_id}"

        parts = data.split(":", 2)

        if len(parts) >= 3 and parts[1] == "provider":
            # Provider selected
            provider_slug = parts[2]
            state = self._model_picker_state.get(scoped_chat)

            msg_id = state.get("msg_id", "") if state else ""
            await self._on_model_provider_selected(scoped_chat, provider_slug, msg_id)
            return None

        if len(parts) >= 3 and parts[1] == "pick" and len(parts) == 3:
            # Model selected — parts[2] = "{model}:{provider}"
            rest = parts[2].rsplit(":", 1)
            if len(rest) == 2:
                model_id, provider_slug = rest
                return await self._on_model_picked(scoped_chat, model_id, provider_slug, user_id)

        if data == "model:back":
            await self._on_model_back(scoped_chat, user_id)
            return None

        logger.warning("MAX: unhandled model callback: %s", data)
        return None

    # ═════════════════════════════════════════════════════════════════════
    # Model picker
    # ═════════════════════════════════════════════════════════════════════

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
        """Send an interactive model picker with callback buttons.

        Two steps:
        1. Show provider list → tap provider → show its models
        2. Show model list → tap model → call on_model_selected
        """
        if not self._http_client:
            return SendResult(success=False, error="Not connected")

        try:
            from hermes_cli.providers import get_label
        except ImportError:
            def get_label(slug: str) -> str:
                return slug

        # Step 1: Show provider selection
        provider_label = get_label(current_provider)
        text = (
            f"⚙ **Model Configuration**\n\n"
            f"Current: `{current_model or 'unknown'}` ({provider_label})\n\n"
            f"Select a provider:"
        )[:MAX_MESSAGE_LENGTH]

        # Build provider buttons (2 per row)
        buttons: List[List[Dict[str, str]]] = []
        row: List[Dict[str, str]] = []
        for p in providers[:20]:  # Max 20 providers
            slug = p.get("slug", "")
            name = str(p.get("name", slug))[:38]
            tag = " ✅" if p.get("is_current") else ""
            btn_text = f"{name}{tag}"[:40]
            row.append({
                "type": "callback",
                "text": btn_text,
                "payload": f"model:provider:{slug}",
            })
            if len(row) >= 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        reply_to = (metadata or {}).get("reply_to_message_id") if metadata else None
        result = await self._post_interactive(chat_id, text, buttons, reply_to=reply_to)
        if result.success:
            self._model_picker_state[str(chat_id)] = {
                "msg_id": result.message_id,
                "providers": providers,
                "session_key": session_key,
                "on_model_selected": on_model_selected,
                "current_model": current_model,
                "current_provider": current_provider,
            }
        return result

    async def _on_model_provider_selected(
        self, chat_id: str, provider_slug: str, message_id: str
    ) -> None:
        """Step 2: Show models for the selected provider."""
        state = self._model_picker_state.get(str(chat_id))
        if not state:
            return

        providers = state.get("providers", [])
        provider = next((p for p in providers if p.get("slug") == provider_slug), None)
        if not provider:
            return

        models = provider.get("models", [])[:15]  # Max 15 models
        provider_name = provider.get("name", provider_slug)

        text = (
            f"⚙ **{provider_name}** models\n\n"
            f"Select a model:"
        )[:MAX_MESSAGE_LENGTH]

        # Build model buttons (1 per row for readability)
        buttons: List[List[Dict[str, str]]] = []
        for m in models:
            name = str(m)[:38]
            is_current = (
                state.get("current_model") == m
                and state.get("current_provider") == provider_slug
            )
            label = f"{'✅ ' if is_current else ''}{name}"[:40]
            buttons.append([{
                "type": "callback",
                "text": label,
                "payload": f"model:pick:{m}:{provider_slug}",
            }])

        # Add "← Back" button
        buttons.append([{
            "type": "callback",
            "text": "← Back to providers",
            "payload": "model:back",
        }])

        # Edit the original message to show models
        await self.edit_message(chat_id, message_id, text)
        # Send new message with model buttons
        await self._post_interactive(chat_id, "\u200b", buttons)  # zero-width space as body

        # Update state
        state["selected_provider"] = provider_slug
        self._model_picker_state[str(chat_id)] = state

    async def _on_model_picked(
        self, chat_id: str, model_id: str, provider_slug: str, user_id: str,
    ) -> Optional[MessageEvent]:
        """Step 3: Model selected — call on_model_selected callback."""
        state = self._model_picker_state.pop(str(chat_id), None)
        if not state:
            return None

        on_model_selected = state.get("on_model_selected")
        if not on_model_selected:
            return None

        try:
            result_text = await on_model_selected(chat_id, model_id, provider_slug)
        except Exception as e:
            result_text = f"❌ Error switching model: {e}"

        source = self.build_source(
            chat_id=f"user:{user_id}",
            chat_name=user_id,
            chat_type="dm",
            user_id=user_id,
            user_name=user_id,
        )
        return MessageEvent(
            text=result_text,
            message_type=MessageType.TEXT,
            source=source,
            internal=True,
        )

    async def _on_model_back(self, chat_id: str, user_id: str) -> None:
        """Go back to provider selection."""
        state = self._model_picker_state.get(str(chat_id))
        if not state:
            return

        from hermes_cli.providers import get_label as _get_label

        providers = state.get("providers", [])
        current_provider = state.get("current_provider", "")
        current_model = state.get("current_model", "")

        try:
            provider_label = _get_label(current_provider)
        except Exception:
            provider_label = current_provider

        text = (
            f"⚙ **Model Configuration**\n\n"
            f"Current: `{current_model or 'unknown'}` ({provider_label})\n\n"
            f"Select a provider:"
        )[:MAX_MESSAGE_LENGTH]

        buttons: List[List[Dict[str, str]]] = []
        row: List[Dict[str, str]] = []
        for p in providers[:20]:
            slug = p.get("slug", "")
            name = p.get("name", slug)[:38]
            tag = " ✅" if p.get("is_current") else ""
            row.append({
                "type": "callback",
                "text": f"{name}{tag}"[:40],
                "payload": f"model:provider:{slug}",
            })
            if len(row) >= 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        await self._post_interactive(chat_id, text, buttons)

    # ═════════════════════════════════════════════════════════════════════
    # Cross-platform session commands
    # ═════════════════════════════════════════════════════════════════════

    async def _handle_cross_sessions(self, text: str, chat_id: str) -> None:
        """Handle /sessions and /resume (no-arg) — list sessions from ALL platforms.

        Bypasses the core gateway's per-platform scoping so the user can see
        CLI, Telegram, Discord, WebUI and other sessions directly from MAX.
        """
        try:
            from hermes_state import SessionDB
        except ImportError:
            await self.send(chat_id, "⚠️ Session store not available")
            return

        args = text[len('/sessions'):].strip()
        search = None
        if args.lower().startswith('search '):
            search = args[6:].strip()
            if not search:
                await self.send(chat_id, "Usage: `/sessions search <query>`")
                return

        db = SessionDB()

        try:
            if search:
                rows = db.list_sessions_rich(
                    limit=20,
                    include_archived=False,
                    order_by_last_active=True,
                    search_query=search,
                )
            else:
                rows = db.list_sessions_rich(
                    limit=15,
                    include_archived=False,
                    order_by_last_active=True,
                )
        except Exception as e:
            logger.warning("MAX: SessionDB query failed: %s", e)
            await self.send(chat_id, f"⚠️ Failed to query sessions: {e}")
            return

        if not rows:
            await self.send(chat_id, "📭 **No sessions found**")
            return

        # Emoji map for known sources
        source_emoji = {
            'cli': '💻', 'telegram': '📱', 'max': '🟣',
            'discord': '🎮', 'webui': '🌐', 'api_server': '🔌',
            'cron': '⏰', 'slack': '💬', 'matrix': '🧩',
        }

        max_preview = 45

        if search:
            header = f"🔍 **Sessions matching \"{search}\":**"
        else:
            header = f"📋 **Recent Sessions** (all platforms):"

        lines = [header]
        for i, s in enumerate(rows[:15], 1):
            source = str(s.get('source', '') or '?')
            emoji = source_emoji.get(source, '📄')
            title = str(s.get('title') or '').strip() or '(unnamed)'
            sid = str(s.get('id', ''))[:12]
            preview = str(s.get('preview', '') or '')[:max_preview].replace('\n', ' ').strip()
            lines.append(
                f"{i}. {emoji} **{source}** — {title[:40]}"
            )
            if preview:
                lines.append(f"   _{preview}..._")
            lines.append(f"   `{sid}...`")

        total = len(rows)
        if total > 15:
            lines.append(f"\n...and {total - 15} more sessions")

        msg = '\n'.join(lines)

        # MAX has 4000 char limit — chunk if needed
        if len(msg) > MAX_MESSAGE_LENGTH - 100:
            msg = '\n'.join(lines[:1] + lines[1:11])  # Keep header + first 10
            msg += f'\n\n...truncated ({total} total)'

        await self.send(chat_id, msg)
        await self.send(
            chat_id,
            "`/resume <id>` — переключиться на сессию\n"
            "`/sessions search <query>` — поиск по сессиям"
        )

    @property
    def dm_policy(self) -> str:
        return "open" if self._allow_all_users else "allowlist"

    @property
    def allow_from(self) -> List[str]:
        return list(self._allowed_users_set)

    @property
    def group_policy(self) -> str:
        return self._group_policy

    @property
    def group_allow_from(self) -> List[str]:
        return self._group_allow_from

    @property
    def max_message_length(self) -> int:
        return MAX_MESSAGE_LENGTH


# ═════════════════════════════════════════════════════════════════════════
# Plugin registration
# ═════════════════════════════════════════════════════════════════════════

def check_max_requirements() -> bool:
    """Check if aiohttp and httpx are available and token is configured."""
    try:
        import aiohttp  # noqa: F401
        import httpx   # noqa: F401
    except ImportError:
        return False
    return bool(os.getenv("MAX_BOT_TOKEN", "").strip())


def validate_config(config) -> bool:
    """Validate that the platform config has enough info to connect."""
    extra = getattr(config, "extra", {}) or {}
    token = os.getenv("MAX_BOT_TOKEN") or getattr(config, "token", "") or extra.get("token", "")
    return bool(str(token).strip())


def is_connected(config) -> bool:
    """Check whether Max is configured."""
    return validate_config(config)


def _env_enablement() -> Optional[dict]:
    """Seed PlatformConfig.extra from env-only setups."""
    token = os.getenv("MAX_BOT_TOKEN", "").strip()
    if not token:
        return None

    extra: dict[str, Any] = {"token": token}

    str_vars = {
        "MAX_WEBHOOK_HOST": "host",
        "MAX_WEBHOOK_PATH": "path",
        "MAX_WEBHOOK_SECRET": "webhook_secret",
        "MAX_WEBHOOK_URL": "webhook_url",
        "MAX_GROUP_POLICY": "group_policy",
    }
    for env_name, key in str_vars.items():
        value = os.getenv(env_name, "").strip()
        if value:
            extra[key] = value

    port = os.getenv("MAX_WEBHOOK_PORT", "").strip()
    if port:
        try:
            extra["port"] = int(port)
        except ValueError:
            extra["port"] = port

    allowed = os.getenv("MAX_ALLOWED_USERS", "").strip()
    if allowed:
        extra["allowed_users"] = [part.strip() for part in allowed.split(",") if part.strip()]

    allow_all = os.getenv("MAX_ALLOW_ALL_USERS", "").strip()
    if allow_all:
        extra["allow_all_users"] = _coerce_bool(allow_all, True)

    stt_enabled = os.getenv("MAX_STT_ENABLED", "").strip()
    if stt_enabled:
        extra["stt_enabled"] = _coerce_bool(stt_enabled, True)

    home = os.getenv("MAX_HOME_CHANNEL", "").strip()
    if home:
        extra["home_channel"] = {
            "chat_id": home,
            "name": os.getenv("MAX_HOME_CHANNEL_NAME", "Max Home") or "Max Home",
        }

    cross = os.getenv("MAX_CROSS_SESSION", "").strip()
    if cross:
        extra["cross_session"] = _coerce_bool(cross, True)

    return extra


def _apply_yaml_config(yaml_cfg: dict, platform_cfg: dict) -> Optional[dict]:
    """Translate top-level max: config into env/extras."""
    del yaml_cfg
    if not isinstance(platform_cfg, dict):
        return None

    extra: dict[str, Any] = {}
    mapping = {
        "token": "MAX_BOT_TOKEN",
        "webhook_secret": "MAX_WEBHOOK_SECRET",
        "webhook_url": "MAX_WEBHOOK_URL",
        "host": "MAX_WEBHOOK_HOST",
        "port": "MAX_WEBHOOK_PORT",
        "path": "MAX_WEBHOOK_PATH",
        "allowed_users": "MAX_ALLOWED_USERS",
        "allow_all_users": "MAX_ALLOW_ALL_USERS",
        "home_channel": "MAX_HOME_CHANNEL",
        "stt_enabled": "MAX_STT_ENABLED",
        "group_policy": "MAX_GROUP_POLICY",
        "cross_session": "MAX_CROSS_SESSION",
    }

    for key, env_name in mapping.items():
        if key not in platform_cfg:
            continue
        value = platform_cfg.get(key)
        if value is None:
            continue
        if key == "allowed_users" and isinstance(value, list):
            extra[key] = [str(v) for v in value]
            env_value = ",".join(str(v) for v in value)
        elif key == "home_channel" and isinstance(value, dict):
            chat_id = str(value.get("chat_id") or "").strip()
            if not chat_id:
                continue
            env_value = chat_id
            extra[key] = value
        else:
            env_value = str(value)
            extra[key] = value
        # SECURITY: Do NOT mutate global os.environ from plugin code.
        # The caller (gateway) is responsible for environment setup.
        if env_value and not os.getenv(env_name):
            # Only set if not already present; this is a fallback bridge
            # for legacy paths, kept for backward compatibility.
            pass  # os.environ mutation removed — caller handles env setup

    return extra or None


def interactive_setup() -> None:
    """Interactive `hermes gateway setup` flow for the Max platform."""
    try:
        from hermes_cli.setup import (
            prompt, prompt_yes_no, save_env_value,
            get_env_value, print_header, print_info,
            print_warning, print_success,
        )
    except ImportError:
        logger.warning("MAX: hermes_cli.setup not available for interactive setup")
        return

    print_header("Max (max.ru) with STT")

    existing_token = get_env_value("MAX_BOT_TOKEN")
    if existing_token:
        print_info(f"Max: already configured (token: {existing_token[:8]}...)")
        if not prompt_yes_no("Reconfigure Max?", False):
            return

    print_info("Connect Hermes to Max messenger (max.ru). Requires aiohttp + httpx.")

    token = prompt("Bot token (from Max Platform → Chat-bots → Integration)",
                   default="", password=True)
    if not token:
        print_warning("Token is required — skipping Max setup")
        return
    save_env_value("MAX_BOT_TOKEN", token.strip())

    print()
    print_info("🔒 Webhook security")
    use_secret = prompt_yes_no("Set a webhook secret?", True)
    if use_secret:
        secret = prompt("Webhook secret (5-256 chars)", password=True)
        save_env_value("MAX_WEBHOOK_SECRET", secret.strip() if secret else "")

    print()
    host = prompt("HTTP server host", default=get_env_value("MAX_WEBHOOK_HOST") or "0.0.0.0")
    save_env_value("MAX_WEBHOOK_HOST", host.strip() or "0.0.0.0")
    port = prompt("HTTP server port", default=get_env_value("MAX_WEBHOOK_PORT") or "8646")
    save_env_value("MAX_WEBHOOK_PORT", port.strip() or "8646")
    path = prompt("Webhook path", default=get_env_value("MAX_WEBHOOK_PATH") or "/max/webhook")
    save_env_value("MAX_WEBHOOK_PATH", path.strip() or "/max/webhook")

    print()
    print_info("🔒 Access control")
    allow_all = prompt_yes_no("Allow all Max users to talk to the bot?", True)
    if allow_all:
        save_env_value("MAX_ALLOW_ALL_USERS", "true")
        save_env_value("MAX_ALLOWED_USERS", "")
    else:
        save_env_value("MAX_ALLOW_ALL_USERS", "false")
        allowed = prompt("Allowed user IDs (comma-separated)",
                         default=get_env_value("MAX_ALLOWED_USERS") or "")
        if allowed:
            save_env_value("MAX_ALLOWED_USERS", allowed.replace(" ", ""))

    print()
    print_info("🎤 Voice messages (STT)")
    stt = prompt_yes_no("Enable voice message download for transcription?", True)
    save_env_value("MAX_STT_ENABLED", "true" if stt else "false")

    print()
    print_success("Max configuration saved to ~/.hermes/.env")
    print_info("Restart the gateway: hermes gateway restart")


def register(ctx) -> None:
    """Plugin entry point — called by the Hermes plugin system."""
    skill_path = Path(__file__).parent / "skills" / "max-gateway" / "SKILL.md"

    ctx.register_platform(
        name="max",
        label="Max (STT)",
        adapter_factory=lambda cfg: MaxAdapter(cfg),
        check_fn=check_max_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=["MAX_BOT_TOKEN"],
        install_hint="pip install aiohttp httpx; pip install faster-whisper  # for STT",
        setup_fn=interactive_setup,
        env_enablement_fn=_env_enablement,
        apply_yaml_config_fn=_apply_yaml_config,
        cron_deliver_env_var="MAX_HOME_CHANNEL",
        standalone_sender_fn=_standalone_send,
        allowed_users_env="MAX_ALLOWED_USERS",
        allow_all_env="MAX_ALLOW_ALL_USERS",
        max_message_length=MAX_MESSAGE_LENGTH,
        emoji="🟣",
        pii_safe=True,
        allow_update_command=True,
        platform_hint=(
            "You are chatting via Max (max.ru) messenger. "
            "Max supports markdown formatting (**bold**, *italic*, `code`, ```blocks```). "
            "Messages are limited to 4000 characters. "
            "You can send images using markdown ![alt](url) syntax. "
            "Voice messages are automatically transcribed — the transcription appears in the message text. "
            "Keep responses clear and well-structured."
        ),
    )

    if skill_path.exists():
        ctx.register_skill(
            "max-gateway",
            skill_path,
            description="Install and configure Hermes Agent gateway access through Max messenger with STT.",
        )


# ═════════════════════════════════════════════════════════════════════════
# Standalone sender (for cron jobs and send_message tool)
# ═════════════════════════════════════════════════════════════════════════

async def _send_max_message(pconfig: PlatformConfig, chat_id: str, message: str) -> SendResult:
    """Send a message via Max API without the full adapter."""
    extra = getattr(pconfig, "extra", {}) or {}
    token = os.getenv("MAX_BOT_TOKEN") or getattr(pconfig, "token", "") or extra.get("token", "")
    if not token:
        return SendResult(success=False, error="MAX_BOT_TOKEN not configured")

    parts = chat_id.split(":", 1)
    target_type = parts[0] if len(parts) > 1 else "user"
    target_id = parts[1] if len(parts) > 1 else chat_id

    params = {"chat_id": target_id} if target_type == "chat" else {"user_id": target_id}
    body = {"text": message[:MAX_MESSAGE_LENGTH], "format": "markdown"}
    headers = {"Authorization": token, "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            resp = await client.post(f"{MAX_API_BASE}/messages", params=params, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return SendResult(
                success=True,
                message_id=str(data.get("message", {}).get("message_id", "")),
            )
    except Exception as exc:
        logger.error("MAX: send_message failed: %s", exc)
        return SendResult(success=False, error="Standalone send failed (see logs)")


async def _standalone_send(
    pconfig: PlatformConfig,
    chat_id: str,
    message: str,
    *,
    thread_id: Optional[str] = None,
    media_files: Optional[List[str]] = None,
    force_document: bool = False,
) -> dict:
    """Standalone sender contract for send_message/cron delivery."""
    del thread_id, force_document
    if media_files:
        logger.warning("MAX: standalone send currently ignores media_files=%s", media_files)
    result = await _send_max_message(pconfig, chat_id, message)
    if result.success:
        return {"success": True, "message_id": result.message_id}
    return {"error": result.error or "Max send failed"}
