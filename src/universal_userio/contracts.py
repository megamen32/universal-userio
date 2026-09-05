"""Stable boundaries between canonical ingress, AI, and the durable Outbox."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class InboxMessage:
    source: str
    message_id: str
    sender: str
    body: str
    received_at: float
    sender_name: str = ""
    attachments: tuple[dict[str, Any], ...] = ()

    @property
    def conversation_key(self) -> str:
        if self.source == "email" or self.source == "gmail" or self.source.startswith("gmail:"):
            return "email:" + self.sender.strip().casefold()
        return f"{self.source}:{self.sender}"


@dataclass(frozen=True, slots=True)
class ReplyDraft:
    id: str
    conversation_id: str
    body: str
    status: str


@dataclass(frozen=True, slots=True)
class ConversationPolicy:
    route_id: str
    mode: str = "approve"
    identity_id: str | None = None


@dataclass(frozen=True, slots=True)
class UserPrincipal:
    user_id: str
    username: str
    role: str
    service_account: bool = False


@dataclass(frozen=True, slots=True)
class ChannelFile:
    filename: str
    content_type: str
    data: bytes


class ChannelAdapter(Protocol):
    """One user-bound channel, independent of provider transport details."""

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]: ...

    def read(
        self, *, chat_id: str | None = None, message_id: str | None = None
    ) -> dict[str, Any]: ...

    def download(self, *, file_ref: str) -> ChannelFile: ...

    def send(
        self, *, chat_id: str, text: str, attachments: list[str] | None = None
    ) -> ReplyDraft: ...


class DraftGenerator(Protocol):
    def suggest(self, *, conversation_id: str, latest_message: InboxMessage) -> str: ...


class OutboxClient(Protocol):
    def send_reply(self, *, route_id: str, conversation_id: str, draft_id: str, body: str) -> str: ...
