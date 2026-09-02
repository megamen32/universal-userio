"""Provider-neutral Telegram identities for the universal channels library."""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from universal_userio.channels.core import ChatMessage, ChatRef, MessageRef


def _normalize_username(value: str | None) -> str:
    """Normalize a Telegram username without importing target models."""

    if value is None:
        return ""
    value = value.strip().lower()
    for prefix in ("https://t.me/", "http://t.me/"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    return value.lstrip("@")


class TelegramParticipantRef(BaseModel):
    """The user discovered by the parser in a source group."""

    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(gt=0)
    username: str | None = None
    access_hash: int | None = None

    @model_validator(mode="after")
    def normalize(self) -> "TelegramParticipantRef":
        if self.username:
            self.username = _normalize_username(self.username) or None
        return self


class TelegramSourceGroup(BaseModel):
    """The group/channel whose membership makes the participant resolvable."""

    model_config = ConfigDict(extra="forbid")

    chat_id: int
    username: str | None = None
    access_hash: int | None = None
    public_link: str | None = None
    invite_link: str | None = None
    membership_required: bool = True

    @model_validator(mode="after")
    def normalize(self) -> "TelegramSourceGroup":
        if self.username:
            self.username = _normalize_username(self.username) or None
        if not self.public_link and self.username:
            self.public_link = f"https://t.me/{self.username}"
        return self


class TelegramTargetContext(BaseModel):
    """Complete parser-to-writer handoff for a Telegram participant."""

    model_config = ConfigDict(extra="forbid")

    parser_account_id: int = Field(gt=0)
    writer_account_id: int | None = Field(default=None, gt=0)
    participant: TelegramParticipantRef
    source_group: TelegramSourceGroup


class TelegramAccountRef(BaseModel):
    """Provider-neutral identity of the account performing an operation."""

    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(gt=0)
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None

    @model_validator(mode="after")
    def normalize(self) -> "TelegramAccountRef":
        if self.username:
            self.username = _normalize_username(self.username) or None
        return self


class TelegramProfileRef(TelegramParticipantRef):
    """Participant identity plus the profile fields needed by prompts."""

    first_name: str | None = None
    last_name: str | None = None
    about: str | None = None


class MembershipStatus(str, Enum):
    """Provider-neutral result of a channel membership operation."""

    JOINED = "joined"
    REQUESTED = "requested"
    BANNED = "banned"
    NOT_SUBSCRIBED = "not_subscribed"


class MembershipResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str
    status: MembershipStatus


@runtime_checkable
class TelegramIdentityPort(Protocol):
    """Identity, permission and membership operations for Telegram flows."""

    async def get_account(self) -> TelegramAccountRef: ...

    async def get_profile(
        self, participant: TelegramParticipantRef | str | int
    ) -> TelegramProfileRef | None: ...

    async def ensure_membership(self, source_group: TelegramSourceGroup) -> bool: ...

    async def resolve_participant(
        self,
        participant: TelegramParticipantRef,
        *,
        source_group: TelegramSourceGroup | None = None,
        message_link: str | None = None,
    ) -> TelegramProfileRef | None: ...

    async def can_message(
        self, peer: Any
    ) -> tuple[bool, str | None]: ...

    async def inspect_membership(self, channel: str) -> MembershipResult: ...

    async def request_membership(self, channel: str) -> MembershipResult: ...

    async def join_channel(self, link: str) -> bool: ...


@runtime_checkable
class TelegramModerationPort(Protocol):
    """Interactive button operations needed by the SpamBot state machine."""

    async def click_button(
        self,
        chat: ChatRef,
        message: MessageRef,
        row: int,
        column: int,
    ) -> ChatMessage | None: ...


__all__ = [
    "TelegramParticipantRef",
    "TelegramSourceGroup",
    "TelegramTargetContext",
    "TelegramAccountRef",
    "TelegramProfileRef",
    "MembershipStatus",
    "MembershipResult",
    "TelegramIdentityPort",
    "TelegramModerationPort",
]
