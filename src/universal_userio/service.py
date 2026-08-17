"""Business use cases; AI proposes and approval is the only send authority."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence

from .contracts import DraftGenerator, InboxMessage, OutboxClient, ReplyDraft
from .store import SQLiteUserIOStore


class UserIOService:
    def __init__(self, store: SQLiteUserIOStore, generator: DraftGenerator, outbox: OutboxClient) -> None:
        self._store = store
        self._generator = generator
        self._outbox = outbox

    @staticmethod
    def conversation_id(message: InboxMessage) -> str:
        return "conv_" + hashlib.sha256(message.conversation_key.encode()).hexdigest()[:24]

    def receive(self, message: InboxMessage, *, route_id: str) -> tuple[str, bool]:
        conversation_id = self.conversation_id(message)
        policy = self._store.policy_for(message, fallback_route_id=route_id)
        return conversation_id, self._store.ingest(message, conversation_id=conversation_id, policy=policy)

    def receive_and_plan(self, message: InboxMessage, *, route_id: str) -> tuple[str, bool, ReplyDraft | None]:
        conversation_id, accepted = self.receive(message, route_id=route_id)
        if not accepted:
            return conversation_id, False, None
        draft = self.propose(conversation_id, message)
        conversation = self._store.conversation(conversation_id)
        if conversation and conversation["response_mode"] == "auto_send":
            draft = self.approve(draft.id)
        return conversation_id, True, draft

    def propose(self, conversation_id: str, message: InboxMessage) -> ReplyDraft:
        return self.propose_variants(conversation_id, message, limit=1)[0]

    def propose_variants(self, conversation_id: str, message: InboxMessage, *, limit: int = 3) -> list[ReplyDraft]:
        if limit < 1:
            raise ValueError("draft limit must be positive")
        if self._store.conversation(conversation_id) is None:
            raise KeyError("conversation not found")
        suggest_variants = getattr(self._generator, "suggest_variants", None)
        generated: Sequence[str]
        if callable(suggest_variants):
            generated = suggest_variants(conversation_id=conversation_id, latest_message=message, limit=limit)
        else:
            generated = [self._generator.suggest(conversation_id=conversation_id, latest_message=message)]
        bodies = [str(body).strip() for body in generated if str(body).strip()][:limit]
        if not bodies:
            raise ValueError("AI produced no reply variants")
        drafts = [ReplyDraft("draft_" + uuid.uuid4().hex, conversation_id, body, "proposed") for body in bodies]
        for draft in drafts:
            self._store.add_draft(draft)
        return drafts

    def approve(self, draft_id: str) -> ReplyDraft:
        draft = self._store.draft(draft_id)
        if draft.status == "approved":
            return draft
        if draft.status != "proposed":
            raise ValueError("draft is not approvable")
        conversation = self._store.conversation(draft.conversation_id)
        if conversation is None:
            raise KeyError("conversation not found")
        receipt = self._outbox.send_reply(
            route_id=str(conversation["route_id"]), conversation_id=draft.conversation_id, draft_id=draft.id, body=draft.body
        )
        return self._store.approve(draft_id, receipt)
