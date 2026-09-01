"""Transport adapters; credentials and provider URLs stay outside the AI domain."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from .contracts import ChannelFile, InboxMessage, ReplyDraft

if TYPE_CHECKING:
    from .service import UserIOService
    from .store import SQLiteUserIOStore


def inbox_message_from_envelope(payload: Mapping[str, Any], *, received_at: float) -> InboxMessage:
    if payload.get("schema") != "universal.inbox.message.v1":
        raise ValueError("unsupported inbox schema")
    source = str(payload.get("source") or "").strip().lower()
    message_id = str(payload.get("message_id") or "").strip()
    sender = str(payload.get("sender") or "").strip()
    body = str(payload.get("body") or "").strip()
    if source not in {"telegram", "matrix", "whatsapp", "vk", "phone", "email", "gmail"} and not source.startswith("gmail:"):
        raise ValueError("unsupported message source")
    if not message_id or not sender or not body:
        raise ValueError("inbox message requires message_id, sender and body")
    return InboxMessage(source, message_id, sender, body, received_at)


@dataclass(frozen=True, slots=True)
class NoticePlaceRoute:
    event_url: str
    token: str
    project: str
    recipient: str = "userio"
    severity: str = "notice"


class NoticePlaceOutboxClient:
    """Emit a policy-bound reply intent; the route owns every destination detail."""

    def __init__(self, routes: Mapping[str, NoticePlaceRoute], *, runner: Any = urllib.request.urlopen) -> None:
        self._routes = dict(routes)
        self._runner = runner

    def send_reply(self, *, route_id: str, conversation_id: str, draft_id: str, body: str) -> str:
        route = self._routes.get(route_id)
        if route is None:
            raise ValueError("unknown UserIO route")
        dedup = f"userio:{conversation_id}:{draft_id}"
        payload = {
            "schema": "notify.event.v1",
            "project": route.project,
            "recipient": route.recipient,
            "kind": "notification",
            "severity": route.severity,
            "title": "UserIO approved reply",
            "body": body,
            "dedup_key": dedup,
            "correlation_id": conversation_id,
            "event_type": "userio.reply.v1",
            "producer": "universal-userio",
            "plugin": "userio",
        }
        request = urllib.request.Request(
            route.event_url.rstrip("/") + "/v1/events",
            data=json.dumps(payload, ensure_ascii=False, sort_keys=True).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {route.token}",
                "Idempotency-Key": "userio-" + hashlib.sha256(dedup.encode()).hexdigest(),
            },
            method="POST",
        )
        try:
            with self._runner(request, timeout=8) as response:
                if int(response.status) != 202:
                    raise RuntimeError(f"NoticePlace returned HTTP {response.status}")
                result = json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"NoticePlace returned HTTP {error.code}") from error
        event_id = result.get("event_id") if isinstance(result, dict) else None
        if not isinstance(event_id, str) or not event_id:
            raise RuntimeError("NoticePlace returned invalid acceptance receipt")
        return event_id


class AdapterNotSupported(ValueError):
    """The selected adapter honestly does not implement this capability."""


class StoredChannelAdapter:
    """User-bound wrapper over UserIO's canonical conversations and draft queue."""

    channel: str | None = None

    def __init__(self, store: SQLiteUserIOStore, service: UserIOService, user_id: str) -> None:
        self._store, self._service, self._user_id = store, service, user_id

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        records = self._store.conversations(
            source=self.channel, limit=max(1, min(limit, 100)), user_id=self._user_id
        )
        return [
            {
                "id": record["id"],
                "channel": _public_channel(str(record["source"])),
                "title": record["identity_id"] or record["sender"],
                "last_message_snippet": str(record["preview"] or "")[:500],
                "unread": int(record["unread_count"]),
            }
            for record in records
        ]

    def read(
        self, *, chat_id: str | None = None, message_id: str | None = None
    ) -> dict[str, Any]:
        if bool(chat_id) == bool(message_id):
            raise ValueError("provide exactly one of chat_id or message_id")
        if chat_id:
            record = self._store.conversation(chat_id, user_id=self._user_id, text_limit=65_536)
            if record is None or not self._matches(str(record["source"])):
                raise KeyError("chat not found")
            return {"chat": record}
        source = self.channel
        record = self._store.message(
            str(message_id), source=source, user_id=self._user_id, text_limit=65_536
        )
        if record is None or not self._matches(str(record["source"])):
            raise KeyError("message not found")
        return {"message": record}

    def download(self, *, file_ref: str) -> ChannelFile:
        del file_ref
        raise AdapterNotSupported("not supported by adapter")

    def send(
        self, *, chat_id: str, text: str, attachments: list[str] | None = None
    ) -> ReplyDraft:
        if attachments:
            raise AdapterNotSupported("attachments are not supported by adapter")
        record = self._store.conversation(chat_id, user_id=self._user_id)
        if record is None or not self._matches(str(record["source"])):
            raise KeyError("chat not found")
        return self._service.create_manual_draft(chat_id, body=text, user_id=self._user_id)

    def _matches(self, source: str) -> bool:
        if self.channel is None:
            return True
        if self.channel == "mail":
            return source in {"mail", "email", "gmail"} or source.startswith("gmail:")
        return source == self.channel


class MailChannelAdapter(StoredChannelAdapter):
    channel = "mail"


class TelegramChannelAdapter(StoredChannelAdapter):
    channel = "telegram"


class WhatsAppChannelAdapter(StoredChannelAdapter):
    channel = "whatsapp"


class VKChannelAdapter(StoredChannelAdapter):
    channel = "vk"


# Provider-facing compatibility names; all expose the same four-method contract.
GmailChannelAdapter = MailChannelAdapter


class UnifiedChannels(StoredChannelAdapter):
    """Resolve all current provider wrappers behind one four-method interface."""

    _types = {
        "mail": MailChannelAdapter,
        "gmail": MailChannelAdapter,
        "email": MailChannelAdapter,
        "telegram": TelegramChannelAdapter,
        "whatsapp": WhatsAppChannelAdapter,
        "vk": VKChannelAdapter,
    }

    def adapter(self, channel: str | None) -> StoredChannelAdapter:
        if not channel:
            return self
        adapter_type = self._types.get(channel.strip().lower())
        if adapter_type is None:
            raise ValueError("unknown channel")
        return adapter_type(self._store, self._service, self._user_id)

    def download(self, *, file_ref: str) -> ChannelFile:
        channel, separator, provider_ref = file_ref.partition(":")
        if separator and channel in self._types:
            return self.adapter(channel).download(file_ref=provider_ref)
        raise AdapterNotSupported("not supported by adapter")


def _public_channel(source: str) -> str:
    return "mail" if source in {"mail", "email", "gmail"} or source.startswith("gmail:") else source
