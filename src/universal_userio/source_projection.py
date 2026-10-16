"""Canonical source/author projection shared by every reader of a message.

The stored ``messages.sender`` is the conversation counterpart, not necessarily
the author: an outgoing message the owner sent is stored with the interlocutor's
name. Any surface that reports who wrote a message must use these rules, so the
messenger API, search hits and the web UI cannot disagree about an author.
"""
from __future__ import annotations

import json
from typing import Any, Mapping


def routing_for(attachments: Any) -> dict[str, Any]:
    """Provider routing metadata carried on the message, when present."""
    if isinstance(attachments, Mapping):
        candidates: list[Any] = [attachments]
    else:
        candidates = list(attachments or [])
    for attachment in candidates:
        try:
            item = attachment if isinstance(attachment, Mapping) else {}
        except TypeError:
            continue
        if item.get("kind") != "telegram_routing":
            continue
        try:
            parsed = json.loads(str(item.get("provider_ref") or "{}"))
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict) and parsed:
            return parsed
    return {}


def canonical_author(*, channel: str, account_ref: str, conversation_kind: str, peer_id: str,
                     direction: str, sender: str, sender_name: str = "",
                     routing: Mapping[str, Any] | None = None,
                     gmail_sender_id: str = "") -> dict[str, Any]:
    """Who actually wrote this message, and in which role.

    ``owner`` is the account that sent it, ``interlocutor`` the peer. The chat
    title never substitutes for the author of an outgoing message.
    """
    routing = dict(routing or {})
    outgoing = direction == "outgoing"
    if channel == "gmail":
        default_id = gmail_sender_id if outgoing else sender
    elif channel == "telegram" and outgoing:
        default_id = account_ref.split(":", 1)[-1] if account_ref.startswith("telegram:") else sender
    elif channel == "telegram" and conversation_kind == "direct":
        default_id = peer_id
    else:
        default_id = sender
    name = (str(routing.get("author_name") or "") or sender_name
            or ("" if outgoing else sender))
    return {
        "id": str(routing.get("author_id") or default_id),
        "name": name,
        "username": str(routing.get("author_username") or ""),
        "first_name": str(routing.get("author_first_name") or ""),
        "last_name": str(routing.get("author_last_name") or ""),
        "role": "owner" if outgoing else "interlocutor",
    }


def canonical_source(*, account_ref: str, conversation_id: str, peer_id: str,
                     conversation_kind: str, channel: str, message_id: str,
                     provider_message_id: str, direction: str,
                     routing: Mapping[str, Any] | None = None,
                     thread_id: str = "", reply_to: str = "") -> dict[str, Any]:
    """The exact source coordinates of one message, as the messenger API reports them."""
    routing = dict(routing or {})
    return {
        "account_ref": account_ref, "conversation_id": conversation_id, "peer_id": peer_id,
        "conversation_kind": conversation_kind, "message_id": message_id,
        "provider_message_id": provider_message_id, "channel": channel,
        "direction": direction,
        "thread_id": str(thread_id or routing.get("reply_to_top_id")
                         or routing.get("thread_id") or ""),
        "reply_to_provider_message_id": str(
            reply_to or routing.get("reply_to_message_id")
            or routing.get("reply_to_msg_id") or ""),
        "invocation": {"mention": routing.get("is_exact_account_mention") is True,
                       "reply": routing.get("is_reply_to_account") is True},
    }