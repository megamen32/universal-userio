"""Start a conversation with a contact that is not in the history yet.

UserIO writes into conversations that already exist. This module resolves a
human identifier (username, phone number, mail address, provider id) into a
provider-native peer, opens the canonical conversation for that peer and hands
the first message to the existing durable send path.

The send itself is not new: :meth:`UserIOService.send_manual_message` already
claims a draft, delivers it through the per-channel outbox and stores the
receipt. Only the peer resolution and the conversation creation are new.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .contact_keys import mail_address

_MAIL_ADDRESS = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_PHONE = re.compile(r"[0-9]{7,15}")


class ContactStartError(ValueError):
    """The identifier cannot become a writable conversation."""


@dataclass(frozen=True)
class ResolvedContact:
    """One provider-native peer, resolved from a human identifier."""

    provider: str
    account_ref: str
    source: str
    peer_id: str
    display_name: str
    route_id: str = ""
    peer_kind: str = "direct"
    identifier: str = ""
    native_sms: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "account_ref": self.account_ref,
            "channel": self.source,
            "peer_id": self.peer_id,
            "display_name": self.display_name,
            "route_id": self.route_id,
            "peer_kind": self.peer_kind,
            "identifier": self.identifier,
        }


class ContactStarter:
    """Resolve an identifier and open the first conversation with it."""

    def __init__(self, store: Any, service: Any) -> None:
        self._store = store
        self._service = service

    # -- accounts -----------------------------------------------------------------

    def account(self, *, user_id: str, account_ref: str, need_send: bool = True) -> dict[str, Any]:
        """Return the account this contact will be written from."""
        account_ref = str(account_ref or "").strip()
        if not account_ref:
            raise ContactStartError("contact_account_required")
        account = next(
            (item for item in self._store.accounts(user_id=user_id)
             if str(item.get("id") or "") == account_ref), None)
        if account is None:
            raise ContactStartError("contact_account_not_found")
        if not bool(account.get("enabled")):
            raise ContactStartError("contact_account_disabled")
        if need_send and "reply" not in set(account.get("capabilities") or ()):
            raise ContactStartError("contact_account_not_reply_capable")
        return account

    # -- resolution ---------------------------------------------------------------

    def resolve(self, *, user_id: str, account_ref: str, identifier: str,
                need_send: bool = True) -> ResolvedContact:
        """Resolve a human identifier into a provider-native peer."""
        identifier = str(identifier or "").strip()
        if not identifier:
            raise ContactStartError("contact_identifier_required")
        account = self.account(user_id=user_id, account_ref=account_ref, need_send=need_send)
        provider = str(account.get("provider") or "").strip().lower()
        resolver = getattr(self, f"_resolve_{provider}", None)
        if resolver is None:
            raise ContactStartError(f"contact_resolution_unsupported:{provider or 'unknown'}")
        return resolver(account, identifier, user_id=user_id)

    def _resolve_gmail(self, account: dict[str, Any], identifier: str, *,
                       user_id: str) -> ResolvedContact:
        address = mail_address(identifier) or identifier.strip().casefold()
        if not _MAIL_ADDRESS.fullmatch(address):
            raise ContactStartError("contact_mail_address_required")
        alias = str(account.get("credential_ref") or "").removeprefix("himalaya:")
        source = "gmail" if alias == "gmail" else f"gmail:{alias}"
        return ResolvedContact(
            provider="gmail", account_ref=str(account["id"]), source=source,
            peer_id=address, display_name=address, route_id="gmail-read-only",
            identifier=identifier, detail={"address": address},
        )

    def _resolve_sms(self, account: dict[str, Any], identifier: str, *,
                     user_id: str) -> ResolvedContact:
        from .channels.sms_gateway import sms_address

        digits = re.sub(r"[^\d]", "", identifier)
        if not _PHONE.fullmatch(digits):
            raise ContactStartError("contact_phone_required")
        address = sms_address("+" + digits)
        native = str(account.get("credential_ref") or "") == "sms-native:default"
        return ResolvedContact(
            provider="sms", account_ref=str(account["id"]), source="sms",
            peer_id=address, display_name=address, route_id="sms",
            identifier=identifier, native_sms=native, detail={"address": address},
        )

    def _resolve_whatsapp(self, account: dict[str, Any], identifier: str, *,
                          user_id: str) -> ResolvedContact:
        digits = re.sub(r"[^\d]", "", identifier)
        if not _PHONE.fullmatch(digits):
            raise ContactStartError("contact_phone_required")
        return ResolvedContact(
            provider="whatsapp", account_ref=str(account["id"]), source="whatsapp",
            peer_id=digits, display_name=digits, route_id="whatsapp-reply",
            identifier=identifier, detail={"address": digits},
        )

    def _resolve_telegram(self, account: dict[str, Any], identifier: str, *,
                          user_id: str) -> ResolvedContact:
        outbox = getattr(self._service, "telegram_outbox", None)
        resolve = getattr(outbox, "resolve_contact", None)
        if not callable(resolve):
            raise ContactStartError("contact_resolution_unsupported:telegram")
        value = identifier.strip().lstrip("@")
        try:
            found = resolve(value, account_ref=str(account["id"]))
        except ContactStartError:
            raise
        except ValueError as error:
            raise ContactStartError(str(error) or "contact_telegram_not_resolved") from None
        peer_id = str((found or {}).get("peer_id") or "")
        if not peer_id.lstrip("-").isdigit():
            raise ContactStartError("contact_telegram_not_resolved")
        name = str((found or {}).get("name") or "").strip()
        username = str((found or {}).get("username") or "").strip()
        return ResolvedContact(
            provider="telegram", account_ref=str(account["id"]), source="telegram",
            peer_id=peer_id, display_name=name or (f"@{username}" if username else peer_id),
            route_id="telegram", identifier=identifier,
            detail={"username": username, "phone": str((found or {}).get("phone") or "")},
        )

    def _resolve_vk(self, account: dict[str, Any], identifier: str, *,
                    user_id: str) -> ResolvedContact:
        from .vk_web import VKWeb

        value = identifier.strip()
        if value.startswith(("https://", "http://")):
            parsed = urlsplit(value)
            if parsed.hostname not in {"vk.ru", "vk.com", "www.vk.ru", "www.vk.com"}:
                raise ContactStartError("contact_vk_link_required")
            value = parsed.path.strip("/")
        kind = "vk_user_id" if value.isdigit() else "vk_username"
        try:
            identity = VKWeb(self._service, user_id).resolve_catalog_contact(
                str(account["id"]), kind, value)
        except ValueError as error:
            raise ContactStartError(str(error) or "contact_vk_not_resolved") from None
        return ResolvedContact(
            provider="vk", account_ref=identity.account_ref, source="vk",
            peer_id=identity.peer_id,
            display_name=identity.display_name or identity.peer_id,
            route_id=identity.route_id, identifier=identifier,
        )

    def _resolve_yandex(self, account: dict[str, Any], identifier: str, *,
                        user_id: str) -> ResolvedContact:
        from .yandex.web import YandexWeb

        value = identifier.strip()
        if value.startswith("https://"):
            parsed = urlsplit(value)
            if parsed.hostname != "telemost.yandex.ru" or not parsed.path.startswith("/user/"):
                raise ContactStartError("contact_yandex_link_required")
            value = parsed.path.removeprefix("/user/")
        try:
            identity = YandexWeb(self._service, user_id).resolve_catalog_contact(
                str(account["id"]), "yandex_user_guid", value)
        except ValueError as error:
            raise ContactStartError(str(error) or "contact_yandex_not_resolved") from None
        return ResolvedContact(
            provider="yandex", account_ref=identity.account_ref, source="yandex",
            peer_id=identity.peer_id,
            display_name=identity.display_name or identity.peer_id,
            route_id=identity.route_id, identifier=identifier,
        )

    # -- conversation -------------------------------------------------------------

    def conversation(self, resolved: ResolvedContact, *, user_id: str) -> dict[str, Any]:
        """Create or reuse the canonical conversation of the resolved peer."""
        conversation = self._store.ensure_contact_conversation(
            resolved.source,
            resolved.display_name or resolved.peer_id,
            user_id=user_id,
            account_ref=resolved.account_ref,
            peer_id=resolved.peer_id,
            conversation_kind=resolved.peer_kind,
            route_id=resolved.route_id or None,
            native_sms=resolved.native_sms,
        )
        if resolved.provider == "gmail":
            # A mail thread is keyed by the whole From header; the bare address
            # keeps the contact's answer inside this very chat.
            self._store.bind_contact_address(
                user_id=user_id,
                address=str(resolved.detail.get("address") or resolved.peer_id),
                conversation_id=str(conversation["id"]),
            )
        return conversation

    def start(self, *, user_id: str, account_ref: str, identifier: str, text: str,
              request_id: str) -> dict[str, Any]:
        """Resolve the contact, open the chat and deliver the first message."""
        body = str(text or "").strip()
        if not body:
            raise ContactStartError("contact_message_required")
        resolved = self.resolve(user_id=user_id, account_ref=account_ref, identifier=identifier)
        conversation = self.conversation(resolved, user_id=user_id)
        draft = self._service.send_manual_message(
            str(conversation["id"]), body=body, request_id=request_id, user_id=user_id)
        return {
            "ok": True,
            "conversation_id": str(conversation["id"]),
            "channel": str(conversation.get("source") or resolved.source),
            "peer": resolved.as_dict(),
            "draft_id": draft.id,
            "status": draft.status,
        }
