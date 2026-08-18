"""Stable boundaries between canonical ingress, AI, and the durable Outbox."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class InboxMessage:
    source: str
    message_id: str
    sender: str
    body: str
    received_at: float

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


class DraftGenerator(Protocol):
    def suggest(self, *, conversation_id: str, latest_message: InboxMessage) -> str: ...


class OutboxClient(Protocol):
    def send_reply(self, *, route_id: str, conversation_id: str, draft_id: str, body: str) -> str: ...
