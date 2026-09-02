"""SMS channel adapter over the Android SMS Gateway (spec Phase 5).

Wraps the existing :class:`~universal_userio.adapters.AndroidSmsGatewayClient`
(inbound ``GET /v1/inbound``, send ``POST /v1/messages``) in the universal
Channel shape.  The gateway reports command acceptance by Android, not carrier
delivery.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping

from universal_userio.channels.core import (
    AdapterNotSupported,
    ChatMessage,
    ChatRef,
    ChatSummary,
    MessageRef,
    stable_ref_id,
)


def _peer(chat: ChatRef) -> str:
    return str(getattr(chat, "id", chat)).lstrip("+")


def _row_sender(row) -> str:
    return str(getattr(row, "sender", "") or "").lstrip("+")


class AndroidSmsChannel:
    """Universal SMS channel backed by one Android SMS Gateway instance."""

    platform = "sms"
    capabilities = frozenset({"read", "send"})

    def __init__(self, client: object) -> None:
        self._client = client

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AndroidSmsChannel":
        from universal_userio.adapters import AndroidSmsGatewayClient

        source = os.environ if env is None else env
        url = str(source.get("USERIO_SMS_GATEWAY_URL") or "").strip()
        token = str(source.get("USERIO_SMS_GATEWAY_TOKEN") or "").strip()
        if not url or not token:
            raise ValueError(
                "SMS env config incomplete; set USERIO_SMS_GATEWAY_URL and USERIO_SMS_GATEWAY_TOKEN"
            )
        return cls(AndroidSmsGatewayClient(url, token))

    def _to_message(self, row) -> ChatMessage:
        message_id = str(getattr(row, "message_id", "") or "")
        sender = _row_sender(row)
        received_at = float(getattr(row, "received_at", 0.0) or 0.0)
        from datetime import datetime, timezone

        date = (
            datetime.fromtimestamp(received_at, tz=timezone.utc) if received_at else None
        )
        return ChatMessage(
            chat_id=sender,
            id=stable_ref_id(message_id),
            text=str(getattr(row, "body", "") or ""),
            sender_id=sender,
            date=date,
            out=False,
        )

    async def list_chats(self) -> list[ChatSummary]:
        rows = await asyncio.to_thread(self._client.inbound)
        chats: dict[str, ChatSummary] = {}
        for row in rows:
            sender = _row_sender(row)
            if sender and sender not in chats:
                chats[sender] = ChatSummary(
                    id=sender, title=sender, username=None, kind="dm", unread_count=0
                )
        return list(chats.values())

    async def read_chat(self, chat: ChatRef, limit: int | None = 100) -> list[ChatMessage]:
        peer = _peer(chat)
        rows = await asyncio.to_thread(self._client.inbound)
        matching = [row for row in rows if _row_sender(row) == peer]
        return [self._to_message(row) for row in matching][: limit or 100]

    async def read_message(self, chat: ChatRef, message_id: int) -> ChatMessage | None:
        peer = _peer(chat)
        rows = await asyncio.to_thread(self._client.inbound)
        for row in rows:
            if _row_sender(row) != peer:
                continue
            if stable_ref_id(str(getattr(row, "message_id", "") or "")) == message_id:
                return self._to_message(row)
        return None

    async def send_message(
        self, chat: ChatRef, text: str, *, reply_to: int | None = None
    ) -> ChatMessage:
        peer = _peer(chat)
        receipt = await asyncio.to_thread(self._client.send, to=peer, body=text)
        return ChatMessage(
            chat_id=peer, id=stable_ref_id(str(receipt)), text=text, sender_id=None, out=True
        )

    async def acknowledge_chat(self, chat: ChatRef) -> None:
        raise AdapterNotSupported("sms channel does not support acknowledge_chat")

    async def download_media(self, chat: ChatRef, message: MessageRef):
        raise AdapterNotSupported("sms channel does not support media download")

    async def forward_message(
        self, source_chat: ChatRef, message: MessageRef, target_chat: ChatRef
    ) -> ChatMessage:
        raise AdapterNotSupported("sms channel does not support forward_message")

    async def delete_message(self, chat: ChatRef, message: MessageRef) -> bool:
        raise AdapterNotSupported("sms channel does not support delete_message")

    async def edit_message(self, chat: ChatRef, message: MessageRef, text: str) -> ChatMessage:
        raise AdapterNotSupported("sms channel does not support edit_message")

    async def react(self, chat: ChatRef, message: MessageRef, emoji: str) -> None:
        raise AdapterNotSupported("sms channel does not support react")

    def typing(self, chat: ChatRef):
        raise AdapterNotSupported("sms channel does not support typing")
