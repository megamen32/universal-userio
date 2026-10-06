"""MAX channel adapter over the OneMe WebSocket client (spec Phase 5 pattern).

Like the SMS channel, this wraps the low-level transport
(:class:`~universal_userio.channels.max_client.MaxClient`) in the universal
Channel shape.  ``read`` lists chats and history through the logged-in MAX
session; ``send`` delivers an already-approved text through MSG_SEND (opcode
64).  The client reports server acceptance, not the peer's device delivery.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from datetime import datetime, timezone

from userio_adapter_sdk import (
    AdapterNotSupported,
    ChatMessage,
    ChatRef,
    ChatSummary,
    MessageRef,
    stable_ref_id,
)


def _peer(chat: ChatRef) -> str:
    return str(getattr(chat, "id", chat))


def _kind(raw: str) -> str:
    raw = str(raw or "").upper()
    if raw.startswith("GROUP"):
        return "group"
    if raw.startswith("CHANNEL"):
        return "channel"
    return "dm"


class MaxChannel:
    """Universal MAX channel backed by one logged-in MaxClient session."""

    platform = "max"
    capabilities = frozenset({"read", "send"})

    def __init__(self, client: object) -> None:
        self._client = client

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "MaxChannel":
        from universal_userio.channels.max_client import MaxClient

        source = os.environ if env is None else env
        token = str(source.get("USERIO_MAX_TOKEN") or "").strip()
        device_id = str(source.get("USERIO_MAX_DEVICE_ID") or "").strip()
        if not token:
            raise ValueError("USERIO_MAX_TOKEN is required for the max channel")
        socks_host, socks_port = "", 0
        socks = str(source.get("USERIO_MAX_SOCKS") or "").strip()
        if socks:
            host, _, port = socks.rpartition(":")
            socks_host, socks_port = host, int(port or 1080)
        return cls(MaxClient(token, device_id, socks_host=socks_host, socks_port=socks_port))

    def _to_message(self, chat_id: str, row: Mapping[str, object]) -> ChatMessage:
        message_id = str(row.get("id") or "")
        sender = row.get("sender")
        time_ms = row.get("time")
        date = (
            datetime.fromtimestamp(int(time_ms) / 1000, tz=timezone.utc) if time_ms else None
        )
        return ChatMessage(
            chat_id=str(chat_id),
            id=stable_ref_id(message_id),
            text=str(row.get("text") or ""),
            sender_id="" if sender is None else str(sender),
            date=date,
            out=False,
        )

    async def list_chats(self) -> list[ChatSummary]:
        rows = await asyncio.to_thread(self._client.chats)
        summaries: list[ChatSummary] = []
        for row in rows:
            chat_id = str(row.get("id") or "")
            if not chat_id:
                continue
            summaries.append(
                ChatSummary(
                    id=chat_id,
                    title=str(row.get("title") or chat_id),
                    username=None,
                    kind=_kind(row.get("type")),
                    unread_count=int(row.get("newMessages") or 0),
                )
            )
        return summaries

    async def read_chat(self, chat: ChatRef, limit: int | None = 100) -> list[ChatMessage]:
        peer = _peer(chat)
        rows = await asyncio.to_thread(self._client.history, peer, backward=limit or 100)
        return [self._to_message(peer, row) for row in rows][: limit or 100]

    async def read_message(self, chat: ChatRef, message_id: int) -> ChatMessage | None:
        peer = _peer(chat)
        rows = await asyncio.to_thread(self._client.history, peer, backward=100)
        for row in rows:
            if stable_ref_id(str(row.get("id") or "")) == message_id:
                return self._to_message(peer, row)
        return None

    async def send_message(
        self, chat: ChatRef, text: str, *, reply_to: int | None = None
    ) -> ChatMessage:
        peer = _peer(chat)
        receipt = await asyncio.to_thread(self._client.send, chat_id=peer, text=text)
        return ChatMessage(
            chat_id=peer,
            id=stable_ref_id(str(receipt.get("message_id") or "")),
            text=text,
            sender_id=None,
            out=True,
        )

    async def acknowledge_chat(self, chat: ChatRef) -> None:
        raise AdapterNotSupported("max channel does not support acknowledge_chat")

    async def download_media(self, chat: ChatRef, message: MessageRef):
        raise AdapterNotSupported("max channel does not support media download yet")

    async def forward_message(
        self, source_chat: ChatRef, message: MessageRef, target_chat: ChatRef
    ) -> ChatMessage:
        raise AdapterNotSupported("max channel does not support forward_message")

    async def delete_message(self, chat: ChatRef, message: MessageRef) -> bool:
        raise AdapterNotSupported("max channel does not support delete_message")

    async def edit_message(self, chat: ChatRef, message: MessageRef, text: str) -> ChatMessage:
        raise AdapterNotSupported("max channel does not support edit_message")

    async def react(self, chat: ChatRef, message: MessageRef, emoji: str) -> None:
        raise AdapterNotSupported("max channel does not support react")

    def typing(self, chat: ChatRef):
        raise AdapterNotSupported("max channel does not support typing")
