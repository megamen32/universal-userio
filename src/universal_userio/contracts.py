"""Stable boundaries between canonical ingress, AI, and the durable Outbox."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class DeliveryNotSubmittedError(RuntimeError):
    """An adapter proves that failure occurred before provider submission."""


class ProviderAuthenticationRequiredError(RuntimeError):
    """A known provider login refusal, distinct from an uncertain mutation."""

    def __init__(self, provider: str, *, provider_code: str = '') -> None:
        self.provider = provider
        self.provider_code = provider_code
        label = 'MAX' if provider == 'max' else 'мессенджера'
        self.public_message = f'Авторизация {label} больше не принимается. Переподключите существующий аккаунт. История и уже отправленные сообщения сохранены.'
        super().__init__('provider_authentication_required' + (f' ({provider_code})' if provider_code else ''))


class ProviderAuthenticationNotSubmittedError(ProviderAuthenticationRequiredError, DeliveryNotSubmittedError):
    """Provider auth failed while verifying source, before any mutation."""


@dataclass(frozen=True, slots=True)
class InboxMessage:
    source: str
    message_id: str
    sender: str
    body: str
    received_at: float
    sender_name: str = ""
    attachments: tuple[dict[str, Any], ...] = ()
    direction: str = "incoming"
    conversation_kind: str = ""
    peer_id: str = ""
    reconciliation: bool = False
    sender_is_bot: bool = False
    # Provider-side edit timestamp (unix seconds). Zero means the sender never
    # reported an edit; re-delivery of an unchanged message must not set it.
    edited_at: float = 0.0
    # Provider read state is distinct from opening the local UserIO view.
    provider_read: bool | None = None
    conversation_title: str = ""
    outbox_receipt: str = ""
    provider_reply_to: str = ""
    provider_thread_id: str = ""

    @property
    def conversation_key(self) -> str:
        if self.source.startswith("email:"):
            # IMAP conversations are private to one linked mailbox.
            return self.source + ":" + self.sender.strip().casefold()
        if self.source == "email" or self.source == "gmail" or self.source.startswith("gmail:"):
            return "email:" + self.sender.strip().casefold()
        if self.source.startswith("matrix:") and self.peer_id:
            # Room identity, not the latest author, identifies a Matrix chat.
            return self.source + ":" + self.peer_id
        return f"{self.source}:{self.sender}"


@dataclass(frozen=True, slots=True)
class ReplyDraft:
    id: str
    conversation_id: str
    body: str
    status: str
    receipt: str = ""


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
    read_only: bool = False
    # None preserves existing credentials. Scoped app credentials use only
    # generic messenger tools and these explicitly assigned accounts.
    messenger_accounts: tuple[str, ...] | None = None
    meeting_recorder: bool = False


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
