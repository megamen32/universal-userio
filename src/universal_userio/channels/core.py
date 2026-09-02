"""Provider-neutral asynchronous chat contracts for the universal-userio channels.

Any project talks to chats through these small immutable data transfer
objects.  Adapters may keep their provider-specific objects internally, but
none of those objects cross the port boundary.  Spec:
docs/2026-09-03-universal-adapters-spec.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


ChatId = int | str
ChatRef = ChatId
MessageRef = Any


class ChatOperationError(Exception):
    """Provider-neutral base error for a chat operation."""

    def __init__(self, message: str = "chat operation failed") -> None:
        super().__init__(message)
        self.message = message


class ChatPermissionError(ChatOperationError):
    """The provider rejected writing to a peer."""


class ChatInvalidPeerError(ChatOperationError):
    """The peer cannot be resolved by the provider."""


class ChatRateLimitError(ChatOperationError):
    """The provider asked the caller to slow down."""


class AdapterNotSupported(ValueError):
    """The adapter platform does not support the requested operation."""


@dataclass(frozen=True, slots=True, init=False)
class ChatSummary:
    """A provider-independent chat listing entry."""

    id: ChatId
    title: str
    username: str | None
    kind: str
    unread_count: int

    def __init__(
        self,
        id: ChatId | None = None,
        title: str = "",
        username: str | None = None,
        kind: str = "unknown",
        unread_count: int = 0,
        *,
        chat_id: ChatId | None = None,
        name: str | None = None,
    ) -> None:
        if id is None:
            id = chat_id
        if id is None:
            raise TypeError("ChatSummary requires id or chat_id")
        if not title and name is not None:
            title = name
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "username", username)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "unread_count", max(0, int(unread_count or 0)))

    @property
    def chat_id(self) -> ChatId:
        """Alias useful to callers that use ``chat_id`` consistently."""

        return self.id

    @property
    def name(self) -> str:
        return self.title


@dataclass(frozen=True, slots=True, init=False)
class ChatMessage:
    """A provider-independent chat message."""

    chat_id: ChatId
    id: int
    text: str
    sender_id: ChatId | None
    date: datetime | None
    media_type: str | None
    filename: str | None
    caption: str | None
    buttons: tuple[tuple[str, ...], ...]
    out: bool
    reply_to_msg_id: int | None

    def __init__(
        self,
        chat_id: ChatId | None = None,
        id: int | None = None,
        text: str = "",
        sender_id: ChatId | None = None,
        date: datetime | None = None,
        media_type: str | None = None,
        filename: str | None = None,
        caption: str | None = None,
        buttons: tuple[tuple[str, ...], ...] | list[list[str]] | None = None,
        out: bool = False,
        reply_to_msg_id: int | None = None,
        *,
        chat: ChatId | None = None,
        message_id: int | None = None,
        message: str | None = None,
    ) -> None:
        if chat_id is None:
            chat_id = chat
        if chat_id is None:
            raise TypeError("ChatMessage requires chat_id or chat")
        if id is None:
            id = message_id
        if id is None:
            raise TypeError("ChatMessage requires id or message_id")
        if not text and message is not None:
            text = message
        object.__setattr__(self, "chat_id", chat_id)
        object.__setattr__(self, "id", int(id))
        object.__setattr__(self, "text", text or "")
        object.__setattr__(self, "sender_id", sender_id)
        object.__setattr__(self, "date", date)
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(self, "filename", filename)
        object.__setattr__(self, "caption", caption)
        object.__setattr__(
            self,
            "buttons",
            tuple(tuple(str(text) for text in row) for row in (buttons or ())),
        )
        object.__setattr__(self, "out", bool(out))
        object.__setattr__(self, "reply_to_msg_id", reply_to_msg_id)

    @property
    def message_id(self) -> int:
        return self.id

    @property
    def message(self) -> str:
        return self.text


@dataclass(frozen=True, slots=True, init=False)
class DownloadedMedia:
    """Bytes downloaded for one message's media attachment."""

    chat_id: ChatId
    message_id: int
    data: bytes
    mime_type: str | None
    filename: str | None

    def __init__(
        self,
        chat_id: ChatId | None = None,
        message_id: int | None = None,
        data: bytes = b"",
        mime_type: str | None = None,
        filename: str | None = None,
        *,
        chat: ChatId | None = None,
        content: bytes | None = None,
        media_type: str | None = None,
    ) -> None:
        if chat_id is None:
            chat_id = chat
        if chat_id is None:
            raise TypeError("DownloadedMedia requires chat_id or chat")
        if message_id is None:
            raise TypeError("DownloadedMedia requires message_id")
        if content is not None:
            data = content
        if media_type is not None and mime_type is None:
            mime_type = media_type
        object.__setattr__(self, "chat_id", chat_id)
        object.__setattr__(self, "message_id", int(message_id))
        object.__setattr__(self, "data", bytes(data))
        object.__setattr__(self, "mime_type", mime_type)
        object.__setattr__(self, "filename", filename)

    @property
    def content(self) -> bytes:
        return self.data

    @property
    def bytes(self) -> bytes:
        return self.data

    @property
    def media_type(self) -> str | None:
        return self.mime_type


@runtime_checkable
class ChatPort(Protocol):
    """Async chat operations required by the dialogue core."""

    async def list_chats(self) -> list[ChatSummary]: ...

    async def read_chat(
        self, chat: ChatRef, limit: int | None = 100
    ) -> list[ChatMessage]: ...

    async def read_message(
        self, chat: ChatRef, message_id: int
    ) -> ChatMessage | None: ...

    async def acknowledge_chat(self, chat: ChatRef) -> None: ...

    async def download_media(
        self, chat: ChatRef, message: MessageRef
    ) -> DownloadedMedia: ...

    async def send_message(
        self, chat: ChatRef, text: str, *, reply_to: int | None = None
    ) -> ChatMessage: ...

    async def forward_message(
        self, source_chat: ChatRef, message: MessageRef, target_chat: ChatRef
    ) -> ChatMessage: ...

    async def delete_message(self, chat: ChatRef, message: MessageRef) -> bool: ...

    async def edit_message(
        self, chat: ChatRef, message: MessageRef, text: str
    ) -> ChatMessage: ...

    async def react(
        self, chat: ChatRef, message: MessageRef, emoji: str
    ) -> None: ...

    def typing(self, chat: ChatRef) -> AbstractAsyncContextManager[None]: ...


@runtime_checkable
class Channel(ChatPort, Protocol):
    """A ChatPort bound to one platform with declared capabilities.

    ``platform`` is the lowercase channel name ("telegram", "email", ...);
    ``capabilities`` lists supported operations out of
    ``read, send, edit, delete, media, typing, react, forward, ack``.
    Operations outside ``capabilities`` raise :class:`AdapterNotSupported`.
    """

    platform: str
    capabilities: frozenset[str]


def mapping_value(value: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    """Return the first present key from a provider mapping."""

    for name in names:
        if name in value:
            return value[name]
    return default


__all__ = [
    "ChatId",
    "ChatRef",
    "MessageRef",
    "ChatOperationError",
    "ChatPermissionError",
    "ChatInvalidPeerError",
    "ChatRateLimitError",
    "AdapterNotSupported",
    "ChatSummary",
    "ChatMessage",
    "DownloadedMedia",
    "ChatPort",
    "Channel",
]
