"""Business use cases; AI proposes and approval is the only send authority."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import uuid
from collections.abc import Sequence

from .contracts import DraftGenerator, InboxMessage, OutboxClient, ReplyDraft
from .store import SQLiteUserIOStore


_LOG = logging.getLogger(__name__)


class DeliveryUnavailableError(ValueError):
    """The configured source intentionally has no outbound delivery capability."""


class UserIOService:
    def __init__(
        self, store: SQLiteUserIOStore, generator: DraftGenerator, outbox: OutboxClient,
        *, sms_gateway: object | None = None, sms_user_id: str = "", sms_route_id: str = "sms",
        gmail_outbox: object | None = None,
        chatgpt_outbox: object | None = None,
        telegram_outbox: object | None = None,
        draft_notifier: object | None = None,
        draft_notification_delay_seconds: float = 5.0,
    ) -> None:
        self._store = store
        self._generator = generator
        self._outbox = outbox
        self._user_generators: dict[str, object] = {}
        self.sms_gateway, self.sms_user_id, self.sms_route_id = sms_gateway, sms_user_id, sms_route_id
        self.gmail_outbox = gmail_outbox
        self.chatgpt_outbox = chatgpt_outbox
        self.telegram_outbox = telegram_outbox
        self.draft_notifier = draft_notifier
        self.draft_notification_delay_seconds = max(0.0, float(draft_notification_delay_seconds))
        self._inbound_listeners: list[object] = []

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
        accepted = self._store.ingest(
            message, conversation_id=conversation_id, policy=policy, user_id=user_id
        )
        if accepted:
            for listener in tuple(self._inbound_listeners):
                listener(user_id, conversation_id, message)
        return conversation_id, accepted

    def _chatgpt_agent_fallback(self, *, agent_id: str, text: str, chat_ref: str) -> dict:
        """Deliver through the user's real browser when the server POST is blocked."""
        from . import agent_channel
        username = self._store.owner().username
        return agent_channel.call(
            agent_id, "gpt_send", {"text": text, "chat_ref": chat_ref},
            user=username, timeout_sec=115.0,
        )

    def add_inbound_listener(self, listener: object) -> None:
        self._inbound_listeners.append(listener)

    def _notify_drafts_if_still_proposed(
        self, draft_ids: Sequence[str], *, user_id: str | None = None,
    ) -> None:
        notifier = self.draft_notifier
        if notifier is None:
            return
        resolved_user_id = self._store._user(user_id)
        proposed: list[ReplyDraft] = []
        for draft_id in draft_ids:
            try:
                draft = self._store.draft(str(draft_id), user_id=resolved_user_id)
            except KeyError:
                continue
            if draft.status == "proposed" and not self._store.draft_browser_notified(
                draft.id, user_id=resolved_user_id
            ):
                proposed.append(draft)
        if not proposed:
            return
        conversation = self._store.conversation(proposed[0].conversation_id, user_id=resolved_user_id)
        if conversation is None:
            return
        notify = getattr(notifier, "notify", None)
        if not callable(notify):
            return
        try:
            notify(user_id=resolved_user_id, conversation=conversation, drafts=proposed)
        except Exception as error:  # Notification failure must never lose the draft itself.
            _LOG.warning("draft approval notification failed for %s: %s", proposed[0].id, error)

    def _notify_drafts_for_approval(
        self, drafts: Sequence[ReplyDraft], *, user_id: str | None = None,
    ) -> None:
        draft_ids = [draft.id for draft in drafts if draft.status == "proposed"]
        if self.draft_notifier is None or not draft_ids:
            return
        if self.draft_notification_delay_seconds <= 0:
            self._notify_drafts_if_still_proposed(draft_ids, user_id=user_id)
            return
        timer = threading.Timer(
            self.draft_notification_delay_seconds,
            self._notify_drafts_if_still_proposed,
            args=(draft_ids,), kwargs={"user_id": user_id},
        )
        timer.daemon = True
        timer.start()

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
        else:
            self._notify_drafts_for_approval([draft], user_id=user_id)
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
        return self.propose_for_approval(
            conversation_id, message, limit=limit, user_id=user_id
        )

    def propose_for_approval(
        self, conversation_id: str, message: InboxMessage, *, limit: int = 3,
        user_id: str | None = None,
    ) -> list[ReplyDraft]:
        drafts = self.propose_variants(
            conversation_id, message, limit=limit, user_id=user_id
        )
        self._notify_drafts_for_approval(drafts, user_id=user_id)
        return drafts

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
        self._notify_drafts_for_approval([draft], user_id=user_id)
        return draft

    def _generator_for(self, user_id: str | None) -> object:
        """BYOK: a user's own endpoint/model/key wins over the server default."""
        resolved = self._store.default_user_id if user_id is None else user_id
        cached = self._user_generators.get(resolved)
        if cached is not None:
            return cached
        settings = self._store.ai_settings(user_id=resolved)
        if settings is None:
            return self._generator
        bridge_url = os.environ.get("USERIO_BYOK_BRIDGE_URL", "").strip()
        bridge_token = os.environ.get("USERIO_BYOK_BRIDGE_TOKEN", "").strip()
        if not bridge_url or not bridge_token:
            return self._generator
        from .adapters import ByokBridgeGenerator

        generator = ByokBridgeGenerator(
            bridge_url=bridge_url, bridge_token=bridge_token,
            endpoint=settings["endpoint"], model=settings["model"], api_key=settings["token"],
        )
        self._user_generators[resolved] = generator
        return generator

    def propose_variants(
        self, conversation_id: str, message: InboxMessage, *, limit: int = 3,
        user_id: str | None = None,
    ) -> list[ReplyDraft]:
        if limit < 1:
            raise ValueError("draft limit must be positive")
        if self._store.conversation(conversation_id, user_id=user_id) is None:
            raise KeyError("conversation not found")
        generator = self._generator_for(user_id)
        suggest_variants = getattr(generator, "suggest_variants", None)
        suggest_with_context = getattr(generator, "suggest_with_context", None)
        generated: Sequence[str]
        record = self._store.conversation(conversation_id, user_id=user_id)
        history = [] if record is None else list(record["messages"])
        if callable(suggest_with_context):
            generated = suggest_with_context(conversation_id=conversation_id, latest_message=message, history=history, limit=limit)
        elif callable(suggest_variants):
            generated = suggest_variants(conversation_id=conversation_id, latest_message=message, limit=limit)
        else:
            generated = [generator.suggest(conversation_id=conversation_id, latest_message=message)]
        bodies = [str(body).strip() for body in generated if str(body).strip()][:limit]
        if not bodies:
            raise ValueError("AI produced no reply variants")
        drafts = [ReplyDraft("draft_" + uuid.uuid4().hex, conversation_id, body, "proposed") for body in bodies]
        for draft in drafts:
            self._store.add_draft(draft, user_id=user_id)
        return drafts

    def approve(self, draft_id: str, *, user_id: str | None = None) -> ReplyDraft:
        resolved_user_id = self._store._user(user_id)
        if not self._store.send_enabled(user_id=resolved_user_id):
            raise DeliveryUnavailableError("outbound delivery is disabled by user policy")
        draft = self._store.draft(draft_id, user_id=resolved_user_id)
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
        elif str(conversation["source"]).startswith("chatgpt"):
            # chatgpt:<slug> sources carry the account; account_ref is the fallback.
            account_ref = str(conversation["source"]).partition(":")[2] or str(conversation.get("account_ref") or "")
            if self.chatgpt_outbox is not None:
                principal = self._store.user(user_id)
                if principal is None:
                    raise ValueError(f"unknown UserIO user: {user_id}")
                receipt = self.chatgpt_outbox.send_reply(
                    chat_ref=str(conversation["sender"]), draft_id=draft.id, body=draft.body,
                    account_ref=account_ref, user=principal.username,
                    agent_fallback=self._chatgpt_agent_fallback,
                )
            else:
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
            messages = list(conversation["messages"])
            chat_id = str(messages[-1]["message_id"]).partition(":")[0] if messages else ""
            receipt = self.telegram_outbox.send_reply(
                chat=str(conversation["sender"]), chat_id=chat_id, body=draft.body, draft_id=draft.id,
                account_ref=str(conversation.get("account_ref") or ""),
            )
        else:
            receipt = self._outbox.send_reply(
                route_id=str(conversation["route_id"]), conversation_id=draft.conversation_id,
                draft_id=draft.id, body=draft.body,
            )
        return self._store.approve(draft_id, receipt, user_id=user_id)
