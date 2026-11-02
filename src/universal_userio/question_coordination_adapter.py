"""Adapter between the existing UserIO SQLite journals and question coordination.

This is not a transport, daemon, policy engine or caller authentication scheme.
Production construction requires live, independently verified actor and grants.
"""
from __future__ import annotations

import hashlib
import json
from typing import Callable

from .question_coordination import AccessDenied, Actor, Binding, QuestionCoordination, Rejected


class ExistingUserIOQuestionJournal:
    """Use the original outgoing action/draft and its provider receipt only."""

    def __init__(self, store):
        self.store = store

    def _messenger(self, src: dict, request_id: str):
        if not request_id.startswith("messenger:") or not self.store._table_exists("messenger_actions"):
            return None
        original_id = request_id.removeprefix("messenger:")
        row = self.store._connection.execute("""
            SELECT id,request_id,envelope,status,receipt FROM messenger_actions
            WHERE user_id=? AND request_id=?
        """, (src["user_id"], original_id)).fetchone()
        if row is None:
            return None
        try:
            envelope = json.loads(row["envelope"])
        except (TypeError, ValueError):
            raise Rejected("invalid_existing_action") from None
        # UserIO does not store a provider_message_id column. Its canonical
        # Telegram source key embeds the verified account, peer and provider ID.
        message_id = str(src["message_id"])
        prefix = str(src["account_ref"]) + "|" + str(src["peer_id"]) + ":"
        legacy_prefix = str(src["peer_id"]) + ":"
        provider_id = (message_id[len(prefix):] if message_id.startswith(prefix)
                       else message_id[len(legacy_prefix):]
                       if src["source"] == "telegram" and message_id.startswith(legacy_prefix)
                       else message_id if src["source"] not in {"telegram", "vk", "max"} else "")
        if src["source"] in {"telegram", "vk", "max"} and not (
            provider_id.isdigit() and provider_id and int(provider_id) > 0
        ):
            raise AccessDenied("original_provider_identity_unavailable")
        if (envelope.get("user_id") != src["user_id"]
                or envelope.get("account_ref") != src["account_ref"]
                or envelope.get("conversation_id") != src["conversation_id"]
                or envelope.get("source_message_id") not in (
                    src["message_id"], provider_id,
                )
                or envelope.get("action") not in {"reply", "edit"}):
            raise AccessDenied("action_source_binding_mismatch")
        return row

    def _draft(self, src: dict, request_id: str):
        if not request_id.startswith("draft:"):
            return None
        draft_id = request_id.removeprefix("draft:")
        row = self.store._connection.execute("""
            SELECT d.id,d.status,d.outbox_receipt,d.body,
                   o.peer_id,o.source_body_sha256
            FROM drafts d
            JOIN draft_reply_origins o ON o.user_id=d.user_id AND o.draft_id=d.id
            WHERE d.user_id=? AND d.id=? AND d.conversation_id=?
              AND o.conversation_id=? AND o.account_ref=? AND o.message_key=?
        """, (src["user_id"], draft_id, src["conversation_id"],
              src["conversation_id"], src["account_ref"], src["message_id"])).fetchone()
        if row is None:
            return None
        original = self.store._connection.execute("""
            SELECT body,deleted_at FROM messages
            WHERE user_id=? AND conversation_id=? AND source='telegram'
              AND message_id=?
        """, (src["user_id"], src["conversation_id"], src["message_id"])).fetchone()
        if (row["peer_id"] != src["peer_id"] or original is None
                or original["deleted_at"] is not None
                or row["source_body_sha256"] != hashlib.sha256(
                    str(original["body"]).encode()).hexdigest()):
            raise AccessDenied("immutable_draft_source_changed")
        return row

    def _original(self, src: dict, request_id: str):
        if request_id.startswith("messenger:"):
            return "messenger", self._messenger(src, request_id)
        if request_id.startswith("draft:"):
            return "draft", self._draft(src, request_id)
        raise Rejected("existing_journal_operation_required")

    def reserve(self, db, src: dict, request_id: str) -> None:
        if db is not self.store._connection:
            raise Rejected("journal_database_mismatch")
        _, row = self._original(src, request_id)
        if row is None:
            raise Rejected("original_operation_not_found")
        if row["status"] != "proposed":
            raise Rejected("original_operation_already_started")

    def read(self, src: dict, request_id: str) -> dict[str, str] | None:
        kind, row = self._original(src, request_id)
        if row is None:
            return None
        if kind == "messenger":
            if row["status"] == "accepted" and row["receipt"]:
                return {"state": "sent", "receipt_ref": "messenger-action:" + row["id"]}
            return {"state": "unknown"}
        if row["status"] == "approved" and row["outbox_receipt"]:
            return {"state": "sent", "receipt_ref": "draft:" + row["id"]}
        return {"state": "unknown"}

    def result_exists(self, src: dict, result_ref: str) -> bool:
        if result_ref.startswith("triage:"):
            if result_ref != "triage:" + str(src["seq"]):
                return False
            row = self.store._connection.execute("""
                SELECT status,result_json FROM workspace_triage
                WHERE user_id=? AND event_seq=?
            """, (src["user_id"], src["seq"])).fetchone()
            return bool(row and row["status"] == "completed" and row["result_json"])
        try:
            _, row = self._original(src, result_ref)
        except (AccessDenied, Rejected):
            return False
        return row is not None


def bind_question_coordination(
    store, *, tenant: str, token_key: bytes,
    verified_actor: Callable[[object], Actor],
    permitted_holders: Callable[[Actor, dict], frozenset[str]],
    human_approval_and_send_grant: Callable[[Actor, dict, str], bool],
) -> QuestionCoordination:
    """Requires authenticated controller callbacks; all default-deny."""
    journal = ExistingUserIOQuestionJournal(store)

    def binding_for(actor: Actor, source: dict) -> Binding:
        if actor.user_id != source["user_id"]:
            raise AccessDenied("account_owner_mismatch")
        if not source["account_ref"] or not source["route_id"]:
            raise AccessDenied("verified_route_required")
        if not store.route_allowed(
            user_id=actor.user_id, source=source["source"], route_id=source["route_id"]
        ):
            raise AccessDenied("route_revoked")
        record = store._connection.execute("""
            SELECT id,account_ref,route_id,peer_id FROM conversations
            WHERE user_id=? AND id=?
        """, (actor.user_id, source["conversation_id"])).fetchone()
        if (record is None or record["account_ref"] != source["account_ref"]
                or record["route_id"] != source["route_id"]
                or record["peer_id"] != source["peer_id"]):
            raise AccessDenied("saved_binding_mismatch")
        holders = permitted_holders(actor, source)
        if not isinstance(holders, frozenset) or not holders <= {"dot", "manager"}:
            raise AccessDenied("verified_holder_binding_required")
        return Binding(
            project="manager",
            binding_id="userio:" + actor.user_id + ":" + record["id"] + ":" +
                       record["account_ref"] + ":" + record["route_id"],
            permitted_holders=holders,
        )

    def authorize_delivery(actor: Actor, source: dict, request_id: str) -> bool:
        if not store.capability_enabled("send", user_id=actor.user_id):
            return False
        if not store.route_allowed(user_id=actor.user_id,
                                   source=source["source"], route_id=source["route_id"]):
            return False
        return human_approval_and_send_grant(actor, source, request_id) is True

    return QuestionCoordination(
        store._connection, store._lock, tenant=tenant, token_key=token_key,
        resolve_actor=verified_actor, resolve_binding=binding_for,
        journal_reserve=journal.reserve, journal_read=journal.read,
        result_exists=journal.result_exists, delivery_authorized=authorize_delivery,
    )
