"""WhatsApp channel adapter over the Baileys HTTP bridge (spec Phase 4).

The bridge is the same Node sidecar Hermes uses
(``scripts/whatsapp-bridge/bridge.js``; a pinned copy ships in
``deploy/whatsapp-bridge/``).  It exposes ``GET /health``, ``GET /messages``
(drain queue), ``POST /send``/``/edit``/``/typing`` on loopback.  The bridge
has no inbound media download endpoint, so ``media`` is not declared.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import urllib.request
from collections.abc import Mapping
from contextlib import asynccontextmanager
from typing import Any

from universal_userio.channels.core import (
    AdapterNotSupported,
    ChatMessage,
    ChatRef,
    ChatSummary,
    MessageRef,
)

DEFAULT_BRIDGE_URL = "http://127.0.0.1:30100"


def _ref_id(message_id: str) -> int:
    """Map a Baileys string message id to the stable int ChatMessage.id."""

    return int(hashlib.sha256(str(message_id).encode()).hexdigest()[:12], 16)


def normalize_chat_id(chat: ChatRef) -> str:
    """Accept raw JIDs (``...@s.whatsapp.net``, ``...@lid``) or bare digits."""

    value = str(getattr(chat, "id", chat))
    if "@" in value:
        return value
    return value.lstrip("+") + "@s.whatsapp.net"


def _extract_text(item: Mapping[str, Any]) -> str:
    message = item.get("message") or {}
    if not isinstance(message, Mapping):
        return ""
    text = message.get("conversation")
    if not text:
        extended = message.get("extendedTextMessage")
        text = extended.get("text") if isinstance(extended, Mapping) else None
    if not text:
        for kind in ("imageMessage", "videoMessage", "documentMessage"):
            part = message.get(kind)
            if isinstance(part, Mapping) and part.get("caption"):
                text = part["caption"]
                break
    return str(text or "")


class WhatsAppBridgeClient:
    """Small stdlib HTTP client for the loopback Baileys bridge."""

    def __init__(
        self, base_url: str = DEFAULT_BRIDGE_URL, *, timeout: float = 30.0,
        runner: Any = urllib.request.urlopen,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._runner = runner

    def _request(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> Any:
        data = None
        headers = {"Host": "127.0.0.1"}
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method
        )
        with self._runner(request, timeout=self.timeout) as response:
            body = response.read()
        return json.loads(body) if body else None

    def health(self) -> dict[str, Any]:
        result = self._request("GET", "/health")
        return dict(result or {})

    def drain_messages(self) -> list[dict[str, Any]]:
        result = self._request("GET", "/messages")
        return list(result or [])

    def send(self, *, chat_id: str, text: str, reply_to: str | None = None) -> str:
        payload: dict[str, Any] = {"chatId": chat_id, "message": text}
        if reply_to:
            payload["replyTo"] = reply_to
        result = self._request("POST", "/send", payload)
        message_id = (result or {}).get("messageId")
        if not message_id:
            raise RuntimeError("bridge did not return a messageId")
        return str(message_id)

    def edit(self, *, chat_id: str, message_id: str, text: str) -> None:
        self._request(
            "POST", "/edit", {"chatId": chat_id, "messageId": message_id, "message": text}
        )

    def typing(self, *, chat_id: str) -> None:
        self._request("POST", "/typing", {"chatId": chat_id})


class WhatsAppChannel:
    """Universal WhatsApp channel backed by the HTTP bridge."""

    platform = "whatsapp"
    capabilities = frozenset({"read", "send", "edit", "typing"})

    def __init__(self, client: WhatsAppBridgeClient) -> None:
        self._client = client
        self._inbox: list[ChatMessage] = []
        self._message_ids: dict[int, str] = {}

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "WhatsAppChannel":
        source = os.environ if env is None else env
        base_url = str(source.get("USERIO_WHATSAPP_BRIDGE_URL") or DEFAULT_BRIDGE_URL)
        return cls(WhatsAppBridgeClient(base_url))

    def _remember_id(self, int_id: int, bridge_id: str) -> None:
        self._message_ids[int_id] = bridge_id

    def _to_message(self, item: Mapping[str, Any]) -> ChatMessage:
        key = item.get("key") or {}
        bridge_id = str(key.get("id") or "")
        chat_jid = str(key.get("remoteJid") or "")
        int_id = _ref_id(bridge_id)
        self._remember_id(int_id, bridge_id)
        return ChatMessage(
            chat_id=chat_jid,
            id=int_id,
            text=_extract_text(item),
            sender_id=chat_jid,
            date=None,
            out=bool(key.get("fromMe")),
        )

    async def poll(self) -> list[ChatMessage]:
        """Drain the bridge queue into the local inbox buffer."""

        items = await asyncio.to_thread(self._client.drain_messages)
        incoming = [self._to_message(item) for item in items]
        self._inbox.extend(incoming)
        return incoming

    async def list_chats(self) -> list[ChatSummary]:
        await self.poll()
        chats: dict[str, ChatSummary] = {}
        for message in self._inbox:
            if message.chat_id and message.chat_id not in chats:
                chats[message.chat_id] = ChatSummary(
                    id=message.chat_id, title=message.chat_id, username=None,
                    kind="dm", unread_count=0,
                )
        return list(chats.values())

    async def read_chat(self, chat: ChatRef, limit: int | None = 100) -> list[ChatMessage]:
        chat_id = normalize_chat_id(chat)
        await self.poll()
        matching = [m for m in self._inbox if m.chat_id == chat_id]
        return matching[: limit or 100]

    async def read_message(self, chat: ChatRef, message_id: int) -> ChatMessage | None:
        chat_id = normalize_chat_id(chat)
        await self.poll()
        return next(
            (m for m in self._inbox if m.chat_id == chat_id and m.id == message_id), None
        )

    async def send_message(
        self, chat: ChatRef, text: str, *, reply_to: int | None = None
    ) -> ChatMessage:
        chat_id = normalize_chat_id(chat)
        bridge_reply_to = self._message_ids.get(reply_to) if reply_to else None
        bridge_id = await asyncio.to_thread(
            self._client.send, chat_id=chat_id, text=text, reply_to=bridge_reply_to
        )
        int_id = _ref_id(bridge_id)
        self._remember_id(int_id, bridge_id)
        return ChatMessage(chat_id=chat_id, id=int_id, text=text, sender_id=None, out=True)

    async def edit_message(self, chat: ChatRef, message: MessageRef, text: str) -> ChatMessage:
        chat_id = normalize_chat_id(chat)
        int_id = int(getattr(message, "id", message))
        bridge_id = self._message_ids.get(int_id)
        if bridge_id is None:
            raise ValueError(f"message {int_id} was not sent through this channel")
        await asyncio.to_thread(
            self._client.edit, chat_id=chat_id, message_id=bridge_id, text=text
        )
        return ChatMessage(chat_id=chat_id, id=int_id, text=text, sender_id=None, out=True)

    def typing(self, chat: ChatRef):
        chat_id = normalize_chat_id(chat)

        @asynccontextmanager
        async def _typing():
            await asyncio.to_thread(self._client.typing, chat_id=chat_id)
            yield

        return _typing()

    async def acknowledge_chat(self, chat: ChatRef) -> None:
        raise AdapterNotSupported("whatsapp channel does not support acknowledge_chat")

    async def download_media(self, chat: ChatRef, message: MessageRef):
        raise AdapterNotSupported("whatsapp bridge does not expose inbound media download")

    async def forward_message(
        self, source_chat: ChatRef, message: MessageRef, target_chat: ChatRef
    ) -> ChatMessage:
        raise AdapterNotSupported("whatsapp channel does not support forward_message")

    async def delete_message(self, chat: ChatRef, message: MessageRef) -> bool:
        raise AdapterNotSupported("whatsapp channel does not support delete_message")

    async def react(self, chat: ChatRef, message: MessageRef, emoji: str) -> None:
        raise AdapterNotSupported("whatsapp channel does not support react")
