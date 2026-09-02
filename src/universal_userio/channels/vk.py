"""VK channel adapter over the userio browser-worker pipeline (spec Phase 5).

VK has no server-side messaging API in this ecosystem: the vk-inbox browser
extension captures inbound messages into the UserIO store and performs the
actual MAIN-world send for approved drafts.  This adapter therefore wires two
injected callables:

- ``reader()`` returns captured inbound rows (in the service composition this
  is the stored VK channel view);
- ``sender(chat_id, text)`` delivers through whatever pipeline owns the
  extension (e.g. manual draft + approve on a UserIO service instance).

Without a ``sender`` the channel still reads, and send raises
:class:`AdapterNotSupported` instead of pretending.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from universal_userio.channels.core import (
    AdapterNotSupported,
    ChatMessage,
    ChatRef,
    ChatSummary,
    DownloadedMedia,
    MessageRef,
    stable_ref_id,
)

Row = dict[str, Any]
Sender = Callable[[str, str], str]


def _peer(chat: ChatRef) -> str:
    return str(getattr(chat, "id", chat))


class VkChannel:
    """Universal VK channel backed by injected reader/sender callables."""

    platform = "vk"
    capabilities = frozenset({"read", "send"})

    def __init__(self, reader: Callable[[], list[Row]], sender: Sender | None = None) -> None:
        self._reader = reader
        self._sender = sender

    def _to_message(self, row: Row) -> ChatMessage:
        message_id = str(row.get("message_id") or row.get("id") or "")
        sender = str(row.get("sender") or row.get("from") or "")
        attachments = row.get("attachments") or []
        first = attachments[0] if attachments else None
        return ChatMessage(
            chat_id=sender,
            id=stable_ref_id(message_id),
            text=str(row.get("body") or row.get("text") or ""),
            sender_id=sender,
            date=row.get("date"),
            media_type=first.get("content_type") if first else None,
            filename=first.get("filename") if first else None,
            out=bool(row.get("out")),
        )

    async def list_chats(self) -> list[ChatSummary]:
        rows = await asyncio.to_thread(self._reader)
        chats: dict[str, ChatSummary] = {}
        for row in rows:
            sender = str(row.get("sender") or row.get("from") or "")
            if sender and sender not in chats:
                title = str(row.get("sender_name") or sender)
                chats[sender] = ChatSummary(
                    id=sender, title=title, username=None, kind="dm", unread_count=0
                )
        return list(chats.values())

    async def read_chat(self, chat: ChatRef, limit: int | None = 100) -> list[ChatMessage]:
        peer = _peer(chat)
        rows = await asyncio.to_thread(self._reader)
        matching = [row for row in rows if str(row.get("sender") or row.get("from") or "") == peer]
        return [self._to_message(row) for row in matching][: limit or 100]

    async def read_message(self, chat: ChatRef, message_id: int) -> ChatMessage | None:
        peer = _peer(chat)
        rows = await asyncio.to_thread(self._reader)
        for row in rows:
            if str(row.get("sender") or row.get("from") or "") != peer:
                continue
            if stable_ref_id(str(row.get("message_id") or row.get("id") or "")) == message_id:
                return self._to_message(row)
        return None

    async def send_message(
        self, chat: ChatRef, text: str, *, reply_to: int | None = None
    ) -> ChatMessage:
        if self._sender is None:
            raise AdapterNotSupported(
                "VK delivery is owned by the browser extension; configure a sender"
                " wired to your UserIO service draft→approve pipeline"
            )
        peer = _peer(chat)
        receipt = await asyncio.to_thread(self._sender, peer, text)
        return ChatMessage(
            chat_id=peer,
            id=stable_ref_id(str(receipt)),
            text=text,
            sender_id=None,
            out=True,
        )

    async def download_media(self, chat: ChatRef, message: MessageRef) -> DownloadedMedia:
        raise AdapterNotSupported("VK media download runs in the browser extension")

    async def acknowledge_chat(self, chat: ChatRef) -> None:
        raise AdapterNotSupported("vk channel does not support acknowledge_chat")

    async def forward_message(
        self, source_chat: ChatRef, message: MessageRef, target_chat: ChatRef
    ) -> ChatMessage:
        raise AdapterNotSupported("vk channel does not support forward_message")

    async def delete_message(self, chat: ChatRef, message: MessageRef) -> bool:
        raise AdapterNotSupported("vk channel does not support delete_message")

    async def edit_message(self, chat: ChatRef, message: MessageRef, text: str) -> ChatMessage:
        raise AdapterNotSupported("vk channel does not support edit_message")

    async def react(self, chat: ChatRef, message: MessageRef, emoji: str) -> None:
        raise AdapterNotSupported("vk channel does not support react")

    def typing(self, chat: ChatRef):
        raise AdapterNotSupported("vk channel does not support typing")
