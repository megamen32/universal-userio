"""Pure event-bound reply contract, with no provider, store or model calls.

The integration must load events and consent from server-owned records. Neither
an event ID, a connected account nor this dataclass itself grants send authority.
Historical events lacking explicit account/peer provenance fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Mapping


def _text(value: Any, name: str, maximum: int = 1024) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        raise ValueError(f"invalid same-channel {name}")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"invalid same-channel {name}")
    return value


@dataclass(frozen=True, slots=True)
class ReplyOrigin:
    user_id: str
    event_seq: int
    source: str
    account_ref: str
    conversation_id: str
    peer_id: str
    sender_id: str
    message_id: str
    provider_message_id: str
    reply_target: str
    conversation_kind: str
    body_sha256: str
    edited_at: float

    @classmethod
    def from_event(cls, event: Mapping[str, Any], *, user_id: str) -> ReplyOrigin:
        user_id = _text(user_id, "owner")
        if event.get("user_id", user_id) != user_id:
            raise ValueError("foreign same-channel event owner")
        seq = event.get("seq")
        if type(seq) is not int or seq <= 0:
            raise ValueError("invalid same-channel event sequence")
        if event.get("direction") != "incoming" or event.get("sender_is_bot", False):
            raise ValueError("same-channel reply requires an incoming human event")
        if event.get("body_truncated", False):
            raise ValueError("read exact event body before replying")
        source = _text(event.get("source"), "source", 32)
        if source not in {"sms", "matrix"}:
            raise ValueError("unsupported same-channel source")
        account = _text(event.get("account_ref"), "account")
        peer = _text(event.get("peer_id"), "peer")
        sender = _text(event.get("sender_id") or event.get("sender"), "sender")
        message = _text(event.get("message_id"), "message")
        provider_message = _text(event.get("provider_message_id", message if source == "sms" else None), "provider message")
        kind = _text(event.get("conversation_kind"), "conversation kind", 32)
        body = event.get("body")
        edited_at = event.get("edited_at", 0)
        if not isinstance(body, str) or not body.strip() or len(body) > 65536:
            raise ValueError("same-channel source requires an exact bounded body")
        if type(edited_at) not in {int, float} or not math.isfinite(edited_at) or edited_at < 0:
            raise ValueError("invalid same-channel source revision")
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if source == "sms":
            if not account.startswith("sms:") or kind != "direct" or peer != sender or not re.fullmatch(r"\+[1-9][0-9]{7,14}", peer):
                raise ValueError("SMS origin lacks exact account and originating number")
        elif (not account.startswith("matrix") or kind not in {"direct", "group"}
              or not peer.startswith("!") or ":" not in peer
              or not sender.startswith("@") or ":" not in sender
              or not provider_message.startswith("$")):
            raise ValueError("Matrix origin lacks exact account, room and event")
        # An ordinary reply targets THIS message, not its earlier quoted parent.
        return cls(user_id, seq, source, account,
                   _text(event.get("conversation_id"), "conversation"), peer,
                   sender, message, provider_message, provider_message, kind,
                   body_hash, float(edited_at))

    def identity(self) -> tuple[object, ...]:
        return (self.user_id, self.event_seq, self.source, self.account_ref,
                self.conversation_id, self.peer_id, self.sender_id,
                self.message_id, self.provider_message_id, self.reply_target,
                self.conversation_kind, self.body_sha256, self.edited_at)

    def conversation_key(self) -> str:
        """Opaque composite key; never merge Matrix rooms by sender identity."""
        return json.dumps([self.source, self.account_ref, self.peer_id], separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class EventReplyConsent:
    """Server-resolved consent, not a client-accepted operation argument.

    The service must first verify its current workspace claim/lease and user
    policy. Lease possession alone must not fabricate this consent record.
    """
    origin: ReplyOrigin
    worker_id: str
    expires_at: float
    allow_ordinary_reply: bool


@dataclass(frozen=True, slots=True)
class SameChannelReplyRequest:
    origin: ReplyOrigin
    request_id: str
    text: str


def authorize_reply(request: SameChannelReplyRequest, *, current_origin: ReplyOrigin,
                    consent: EventReplyConsent, worker_id: str, now: float,
                    account: Mapping[str, Any], send_enabled: bool) -> None:
    """Validate exact immutable source and narrowly scoped reply authority.

    After this succeeds, integration still must atomically claim the request,
    revalidate the source account immediately before send, use provider-side
    idempotency when available, and preserve uncertain sends for inspection.
    """
    if request.origin.identity() != current_origin.identity() or consent.origin.identity() != current_origin.identity():
        raise ValueError("same-channel source changed")
    _text(worker_id, "worker", 128)
    if (consent.worker_id != worker_id or not consent.allow_ordinary_reply
            or not math.isfinite(now) or not math.isfinite(consent.expires_at)
            or consent.expires_at <= now):
        raise ValueError("same-channel event reply is not authorized")
    if not send_enabled or account.get("enabled") is not True or "reply" not in account.get("capabilities", ()):
        raise ValueError("same-channel account cannot reply")
    if account.get("id") != current_origin.account_ref or account.get("provider") != current_origin.source:
        raise ValueError("same-channel account mismatch")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", request.request_id):
        raise ValueError("invalid same-channel idempotency key")
    if not isinstance(request.text, str) or not request.text.strip() or len(request.text) > 4096:
        raise ValueError("invalid same-channel reply text")
    if any(ord(char) < 32 and char not in "\n\t" for char in request.text):
        raise ValueError("invalid same-channel reply text")
