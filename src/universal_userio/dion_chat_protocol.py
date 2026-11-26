"""Provider-neutral DION personal-chat protocol seam.

This module intentionally contains no guessed URL or browser scraping.  The
private chat transport is injected by the eventual adapter; this file only
defines the durable identity/cursor/message contract UserIO will persist.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


class DionChatProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class DionChatMessage:
    provider_message_id: str
    chat_id: str
    sender_id: str
    body: str
    timestamp_ms: int
    direction: str


@dataclass(frozen=True)
class DionChatPage:
    messages: tuple[DionChatMessage, ...]
    next_cursor: str | None


class DionChatTransport(Protocol):
    def list_messages(self, chat_id: str, cursor: str | None) -> Mapping[str, Any]: ...


def parse_message(raw: Mapping[str, Any], *, own_user_id: str) -> DionChatMessage:
    """Parse only immutable provider IDs; display names are never identities."""
    message_id = str(raw.get("id") or raw.get("message_id") or "").strip()
    chat_id = str(raw.get("chat_id") or raw.get("conversation_id") or "").strip()
    sender_id = str(raw.get("sender_id") or raw.get("user_id") or "").strip()
    body = raw.get("body") if raw.get("body") is not None else raw.get("text")
    try:
        timestamp_ms = int(raw.get("timestamp_ms") or raw.get("timestamp") or 0)
    except (TypeError, ValueError) as exc:
        raise DionChatProtocolError("invalid message timestamp") from exc
    if not message_id or not chat_id or not sender_id or not isinstance(body, str):
        raise DionChatProtocolError("message lacks immutable provider fields")
    if timestamp_ms < 0 or not own_user_id:
        raise DionChatProtocolError("invalid message identity")
    return DionChatMessage(message_id, chat_id, sender_id, body, timestamp_ms,
                           "out" if sender_id == own_user_id else "in")


def parse_page(payload: Mapping[str, Any], *, chat_id: str, own_user_id: str) -> DionChatPage:
    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, Sequence) or isinstance(raw_messages, (str, bytes)):
        raise DionChatProtocolError("chat response has no message sequence")
    messages = tuple(parse_message({**item, "chat_id": chat_id}, own_user_id=own_user_id)
                     for item in raw_messages if isinstance(item, Mapping))
    cursor = payload.get("next_cursor")
    if cursor is not None and not isinstance(cursor, str):
        raise DionChatProtocolError("invalid chat cursor")
    return DionChatPage(messages, cursor or None)


def sync_once(transport: DionChatTransport, *, chat_id: str, own_user_id: str,
              cursor: str | None = None) -> DionChatPage:
    if not chat_id or not own_user_id:
        raise DionChatProtocolError("chat and owner identity are required")
    return parse_page(transport.list_messages(chat_id, cursor), chat_id=chat_id,
                      own_user_id=own_user_id)
