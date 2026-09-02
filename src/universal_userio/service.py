"""Business use cases; AI proposes and approval is the only send authority."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence

from .contracts import DraftGenerator, InboxMessage, OutboxClient, ReplyDraft
from .store import SQLiteUserIOStore


class DeliveryUnavailableError(ValueError):
    """The configured source intentionally has no outbound delivery capability."""


class UserIOService:
    def __init__(
        self, store: SQLiteUserIOStore, generator: DraftGenerator, outbox: OutboxClient,
        *, sms_gateway: object | None = None, sms_user_id: str = "", sms_route_id: str = "sms",
        gmail_outbox: object | None = None,
        telegram_outbox: object | None = None,
    ) -> None:
        self._store = store
        self._generator = generator
        self._outbox = outbox
        self.sms_gateway, self.sms_user_id, self.sms_route_id = sms_gateway, sms_user_id, sms_route_id
        self.gmail_outbox = gmail_outbox
        self.telegram_outbox = telegram_outbox

    @staticmethod
    def conversation_id(message: InboxMessage, *, user_id: str = "") -> str:
        key = f"{user_id}\0{message.conversation_key}" if user_id else message.conversation_key
        return "conv_" + hashlib.sha256(key.encode()).hexdigest()[:24]

    def receive(self, message: InboxMessage, *, route_id: str, user_id: str | None = None) -> tuple[str, bool]:
        user_id = self._store.default_user_id if user_id is None else user_id
        conversation_id = self._store.conversation_id_for_key(
            message.conversation_key, user_id=user_id
        ) or self.conversation_id(message, user_id=user_id)
        policy = self._store.policy_for(message, fallback_route_id=route_id, user_id=user_id)
        if not self._store.route_allowed(
            user_id=user_id, source=message.source, route_id=policy.route_id
        ):
            raise ValueError("route is not assigned to user")
        return conversation_id, self._store.ingest(
            message, conversation_id=conversation_id, policy=policy, user_id=user_id
        )

    def receive_and_plan(
        self, message: InboxMessage, *, route_id: str, user_id: str | None = None
    ) -> tuple[str, bool, ReplyDraft | None]:
        user_id = self._store.default_user_id if user_id is None else user_id
        conversation_id, accepted = self.receive(message, route_id=route_id, user_id=user_id)
        if not accepted:
            return conversation_id, False, None
        draft = self.propose(conversation_id, message, user_id=user_id)
        conversation = self._store.conversation(conversation_id, user_id=user_id)
        if conversation and conversation["response_mode"] == "auto_send":
            draft = self.approve(draft.id, user_id=user_id)
        return conversation_id, True, draft

    def propose(
        self, conversation_id: str, message: InboxMessage, *, user_id: str | None = None
    ) -> ReplyDraft:
        return self.propose_variants(conversation_id, message, limit=1, user_id=user_id)[0]

    def propose_from_conversation(
        self, conversation_id: str, *, limit: int = 3, user_id: str | None = None
    ) -> list[ReplyDraft]:
        """Run the opt-in AI action against the latest stored inbound message."""
        conversation = self._store.conversation(conversation_id, user_id=user_id)
        if conversation is None:
            raise KeyError("conversation not found")
        messages = list(conversation["messages"])
        if not messages:
            raise ValueError("conversation has no messages")
        latest = messages[-1]
        message = InboxMessage(
            source=str(latest["source"]), message_id=str(latest["message_id"]), sender=str(latest["sender"]),
            body=str(latest["body"]), received_at=float(latest["received_at"]),
        )
        return self.propose_variants(conversation_id, message, limit=limit, user_id=user_id)

    def create_manual_draft(
        self, conversation_id: str, *, body: str, user_id: str | None = None
    ) -> ReplyDraft:
        if self._store.conversation(conversation_id, user_id=user_id) is None:
            raise KeyError("conversation not found")
        text = body.strip()
        if not text:
            raise ValueError("draft body is required")
        draft = ReplyDraft("draft_" + uuid.uuid4().hex, conversation_id, text, "proposed")
        self._store.add_draft(draft, user_id=user_id)
        return draft

    def propose_variants(
        self, conversation_id: str, message: InboxMessage, *, limit: int = 3,
        user_id: str | None = None,
    ) -> list[ReplyDraft]:
        if limit < 1:
            raise ValueError("draft limit must be positive")
        if self._store.conversation(conversation_id, user_id=user_id) is None:
            raise KeyError("conversation not found")
        suggest_variants = getattr(self._generator, "suggest_variants", None)
        suggest_with_context = getattr(self._generator, "suggest_with_context", None)
        generated: Sequence[str]
        record = self._store.conversation(conversation_id, user_id=user_id)
        history = [] if record is None else list(record["messages"])
        if callable(suggest_with_context):
            generated = suggest_with_context(conversation_id=conversation_id, latest_message=message, history=history, limit=limit)
        elif callable(suggest_variants):
            generated = suggest_variants(conversation_id=conversation_id, latest_message=message, limit=limit)
        else:
            generated = [self._generator.suggest(conversation_id=conversation_id, latest_message=message)]
        bodies = [str(body).strip() for body in generated if str(body).strip()][:limit]
        if not bodies:
            raise ValueError("AI produced no reply variants")
        drafts = [ReplyDraft("draft_" + uuid.uuid4().hex, conversation_id, body, "proposed") for body in bodies]
        for draft in drafts:
            self._store.add_draft(draft, user_id=user_id)
        return drafts

    def approve(self, draft_id: str, *, user_id: str | None = None) -> ReplyDraft:
        draft = self._store.draft(draft_id, user_id=user_id)
        if draft.status == "approved":
            return draft
        if draft.status != "proposed":
            raise ValueError("draft is not approvable")
        conversation = self._store.conversation(draft.conversation_id, user_id=user_id)
        if conversation is None:
            raise KeyError("conversation not found")
        user_id = self._store.default_user_id if user_id is None else user_id
        if not self._store.route_allowed(
            user_id=user_id, source=str(conversation["source"]), route_id=str(conversation["route_id"])
        ):
            raise ValueError("route is not assigned to user")
        if str(conversation["source"]).startswith("gmail:"):
            if self.gmail_outbox is None:
                raise DeliveryUnavailableError("Gmail delivery is not configured")
            account_alias = str(conversation["source"]).partition(":")[2]
            account = next((item for item in self._store.accounts(user_id=user_id) if item["id"] == f"gmail-{account_alias}"), None)
            if account is None:
                raise DeliveryUnavailableError("Gmail account is not configured")
            latest = list(conversation["messages"])[-1]
            receipt = self.gmail_outbox.send_reply(
                account=account_alias, sender=str(account["display_name"]), recipient=str(conversation["sender"]),
                message_id=str(latest["message_id"]), body=draft.body, draft_id=draft.id,
            )
        elif conversation["source"] == "chatgpt":
            send_chatgpt_reply = getattr(self._outbox, "send_chatgpt_reply", None)
            if not callable(send_chatgpt_reply):
                raise ValueError("configured outbox does not support ChatGPT delivery")
            receipt = send_chatgpt_reply(
                chat_ref=str(conversation["sender"]), draft_id=draft.id, body=draft.body
            )
        elif conversation["source"] == "sms":
            if self.sms_gateway is None or user_id != self.sms_user_id:
                raise ValueError("Android SMS adapter is not configured for this UserIO user")
            receipt = self.sms_gateway.send(to=str(conversation["sender"]), body=draft.body)
        elif conversation["source"] == "telegram" and self.telegram_outbox is not None:
            receipt = self.telegram_outbox.send_reply(
                chat=str(conversation["sender"]), body=draft.body, draft_id=draft.id
            )
        else:
            receipt = self._outbox.send_reply(
                route_id=str(conversation["route_id"]), conversation_id=draft.conversation_id,
                draft_id=draft.id, body=draft.body,
            )
        return self._store.approve(draft_id, receipt, user_id=user_id)
