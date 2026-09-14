"""Stable provider-neutral contracts for UserIO-compatible channel adapters.

Any project talks to chats through these small immutable data transfer
objects.  Adapters may keep their provider-specific objects internally, but
none of those objects cross the port boundary.  Spec:
docs/2026-09-03-universal-adapters-spec.md.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


ChatId = int | str
ChatRef = ChatId
MessageRef = Any
ContactRef = ChatId


class AdapterCapabilities:
    """Stable capability names shared by adapters and consumers."""

    READ = "read"
    SEND = "send"
    EDIT = "edit"
    DELETE = "delete"
    MEDIA = "media"
    TYPING = "typing"
    REACT = "react"
    FORWARD = "forward"
    ACK = "ack"
    DOWNLOAD = "download"
    UPLOAD = "upload"
    GET_CONTACT = "get_contact"
    ADD_CONTACT = "add_contact"
    EDIT_CONTACT = "edit_contact"
    REMOVE_CONTACT = "remove_contact"
    ADD_CONTACT_TO_GROUP = "add_contact_to_group"
    REMOVE_CONTACT_FROM_GROUP = "remove_contact_from_group"

    ALL = frozenset(
        {
            READ,
            SEND,
            EDIT,
            DELETE,
            MEDIA,
            TYPING,
            REACT,
            FORWARD,
            ACK,
            DOWNLOAD,
            UPLOAD,
            GET_CONTACT,
            ADD_CONTACT,
            EDIT_CONTACT,
            REMOVE_CONTACT,
            ADD_CONTACT_TO_GROUP,
            REMOVE_CONTACT_FROM_GROUP,
        }
    )


def stable_ref_id(message_id: str) -> int:
    """Map an opaque provider message id to the stable int ``ChatMessage.id``."""

    return int(hashlib.sha256(str(message_id).encode()).hexdigest()[:12], 16)


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


@dataclass(frozen=True, slots=True)
class Contact:
    """Provider-neutral address-book contact.

    ``id`` may be absent before :meth:`ChatPort.add_contact`; providers return
    a populated id when they expose one. No provider SDK object crosses this
    boundary.
    """

    id: ContactRef | None = None
    display_name: str = ""
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    phone: str | None = None
    email: str | None = None
    is_contact: bool | None = None


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
class FilePort(Protocol):
    """Optional file operations advertised by ``download``/``upload`` caps."""

    async def download(
        self, chat: ChatRef, message: MessageRef
    ) -> DownloadedMedia: ...

    async def upload(
        self,
        chat: ChatRef,
        data: bytes,
        *,
        filename: str,
        mime_type: str | None = None,
        caption: str | None = None,
    ) -> ChatMessage: ...


@runtime_checkable
class ContactPort(Protocol):
    """Optional provider address-book operations advertised by contact caps."""

    async def get_contact(self, contact: ContactRef) -> Contact | None: ...

    async def add_contact(self, contact: Contact) -> Contact: ...

    async def edit_contact(
        self,
        contact: ContactRef,
        *,
        display_name: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        username: str | None = None,
        phone: str | None = None,
        email: str | None = None,
    ) -> Contact: ...

    async def remove_contact(self, contact: ContactRef) -> bool: ...


@runtime_checkable
class GroupContactPort(Protocol):
    """Optional operations for managing contact membership in provider groups."""

    async def add_contact_to_group(
        self, contact: ContactRef, group: ChatRef
    ) -> bool: ...

    async def remove_contact_from_group(
        self, contact: ContactRef, group: ChatRef
    ) -> bool: ...


@runtime_checkable
class Channel(ChatPort, Protocol):
    """A ChatPort bound to one platform with declared capabilities.

    ``platform`` is the lowercase channel name ("telegram", "email", ...);
    ``capabilities`` lists supported operations out of
    :class:`AdapterCapabilities`. Legacy ``media`` remains distinct from the
    explicit ``download`` and ``upload`` operations.
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
    "ContactRef",
    "AdapterCapabilities",
    "stable_ref_id",
    "ChatOperationError",
    "ChatPermissionError",
    "ChatInvalidPeerError",
    "ChatRateLimitError",
    "AdapterNotSupported",
    "ChatSummary",
    "ChatMessage",
    "DownloadedMedia",
    "Contact",
    "ChatPort",
    "FilePort",
    "ContactPort",
    "GroupContactPort",
    "Channel",
    "mapping_value",
]

# Imported last to avoid a cycle while ``omnichannel`` types against the
# protocol and DTOs defined above.
from .omnichannel import ChannelBinding, Omnichannel

__all__ += ["ChannelBinding", "Omnichannel"]
