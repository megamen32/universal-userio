"""Canonical conversation keys for peers that are not in the history yet.

UserIO keys a conversation by the pair (source, account, peer). The inbound
ingress derives that key from the provider envelope, so a conversation created
for a brand-new contact must use exactly the same key the contact's first
inbound message will produce — otherwise the reply lands in a second chat.

This module is the single source of truth for that rule. ``service.receive``
and the first-contact writer both call :func:`peer_conversation_key`.
"""
from __future__ import annotations

import hashlib

EMAIL_SOURCES = frozenset({"mail", "email", "gmail"})


def is_email_source(source: str) -> bool:
    """True for the mail family, whose conversations are keyed by the From header."""
    source = (source or "").strip().lower()
    return source in EMAIL_SOURCES or source.startswith(("gmail:", "email:"))


def peer_conversation_key(
    source: str,
    account_ref: str = "",
    peer_id: str = "",
    sender: str = "",
    *,
    native_sms: bool = False,
) -> str:
    """Return the conversation key the ingress uses for this exact peer.

    The parameters mirror what the provider envelope carries: ``peer_id`` is
    the provider-native peer identity and ``sender`` the human-readable
    originator. A first-contact writer only knows the identifier, so ``sender``
    carries it until the provider resolves a native peer.
    """
    source = (source or "").strip()
    account_ref = (account_ref or "").strip()
    peer_id = (peer_id or "").strip()
    sender = (sender or "").strip()
    if source == "yandex" and account_ref.startswith("yandex:") and peer_id:
        return f"yandex:{account_ref}:{peer_id}"
    if source == "max" and account_ref and peer_id:
        return f"max:{account_ref}:{peer_id}"
    if source == "vk" and account_ref.startswith("vk:") and peer_id:
        return f"vk:{account_ref}:{peer_id}"
    if source == "sms":
        if native_sms:
            # The native Android gateway keys a thread by the normalized address.
            return f"sms:{sender}"
        if account_ref.startswith("sms:"):
            return f"sms:{account_ref}:{peer_id or sender}"
        return f"sms:{sender}"
    if source == "telegram":
        if peer_id.lstrip("-").isdigit() and account_ref:
            return f"telegram:{account_ref}:{peer_id}"
        return f"telegram:{sender}"
    if source.startswith("email:"):
        # IMAP conversations are private to one linked mailbox.
        return source + ":" + sender.casefold()
    if source == "email" or source == "gmail" or source.startswith("gmail:"):
        return "email:" + sender.casefold()
    if source.startswith("matrix:") and peer_id:
        return source + ":" + peer_id
    return f"{source}:{sender}"


def peer_conversation_id(user_id: str, conversation_key: str) -> str:
    """Return the deterministic conversation id for one key."""
    digest = hashlib.sha256(f"{user_id}\0{conversation_key}".encode()).hexdigest()
    return "conv_" + digest[:24]


def mail_address(value: str) -> str:
    """Return the bare address of a ``Name <address>`` sender, lowercased."""
    from email.utils import parseaddr

    address = parseaddr((value or "").strip())[1]
    return address.strip().casefold()
