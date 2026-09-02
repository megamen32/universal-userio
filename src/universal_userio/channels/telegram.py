"""Telegram channel adapter: OpenTele2 chat operations and Telegram resolution."""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Tuple
from urllib.parse import parse_qs, urlparse

try:  # opentele2 owns the compatible Telethon runtime when installed
    from opentele2.tl import TelegramClient
except ImportError:  # plain Telethon fallback for standalone library use
    from telethon import TelegramClient

from telethon import functions, types, errors
from telethon.errors import (
    ForbiddenError,
    PeerFloodError,
    PeerIdInvalidError,
    UserAlreadyParticipantError,
)
from telethon.errors.rpcerrorlist import UserIsBlockedError, YouBlockedUserError
from telethon.tl.functions.channels import GetParticipantRequest, JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import InputPeerUser

from universal_userio.channels.core import (
    ChatInvalidPeerError,
    ChatMessage,
    ChatPermissionError,
    ChatPort,
    ChatRateLimitError,
    ChatRef,
    ChatSummary,
    DownloadedMedia,
)
from universal_userio.channels.telegram_contracts import (
    MembershipResult,
    MembershipStatus,
    TelegramAccountRef,
    TelegramIdentityPort,
    TelegramModerationPort,
    TelegramParticipantRef,
    TelegramProfileRef,
    TelegramSourceGroup,
)

logger = logging.getLogger(__name__)


def parse_msg_url(url: str) -> Tuple[str | int, int]:
    """Return ``(chat, message_id)`` parsed from a Telegram message link."""
    url = url.rstrip("/")
    if "/c/" in url:
        parts = url.rsplit("/", 2)
        chat_id = int("-100" + parts[-2])
        msg_id = int(parts[-1])
        return chat_id, msg_id
    parts = url.rsplit("/", 2)
    chat = parts[-2].lstrip("@")
    msg_id = int(parts[-1])
    return chat, msg_id


class TelegramAPI(ChatPort, TelegramIdentityPort, TelegramModerationPort):
    """Wrapper around :class:`TelegramClient` with utility helpers."""

    platform = "telegram"
    capabilities = frozenset(
        {"read", "send", "edit", "delete", "media", "typing", "react", "forward", "ack"}
    )

    def __init__(self, client: TelegramClient) -> None:
        self.client = client

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "TelegramAPI":
        """Build an adapter from ``USERIO_TELEGRAM_*``/``TG_*`` environment settings."""

        config = telegram_env_config(env)
        return cls(
            TelegramClient(
                config.session,
                config.api_id,
                config.api_hash,
                proxy=config.proxy,
            )
        )

    @staticmethod
    def _chat_id(chat: ChatRef | Any) -> Any:
        return getattr(chat, "id", chat)

    @staticmethod
    def _message_id(message: Any) -> int | None:
        if isinstance(message, int):
            return message
        return getattr(message, "id", getattr(message, "message_id", None))

    @classmethod
    def _chat_summary(cls, dialog: Any) -> ChatSummary:
        entity = getattr(dialog, "entity", dialog)
        chat_id = getattr(dialog, "id", getattr(entity, "id", None))
        title = (
            getattr(dialog, "title", None)
            or getattr(entity, "title", None)
            or getattr(entity, "first_name", None)
            or getattr(entity, "username", None)
            or str(chat_id)
        )
        if getattr(entity, "broadcast", False):
            kind = "channel"
        elif getattr(entity, "megagroup", False) or getattr(entity, "group", False):
            kind = "group"
        else:
            kind = "user"
        return ChatSummary(
            chat_id=chat_id,
            title=str(title),
            username=getattr(entity, "username", None),
            kind=kind,
            unread_count=int(getattr(dialog, "unread_count", 0) or 0),
        )

    @classmethod
    def _chat_message(cls, chat_id: Any, raw: Any, *, fallback_text: str = "") -> ChatMessage:
        if isinstance(raw, ChatMessage):
            return raw
        document = getattr(raw, "document", None)
        file_info = getattr(raw, "file", None)
        media_type = (
            getattr(document, "mime_type", None)
            or getattr(file_info, "mime_type", None)
        )
        if getattr(raw, "photo", None) is not None and not media_type:
            media_type = "image/jpeg"
        return ChatMessage(
            chat_id=chat_id,
            message_id=int(getattr(raw, "id", 0)),
            text=getattr(raw, "message", None)
            or getattr(raw, "text", None)
            or fallback_text,
            sender_id=getattr(raw, "sender_id", None),
            date=getattr(raw, "date", None),
            media_type=media_type,
            filename=getattr(file_info, "name", None),
            caption=getattr(raw, "caption", None),
            buttons=tuple(
                tuple(str(getattr(button, "text", "") or "") for button in row)
                for row in (getattr(raw, "buttons", None) or ())
            ),
            out=bool(getattr(raw, "out", False)),
            reply_to_msg_id=getattr(raw, "reply_to_msg_id", None),
        )

    async def list_chats(self) -> list[ChatSummary]:
        """List dialogs as provider-neutral chat summaries."""

        dialogs: list[Any] = []
        iterator = getattr(self.client, "iter_dialogs", None)
        if iterator is not None:
            async for dialog in iterator():
                dialogs.append(dialog)
        else:
            result = await self.client.get_dialogs()
            dialogs.extend(result or [])
        return [self._chat_summary(dialog) for dialog in dialogs]

    async def read_chat(
        self, chat: ChatRef, limit: int | None = 100
    ) -> list[ChatMessage]:
        """Read recent messages from ``chat`` as immutable DTOs."""

        kwargs = {} if limit is None else {"limit": limit}
        result = await self.client.get_messages(self._chat_id(chat), **kwargs)
        if result is None:
            return []
        values = result if isinstance(result, (list, tuple)) else [result]
        return [self._chat_message(self._chat_id(chat), value) for value in values if value]

    async def acknowledge_chat(self, chat: ChatRef) -> None:
        """Acknowledge all currently visible messages in a chat."""

        acknowledge = getattr(self.client, "send_read_acknowledge", None)
        if acknowledge is not None:
            await acknowledge(self._chat_id(chat))

    async def read_message(self, chat: ChatRef, message_id: int) -> ChatMessage | None:
        raw = await self.client.get_messages(self._chat_id(chat), ids=int(message_id))
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else None
        if raw is None:
            return None
        return self._chat_message(self._chat_id(chat), raw)

    async def forward_message(
        self, source_chat: ChatRef, message: Any, target_chat: ChatRef
    ) -> ChatMessage:
        """Forward one message while keeping source and destination explicit."""

        message_id = self._message_id(message)
        if message_id is None:
            raise LookupError(f"message id is required: {message!r}")
        source = self._chat_id(source_chat)
        target = self._chat_id(target_chat)
        forward = getattr(self.client, "forward_messages", None)
        if forward is None:
            raise NotImplementedError("Telegram client does not support forwarding")
        try:
            raw = await forward(target, message_id, from_peer=source)
        except TypeError:
            raw = await forward(target, message_id, source)
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else None
        if raw is None:
            return ChatMessage(chat_id=target, message_id=message_id, text="")
        return self._chat_message(target, raw)

    async def _raw_message(self, chat: ChatRef, message: Any) -> Any:
        if not isinstance(message, (int, ChatMessage)) and callable(
            getattr(message, "download_media", None)
        ):
            return message
        message_id = self._message_id(message)
        if message_id is None:
            raise LookupError(f"message id is required: {message!r}")
        raw = await self.client.get_messages(self._chat_id(chat), ids=message_id)
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else None
        if raw is None:
            raise LookupError(f"message not found: {self._chat_id(chat)}/{message_id}")
        return raw

    async def download_media(
        self, chat: ChatRef, message: Any
    ) -> DownloadedMedia:
        """Download message media into an immutable byte DTO."""

        raw = await self._raw_message(chat, message)
        data = await raw.download_media(bytes)
        if data is None:
            raise LookupError(f"media not found: {self._chat_id(chat)}/{self._message_id(raw)}")
        document = getattr(raw, "document", None)
        file_info = getattr(raw, "file", None)
        mime_type = (
            getattr(document, "mime_type", None)
            or getattr(file_info, "mime_type", None)
        )
        if getattr(raw, "photo", None) is not None and not mime_type:
            mime_type = "image/jpeg"
        return DownloadedMedia(
            chat_id=self._chat_id(chat),
            message_id=int(self._message_id(raw) or 0),
            data=data,
            mime_type=mime_type,
            filename=getattr(file_info, "name", None),
        )

    async def send_message(
        self, chat: ChatRef, text: str, *, reply_to: int | None = None
    ) -> ChatMessage:
        """Send a text message and return its neutral DTO."""

        target = self._chat_id(chat)
        try:
            if reply_to is None:
                raw = await self.client.send_message(target, text)
            else:
                raw = await self.client.send_message(target, text, reply_to=reply_to)
        except ForbiddenError as exc:
            raise ChatPermissionError(getattr(exc, "message", str(exc))) from exc
        except PeerIdInvalidError as exc:
            raise ChatInvalidPeerError(str(exc)) from exc
        except PeerFloodError as exc:
            raise ChatRateLimitError(str(exc)) from exc
        if raw is None:
            return ChatMessage(chat_id=target, message_id=0, text=text)
        return self._chat_message(target, raw, fallback_text=text)

    async def delete_message(self, chat: ChatRef, message: Any) -> bool:
        """Delete one message and report whether the provider accepted it."""

        target = self._chat_id(chat)
        message_id = self._message_id(message)
        if message_id is None:
            raise LookupError(f"message id is required: {message!r}")
        delete = getattr(self.client, "delete_messages", None)
        if delete is not None:
            result = await delete(target, [message_id])
            return True if result is None else bool(result)
        raw = await self._raw_message(chat, message)
        await raw.delete()
        return True

    async def edit_message(
        self, chat: ChatRef, message: Any, text: str
    ) -> ChatMessage:
        """Edit one message and return the updated neutral DTO."""

        target = self._chat_id(chat)
        message_id = self._message_id(message)
        if message_id is None:
            raise LookupError(f"message id is required: {message!r}")
        edit = getattr(self.client, "edit_message", None)
        if edit is not None:
            raw = await edit(target, message_id, text)
        else:
            raw = await self._raw_message(chat, message)
            raw = await raw.edit(text)
        if raw is None:
            return ChatMessage(chat_id=target, message_id=message_id, text=text)
        return self._chat_message(target, raw, fallback_text=text)

    async def react(self, chat: ChatRef, message: Any, emoji: str) -> None:
        """Add one emoji reaction to a message."""

        target = self._chat_id(chat)
        raw = message
        if not callable(getattr(raw, "react", None)):
            raw = await self._raw_message(chat, message)
        react = getattr(raw, "react", None)
        if callable(react):
            await react(emoji)
            return
        message_id = self._message_id(raw)
        if message_id is None:
            raise LookupError(f"message id is required: {message!r}")
        await self.client(
            functions.messages.SendReactionRequest(
                peer=target,
                msg_id=message_id,
                reaction=[types.ReactionEmoji(emoticon=emoji)],
            )
        )

    @asynccontextmanager
    async def typing(self, chat: ChatRef):
        """Keep the provider's typing indicator active for the context body."""

        action = getattr(self.client, "action", None)
        if action is None:
            yield
            return
        async with action(self._chat_id(chat), "typing"):
            yield

    async def join_channel(self, link: str) -> bool:
        """Join a channel or chat using either an invite or a public link."""
        try:
            if link.startswith("tg://join?"):
                invite = parse_qs(urlparse(link).query).get("invite", [""])[0]
                if not invite:
                    return False
                await self.client(ImportChatInviteRequest(invite))
            elif "+" in link or "joinchat" in link:
                invite = link.rsplit("/", 1)[-1].split("+")[-1]
                await self.client(ImportChatInviteRequest(invite))
            else:
                username = link.rsplit("/", 1)[-1].lstrip("@")
                await self.client(JoinChannelRequest(username))
        except UserAlreadyParticipantError:
            return True
        except Exception:  # pragma: no cover - logging only
            logger.exception("Failed to join %s:", link, exc_info=True)
            return False
        return True

    async def click_button(
        self, chat: ChatRef, message: Any, row: int, column: int
    ) -> ChatMessage | None:
        """Click one provider button and map the resulting message."""

        raw = await self._raw_message(chat, message)
        click = getattr(raw, "click", None)
        if click is None:
            return None
        result = await click(row, column)
        if result is None:
            result = raw
        return self._chat_message(self._chat_id(chat), result)

    @staticmethod
    def _profile_from_entity(entity: Any, *, about: str | None = None) -> TelegramProfileRef | None:
        user_id = getattr(entity, "id", None)
        if user_id is None:
            return None
        return TelegramProfileRef(
            user_id=int(user_id),
            username=getattr(entity, "username", None),
            access_hash=getattr(entity, "access_hash", None),
            first_name=getattr(entity, "first_name", None),
            last_name=getattr(entity, "last_name", None),
            about=about if about is not None else getattr(entity, "about", None),
        )

    async def get_account(self) -> TelegramAccountRef:
        """Return the current writer identity without exposing a raw entity."""

        account = await self.client.get_me()
        return TelegramAccountRef(
            user_id=int(account.id),
            username=getattr(account, "username", None),
            first_name=getattr(account, "first_name", None),
            last_name=getattr(account, "last_name", None),
        )

    async def get_profile(
        self, participant: TelegramParticipantRef | str | int
    ) -> TelegramProfileRef | None:
        """Resolve a profile and map the optional full-user details."""

        if isinstance(participant, TelegramParticipantRef):
            target: Any = participant.username or participant.user_id
        else:
            target = participant
        entity = await self.client.get_entity(target)
        about = getattr(entity, "about", None)
        request = getattr(self.client, "__call__", None)
        if callable(request):
            try:
                full = await request(GetFullUserRequest(entity.id))
                full_user = getattr(full, "full_user", full)
                about = getattr(full_user, "about", about)
                first_name = getattr(full_user, "first_name", getattr(entity, "first_name", None))
                last_name = getattr(full_user, "last_name", getattr(entity, "last_name", None))
                entity = type("ProfileEntity", (), {
                    "id": entity.id,
                    "username": getattr(entity, "username", None),
                    "access_hash": getattr(entity, "access_hash", None),
                    "first_name": first_name,
                    "last_name": last_name,
                })()
            except Exception:
                logger.debug("Failed to fetch full profile for %s", target, exc_info=True)
        return self._profile_from_entity(entity, about=about)

    async def ensure_membership(self, source_group: TelegramSourceGroup) -> bool:
        """Ensure writer membership only when provenance requires it."""

        if not source_group.membership_required:
            return True
        link = source_group.invite_link or source_group.public_link
        if not link:
            return False
        return await self.join_channel(link)

    async def resolve_participant(
        self,
        participant: TelegramParticipantRef,
        *,
        source_group: TelegramSourceGroup | None = None,
        message_link: str | None = None,
    ) -> TelegramProfileRef | None:
        """Resolve a participant after satisfying its source-group prerequisite."""

        if source_group is not None and not await self.ensure_membership(source_group):
            return None
        if participant.username:
            try:
                return await self.get_profile(participant.username)
            except Exception:
                logger.debug("Failed to resolve participant @%s", participant.username, exc_info=True)
        if message_link:
            try:
                chat, mid = parse_msg_url(message_link)
                raw = await self.client.get_messages(chat, ids=int(mid))
                if raw:
                    sender = await raw.get_sender()
                    profile = self._profile_from_entity(sender)
                    if profile is not None:
                        return profile
            except Exception:
                logger.debug("Failed to resolve participant from %s", message_link, exc_info=True)
        try:
            return await self.get_profile(participant.user_id)
        except Exception:
            logger.debug("Failed to resolve participant id %s", participant.user_id, exc_info=True)
            return None

    async def can_message(self, peer: Any) -> tuple[bool, str | None]:
        if isinstance(peer, TelegramParticipantRef):
            if peer.access_hash is not None:
                peer = InputPeerUser(peer.user_id, peer.access_hash)
            else:
                peer = peer.username or peer.user_id
        return await can_message(self.client, peer)

    @staticmethod
    def _membership_from_error(channel: str, exc: Exception) -> MembershipResult:
        text = str(exc)
        if "USER_BANNED_IN_CHANNEL" in text:
            status = MembershipStatus.BANNED
        elif "requested to join this chat" in text:
            status = MembershipStatus.REQUESTED
        else:
            status = MembershipStatus.NOT_SUBSCRIBED
        return MembershipResult(channel=channel, status=status)

    async def inspect_membership(self, channel: str) -> MembershipResult:
        try:
            entity = await self.client.get_entity(channel)
            await self.client(GetParticipantRequest(entity, "me"))
            return MembershipResult(channel=channel, status=MembershipStatus.JOINED)
        except Exception as exc:
            return self._membership_from_error(channel, exc)

    async def request_membership(self, channel: str) -> MembershipResult:
        current = await self.inspect_membership(channel)
        if current.status is not MembershipStatus.NOT_SUBSCRIBED:
            return current
        try:
            entity = await self.client.get_entity(channel)
            await self.client(JoinChannelRequest(entity))
            return MembershipResult(channel=channel, status=MembershipStatus.JOINED)
        except Exception as exc:
            return self._membership_from_error(channel, exc)

    @staticmethod
    def _telegram_context(manager: "DialogueManager") -> dict[str, Any]:
        target = manager.channel_data.get("target") or {}
        return target.get("telegram") or manager.channel_data.get("telegram") or {}

    async def ensure_admin(self, manager: "DialogueManager") -> None:
        """Compatibility facade over the provider-neutral identity methods.

        The application still asks the adapter to populate legacy manager
        fields, but all provider-specific resolution stays in this adapter.
        """

        context = self._telegram_context(manager)
        if str(manager.admin).startswith("@") and not context:
            return
        source_group = None
        participant_ref = None
        message_link = manager.channel_data.get("msg_url")
        if context:
            try:
                source_group = TelegramSourceGroup.model_validate(context.get("source_group") or {})
                participant_ref = TelegramParticipantRef.model_validate(context.get("participant") or {})
            except Exception:
                logger.exception("Invalid Telegram target context while resolving %s", manager.admin)
                return
            expected_writer_id = context.get("writer_account_id")
            if expected_writer_id is not None:
                try:
                    writer = await self.get_account()
                except Exception:
                    logger.error("Cannot resolve participant: writer identity is unavailable")
                    return
                if writer.user_id != expected_writer_id:
                    logger.error(
                        "Cannot resolve participant: expected writer %s, got %s",
                        expected_writer_id,
                        writer.user_id,
                    )
                    return
        else:
            try:
                participant_ref = TelegramParticipantRef(
                    user_id=int(manager.admin),
                )
            except (TypeError, ValueError):
                profile = await self.get_profile(str(manager.admin).lstrip("@"))
                if profile is None:
                    return
                participant_ref = TelegramParticipantRef(
                    user_id=profile.user_id,
                    username=profile.username,
                    access_hash=profile.access_hash,
                )

        profile = await self.resolve_participant(
            participant_ref,
            source_group=source_group,
            message_link=message_link,
        )
        if profile is None or profile.access_hash is None:
            logger.error("Cannot resolve Telegram participant %s", manager.admin)
            return

        manager.resolved_participant = profile
        manager.input_peer = InputPeerUser(profile.user_id, profile.access_hash)
        manager.admin = profile.username or str(profile.user_id)
        manager.access_hash = profile.access_hash
        manager.channel_data.setdefault(
            "full_name", " ".join(filter(None, [profile.first_name, profile.last_name]))
        )


async def can_message(
    client: TelegramClient, peer: InputPeerUser | int | str
) -> tuple[bool, str | None]:
    """Check if the bot can send messages to ``peer``.

    Returns ``(ok, reason)`` where ``reason`` is one of
    ``blocked_by_peer``, ``privacy_restricted``, ``write_forbidden``,
    ``user_deleted``, ``peer_invalid`` or ``other``.
    """
    if not callable(getattr(client, "__call__", None)):
        return True, None
    try:
        await client(
            functions.messages.SetTypingRequest(
                peer=peer, action=types.SendMessageTypingAction()
            )
        )
        return True, None
    except (UserIsBlockedError,YouBlockedUserError):
        return False, "blocked_by_peer"
    except errors.UserPrivacyRestrictedError:
        return False, "privacy_restricted"
    except errors.ChatWriteForbiddenError:
        return False, "write_forbidden"
    except errors.InputUserDeactivatedError:
        return False, "user_deleted"
    except errors.PeerIdInvalidError:
        return False, "peer_invalid"
    except errors.RPCError:
        return False, "other"


@dataclass(frozen=True, slots=True)
class TelegramEnvConfig:
    """Telegram client settings resolved from environment variables."""

    api_id: int
    api_hash: str
    session: str
    proxy: dict[str, object] | None


def _proxy_spec(url: str | None) -> dict[str, object] | None:
    """Convert a proxy URL (optionally with userinfo) into a Telethon proxy dict."""

    if not url:
        return None
    parsed = urlparse(url)
    if not parsed.hostname or parsed.port is None:
        return None
    scheme = parsed.scheme.lower()
    if scheme == "socks5h":
        scheme = "socks5"
    spec: dict[str, object] = {
        "proxy_type": scheme,
        "addr": parsed.hostname,
        "port": parsed.port,
        "rdns": True,
    }
    if parsed.username:
        spec["username"] = parsed.username
    if parsed.password:
        spec["password"] = parsed.password
    return spec


def telegram_env_config(env: Mapping[str, str] | None = None) -> TelegramEnvConfig:
    """Resolve Telegram settings from ``USERIO_TELEGRAM_*`` with ``TG_*`` fallbacks."""

    source: Mapping[str, str] = os.environ if env is None else env

    def pick(*names: str) -> str | None:
        for name in names:
            value = source.get(name)
            if value:
                return value
        return None

    api_id = pick("USERIO_TELEGRAM_API_ID", "TG_API_ID")
    api_hash = pick("USERIO_TELEGRAM_API_HASH", "TG_API_HASH")
    session = pick("USERIO_TELEGRAM_SESSION", "TG_SESSION")
    missing = [
        label
        for label, value in (
            ("api_id", api_id),
            ("api_hash", api_hash),
            ("session", session),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            "Telegram env config incomplete, missing: "
            + ", ".join(missing)
            + "; set USERIO_TELEGRAM_API_ID/API_HASH/SESSION"
            " or TG_API_ID/API_HASH/SESSION"
        )
    return TelegramEnvConfig(
        api_id=int(api_id),
        api_hash=str(api_hash),
        session=str(session),
        proxy=_proxy_spec(pick("USERIO_TELEGRAM_PROXY", "TG_PROXY")),
    )
