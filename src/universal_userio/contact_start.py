"""Resolve canonical contacts and prepare durable drafts before explicit approval."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .contact_keys import mail_address

_MAIL_ADDRESS = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_PHONE = re.compile(r"[0-9]{7,15}")


class ContactStartError(ValueError):
    """The identifier cannot become a writable conversation.

    ``code`` is the machine-readable reason. A consumer must branch on it,
    never on the message text.
    """

    def __init__(self, code: str, *, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}:{detail}" if detail else code)


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

    def resolution_support(self, *, user_id: str, account_ref: str) -> dict[str, Any]:
        """Report whether this account can resolve a new peer right now.

        Resolution is a local lookup for most providers, but a Telegram
        username can only become a peer id inside the connector's own live
        session. That session can be down while the account itself looks
        healthy, so a consumer has to learn about it from capabilities
        instead of from a failed call.

        The answer is ``supported`` (the provider can do it at all),
        ``availability`` (``available``, ``unavailable`` or ``unknown``) and
        a machine-readable ``reason``.
        """
        account = next(
            (item for item in self._store.accounts(user_id=user_id)
             if str(item.get("id") or "") == str(account_ref or "")), None)
        if account is None:
            return {"supported": False, "availability": "unavailable",
                    "reason": "contact_account_not_found"}
        if not bool(account.get("enabled")):
            return {"supported": False, "availability": "unavailable",
                    "reason": "contact_account_disabled"}
        provider = str(account.get("provider") or "").strip().lower()
        if getattr(self, f"_resolve_{provider}", None) is None:
            return {"supported": False, "availability": "unavailable",
                    "reason": "contact_resolution_unsupported"}
        if provider != "telegram":
            return {"supported": True, "availability": "available", "reason": ""}
        outbox = getattr(self._service, "telegram_outbox", None)
        status = getattr(outbox, "contact_resolution_status", None)
        if not callable(status):
            return {"supported": False, "availability": "unavailable",
                    "reason": "contact_resolution_unsupported"}
        try:
            verdict = status(str(account["id"]))
        except Exception:
            return {"supported": True, "availability": "unknown",
                    "reason": "connector_status_unavailable"}
        resolvable = verdict.get("resolvable") if isinstance(verdict, dict) else None
        reason = str((verdict or {}).get("reason") or "") if isinstance(verdict, dict) else ""
        if resolvable is True:
            return {"supported": True, "availability": "available", "reason": ""}
        if resolvable is None:
            return {"supported": True, "availability": "unknown",
                    "reason": reason or "connector_status_unavailable"}
        return {"supported": False, "availability": "unavailable",
                "reason": reason or "connector_unreachable"}

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

    def _resolve_matrix(self, account: dict[str, Any], identifier: str, *,
                        user_id: str) -> ResolvedContact:
        # A literal room ID is already canonical; never join rooms or guess
        # a contact from a display name on an operator notification path.
        if not re.fullmatch(r"![^\s:]+:[^\s/]+", identifier):
            raise ContactStartError("contact_matrix_room_id_required")
        return ResolvedContact(provider="matrix", account_ref=str(account["id"]),
            source="matrix", peer_id=identifier, display_name=identifier,
            route_id="matrix-read-only", peer_kind="group", identifier=identifier)

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
        from .adapters import TelegramConnectorRefused
        from .contracts import ContactResolutionUnavailable

        outbox = getattr(self._service, "telegram_outbox", None)
        resolve = getattr(outbox, "resolve_contact", None)
        if not callable(resolve):
            raise ContactStartError("contact_resolution_unsupported:telegram")
        value = identifier.strip().lstrip("@")
        try:
            found = resolve(value, account_ref=str(account["id"]))
        except ContactStartError:
            raise
        except ContactResolutionUnavailable as error:
            raise ContactStartError(error.code) from None
        except TelegramConnectorRefused as error:
            # The connector answered before touching Telegram: it holds no
            # live session for this account.
            raise ContactStartError(error.connector_code or "account_not_connected") from None
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

    def prepare(self, *, user_id: str, text: str, request_id: str,
                conversation_id: str = '', account_ref: str = '', identifier: str = '',
                notify: bool = False, reply_to: Any = None) -> dict[str, Any]:
        """Canonical recipient first, then one durable draft. Never send here.

        Prepare is a local compose step: it stores exactly one draft and returns
        it for review. Nothing reaches the provider through it - not the draft
        itself and not an owner notification - because the only delivery path is
        the reviewed approval of messages.send. An explicit notify=True asks for
        the owner's approval notice; no public compose route asks for it.
        """
        body = str(text or '').strip()
        if not body:
            raise ContactStartError('contact_message_required')
        if not isinstance(request_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,80}',request_id):
            raise ContactStartError('request_id_required')
        if bool(conversation_id) == bool(identifier) or identifier and not account_ref:
            raise ContactStartError('provide_conversation_id_or_account_and_identifier')
        peer = None
        if conversation_id:
            conversation_id = self._store.resolve_conversation_id(conversation_id, user_id=user_id)
            conversation = self._store.conversation(conversation_id, user_id=user_id)
            if conversation is None:
                raise ContactStartError('conversation_not_found')
            self.account(user_id=user_id,account_ref=conversation.get('account_ref'))
            if account_ref and conversation.get('account_ref') != account_ref:
                raise ContactStartError('contact_account_conflict')
        else:
            resolved = self.resolve(user_id=user_id, account_ref=account_ref, identifier=identifier)
            conversation = self.conversation(resolved, user_id=user_id)
            peer = resolved.as_dict()
        # The reply anchor is the source-bound object that names the incoming
        # message this draft answers. It is never defaulted: without it the
        # message is sent as an ordinary message. A malformed anchor is
        # refused, never quietly dropped.
        anchor = reply_to if reply_to else None
        draft = self._service.prepare_manual_message(str(conversation['id']), body=body,
            request_id=request_id, user_id=user_id, notify=notify, reply_to=anchor)
        from .delivery_receipt import delivery_fields
        return {'ok':True, 'conversation_id':str(conversation['id']),
            'channel':str(conversation.get('source') or ''), 'peer':peer,
            'draft_id':draft.id, 'body':draft.body, 'status':draft.status,
            'reply_to':anchor,
            'approval_required':draft.status=='proposed', **delivery_fields(draft.receipt or '')}
