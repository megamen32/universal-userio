"""MAX (oneme.ru) WebSocket transport client for the ``max`` channel.

Speaks the OneMe JSON WebSocket protocol (ver 11) that web.max.ru itself uses:
``wss://ws-api.oneme.ru/websocket`` with envelope frames
``{"ver": 11, "cmd": 0|1|3, "seq": n, "opcode": op, "payload": {...}}``.

Session lifecycle: INIT (opcode 6, deviceId + userAgent) -> LOGIN (opcode 19,
access token) -> normal requests. Heartbeat PING (opcode 1) keeps the socket
alive; DISPATCH (opcode 128) frames carry incoming events for the ingress.

Protocol reference: community reverse-engineering docs
(https://github.com/pr0bel1230/max-api-docs). The access token is the session
token minted by the SMS login flow (VERIFICATION_REQUEST opcode 17 ->
CODE_ENTER opcode 18); the Android app's native token is NOT valid here.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

import websocket

WS_URL = "wss://ws-api.oneme.ru/websocket"

OP_PING = 1
OP_INIT = 6
OP_LOGIN = 19
OP_GET_CHATS = 53
OP_GET_HISTORY = 49
OP_SEND = 64
OP_DISPATCH = 128

_HEARTBEAT_INTERVAL = 25.0


class MaxTransport:
    """Thin wrapper over a websocket-client connection (overridable in tests)."""

    def __init__(self, *, socks_host: str = "", socks_port: int = 0) -> None:
        self._socks_host, self._socks_port = socks_host, socks_port

    def connect(self, timeout: float) -> Any:
        if self._socks_host:
            import socket as pysocket
            import ssl

            import socks

            raw = socks.socksocket(pysocket.AF_INET, pysocket.SOCK_STREAM)
            raw.set_proxy(socks.SOCKS5, self._socks_host, self._socks_port)
            raw.settimeout(timeout)
            raw.connect(("ws-api.oneme.ru", 443))
            tls = ssl.create_default_context().wrap_socket(
                raw, server_hostname="ws-api.oneme.ru"
            )
            ws = websocket.WebSocket()
            ws.connect(WS_URL, socket=tls, timeout=timeout)
            return ws
        return websocket.create_connection(WS_URL, timeout=timeout)

    @staticmethod
    def send(ws: Any, text: str) -> None:
        ws.send(text)

    @staticmethod
    def recv(ws: Any, timeout: float) -> dict[str, Any] | None:
        ws.settimeout(timeout)
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            return None
        if not raw:
            return None
        return json.loads(raw)

    @staticmethod
    def close(ws: Any) -> None:
        try:
            ws.close()
        except Exception:  # noqa: BLE001 - best-effort teardown
            pass


class MaxClient:
    """Synchronous MAX session client: one command = one locked round-trip."""

    def __init__(
        self,
        token: str,
        device_id: str,
        *,
        socks_host: str = "",
        socks_port: int = 0,
        app_version: str = "26.6.17",
        transport: MaxTransport | None = None,
    ) -> None:
        self._token = token
        self._device_id = device_id or str(uuid.uuid4())
        self._app_version = app_version
        self._transport = transport or MaxTransport(socks_host=socks_host, socks_port=socks_port)
        self._ws: Any = None
        self._lock = threading.Lock()
        self._seq = 0
        self._profile: dict[str, Any] | None = None
        self._last_heartbeat = 0.0

    # -- session -----------------------------------------------------------

    @property
    def profile(self) -> dict[str, Any] | None:
        return self._profile

    def connected(self) -> bool:
        ws = self._ws
        return bool(ws) and getattr(ws, "connected", False)

    def connect(self) -> dict[str, Any]:
        """INIT + LOGIN; returns the LOGIN payload (profile, chats, users)."""
        if self.connected():
            return self._profile or {}
        ws = self._transport.connect(timeout=20)
        self._ws, self._seq, self._last_heartbeat = ws, 0, time.monotonic()
        init = self._roundtrip(
            OP_INIT,
            {
                "deviceId": self._device_id,
                "userAgent": {
                    "deviceType": "WEB",
                    "pushDeviceType": "WEBPUSH",
                    "locale": "ru",
                    "deviceLocale": "ru",
                    "osVersion": "Linux",
                    "deviceName": "Firefox",
                    "headerUserAgent": (
                        "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
                    ),
                    "appVersion": self._app_version,
                    "screen": "1366x900 1.0x",
                    "timezone": "Europe/Moscow",
                },
            },
        )
        if not init:
            raise RuntimeError("MAX INIT returned no payload")
        login = self._roundtrip(
            OP_LOGIN,
            {
                "interactive": True,
                "token": self._token,
                "chatsCount": 100,
                "chatsSync": 100,
                "contactsSync": 0,
                "presenceSync": 0,
                "draftsSync": 0,
            },
        )
        if not isinstance(login, dict) or not login.get("profile"):
            raise RuntimeError("MAX LOGIN rejected the session token")
        self._profile = login
        return login

    def close(self) -> None:
        with self._lock:
            if self._ws is not None:
                self._transport.close(self._ws)
                self._ws = None

    def _ensure(self) -> None:
        if not self.connected():
            self.connect()

    # -- protocol core ------------------------------------------------------

    def _send_envelope(self, opcode: int, payload: dict[str, Any]) -> int:
        self._seq += 1
        frame = {"ver": 11, "cmd": 0, "seq": self._seq, "opcode": opcode, "payload": payload}
        self._transport.send(self._ws, json.dumps(frame, ensure_ascii=False))
        return self._seq

    def _heartbeat_if_due(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_heartbeat < _HEARTBEAT_INTERVAL:
            return
        if self._ws is not None:
            self._send_envelope(OP_PING, {"interactive": False})
        self._last_heartbeat = now

    def _roundtrip(
        self, opcode: int, payload: dict[str, Any], *, timeout: float = 20.0
    ) -> Any:
        """Send one request and read frames until its ACK (cmd=1) or error (cmd=3)."""
        with self._lock:
            self._ensure()
            self._send_envelope(opcode, payload)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                frame = self._transport.recv(self._ws, max(1.0, deadline - time.monotonic()))
                if frame is None:
                    continue
                if frame.get("opcode") == opcode and frame.get("cmd") in (1, 3):
                    if frame.get("cmd") == 3:
                        error = frame.get("payload") or {}
                        raise RuntimeError(
                            f"MAX opcode {opcode} failed: {error.get('error') or error}"
                        )
                    return frame.get("payload")
            raise TimeoutError(f"MAX opcode {opcode} timed out after {timeout}s")

    # -- public commands ----------------------------------------------------

    def chats(self, *, count: int = 50) -> list[dict[str, Any]]:
        payload = self._roundtrip(OP_GET_CHATS, {"count": count, "marker": int(time.time() * 1000)})
        chats = (payload or {}).get("chats")
        return list(chats) if isinstance(chats, list) else []

    def history(self, chat_id: int | str, *, backward: int = 30) -> list[dict[str, Any]]:
        payload = self._roundtrip(
            OP_GET_HISTORY,
            {"chatId": int(chat_id), "backward": int(backward), "forward": 0},
        )
        messages = (payload or {}).get("messages")
        return list(messages) if isinstance(messages, list) else []

    def send(self, *, chat_id: int | str, text: str) -> dict[str, Any]:
        payload = self._roundtrip(
            OP_SEND,
            {
                "chatId": int(chat_id),
                "message": {
                    "text": text,
                    "cid": int(time.time() * 1000),
                    "elements": [],
                    "attaches": [],
                },
                "notify": True,
            },
        )
        return {
            "chat_id": str((payload or {}).get("chatId") or chat_id),
            "message_id": str(((payload or {}).get("message") or {}).get("id") or ""),
            "receipt": "max-ws-accepted",
        }

    def dispatch(self, *, timeout: float = 25.0) -> dict[str, Any] | None:
        """Read one DISPATCH frame if it arrives before the timeout (and keep the heartbeat due)."""
        with self._lock:
            self._ensure()
            self._heartbeat_if_due()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                frame = self._transport.recv(self._ws, max(1.0, deadline - time.monotonic()))
                if frame is None:
                    continue
                if frame.get("opcode") == OP_DISPATCH:
                    return frame.get("payload") or {}
                if frame.get("opcode") == OP_PING:
                    self._heartbeat_if_due(force=True)
            return None


def dispatch_to_message(payload: Any) -> dict[str, Any] | None:
    """Best-effort extraction of a chat message from a DISPATCH payload.

    Returns ``{chat_id, message_id, sender, text, time, out}`` or ``None`` when
    the event carries no user-visible message (typing, presence, edits...).
    """
    if not isinstance(payload, dict):
        return None
    message = payload.get("message") if isinstance(payload.get("message"), dict) else payload
    chat_id = payload.get("chatId") or message.get("chatId") or message.get("chat_id")
    text = message.get("text")
    message_id = message.get("id")
    if chat_id is None or message_id is None or not str(text or "").strip():
        return None
    sender = message.get("sender")
    return {
        "chat_id": str(chat_id),
        "message_id": str(message_id),
        "sender": "" if sender is None else str(sender),
        "text": str(text),
        "time": message.get("time"),
        "out": bool(message.get("out")) or sender is None,
    }
