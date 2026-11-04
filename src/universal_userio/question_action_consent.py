"""Original UserIO Messenger action consent, owned by the existing user session.

This is NOT a second delivery journal or provider fence. The source-bound
Messenger action, question epoch, and original outgoing receipt remain
authoritative. A stored consent is only a prerequisite to that existing fence.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time

from .messenger import Messenger, digest
from .question_coordination import AccessDenied, Busy, Rejected


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class QuestionActionConsent:
    """Persist immutable human approval for one proposed original reply."""

    def __init__(self, store, service, runtime):
        self.store = store
        self.service = service
        self.runtime = runtime
        with store._lock, store._connection:
            store._connection.execute("""
                CREATE TABLE IF NOT EXISTS question_action_consents (
                    user_id TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    question_id TEXT NOT NULL,
                    question_epoch INTEGER NOT NULL,
                    action_hash TEXT NOT NULL,
                    body_sha256 TEXT NOT NULL,
                    source_message_id TEXT NOT NULL,
                    approved_by TEXT NOT NULL,
                    approved_at REAL NOT NULL,
                    approval_ref TEXT NOT NULL,
                    PRIMARY KEY(user_id, action_id, question_epoch)
                )
            """)
        runtime.human_consent = self

    def _owner(self, principal):
        if (principal is None or principal.service_account
                or principal.read_only or principal.role != "owner"
                or principal.user_id != self.store.default_user_id):
            raise AccessDenied("interactive_owner_approval_required")
        if principal.messenger_accounts is not None or principal.messenger_grants is not None:
            raise AccessDenied("unrestricted_owner_session_required")
        if not self.store.capability_enabled("send", user_id=principal.user_id):
            raise AccessDenied("current_send_grant_required")

    def _snapshot(self, principal, action_id):
        self._owner(principal)
        if not isinstance(action_id, str) or not action_id.startswith("action_"):
            raise Rejected("exact_original_action_id_required")
        messenger = Messenger(self.store, self.service, principal)
        action = messenger.get(action_id)
        if action["status"] != "proposed":
            raise Busy("original_action_is_not_proposed")
        env = action["envelope"]
        if env.get("action") != "reply" or env.get("action_id") != action_id:
            raise Rejected("only_original_reply_requires_consent")
        question = self.runtime._question_for_action(principal, env)
        if question is None:
            raise Rejected("question_not_claimed_for_original_action")
        seq, revision = question
        _actor, source = self.runtime.coordinator._scope(principal, seq, revision)
        with self.store._lock:
            row = self.store._connection.execute("""
                SELECT holder,epoch,state,delivery_state,result_ref
                FROM question_coordination WHERE question_id=?
            """, (source["question_id"],)).fetchone()
        if (row is None or row["state"] != "active"
                or row["delivery_state"] != "none"
                or row["result_ref"] != "messenger:" + action_id):
            raise Busy("question_action_not_ready_for_human_consent")
        messenger._allow(env["account_ref"], "reply")
        conv = messenger._conversation(env["account_ref"], env["conversation_id"])
        messenger._source_still_current(conv, env)
        body = env.get("payload", {}).get("text")
        if not isinstance(body, str) or not body.strip():
            raise Rejected("exact_nonempty_reply_required")
        return {
            "user_id": source["user_id"],
            "question_id": source["question_id"],
            "event_seq": seq,
            "revision": revision,
            "epoch": int(row["epoch"]),
            "holder": str(row["holder"]),
            "account_ref": env["account_ref"],
            "conversation_id": env["conversation_id"],
            "source_message_id": env["source_message_id"],
            "peer_id": env["peer_id"],
            "action_id": action_id,
            "action_hash": action["hash"],
            "body": body,
            "source_body": str(self.store.message(
                env["source_message_id"], source=source["source"],
                user_id=source["user_id"])["body"]),
            "body_sha256": _digest_text(body),
        }

    def preview(self, principal, action_id):
        """No send; human UI shows the exact source and outgoing body."""
        return self._snapshot(principal, action_id)

    def record(self, principal, *, action_id, expected_hash, expected_body_sha256,
               question_id, epoch):
        """Only called after an authenticated owner-session UI confirmation."""
        snap = self._snapshot(principal, action_id)
        if (type(epoch) is not int or epoch != snap["epoch"]
                or question_id != snap["question_id"]
                or not isinstance(expected_hash, str)
                or not hmac.compare_digest(expected_hash, snap["action_hash"])
                or not isinstance(expected_body_sha256, str)
                or not hmac.compare_digest(expected_body_sha256, snap["body_sha256"])):
            raise Rejected("human_consent_action_snapshot_changed")
        with self.store._lock, self.store._connection:
            self.store._connection.execute("BEGIN IMMEDIATE")
            # Require the same epoch and no provider attempt at commit time.
            row = self.store._connection.execute("""
                SELECT holder,epoch,state,delivery_state,result_ref
                FROM question_coordination WHERE question_id=?
            """, (question_id,)).fetchone()
            action = self.store._connection.execute("""
                SELECT hash,status,envelope FROM messenger_actions
                WHERE user_id=? AND id=?
            """, (snap["user_id"], action_id)).fetchone()
            original_message = self.store._connection.execute("""
                SELECT body,edited_at FROM messages
                WHERE user_id=? AND source='telegram' AND message_id=?
            """, (snap["user_id"], snap["source_message_id"])).fetchone()
            expected_revision = snap["revision"]
            observed_revision = (str(original_message["edited_at"])
                if original_message is not None and original_message["edited_at"] is not None
                else "0")
            if (row is None or int(row["epoch"]) != epoch or row["state"] != "active"
                    or original_message is None
                    or observed_revision != expected_revision
                    or original_message["body"] != snap["source_body"]
                    or row["delivery_state"] != "none"
                    or row["result_ref"] != "messenger:" + action_id
                    or action is None or action["status"] != "proposed"
                    or action["hash"] != expected_hash
                    or digest(json.loads(action["envelope"])) != action["hash"]):
                raise Busy("human_consent_snapshot_changed")
            existing = self.store._connection.execute("""
                SELECT approval_ref,action_hash,body_sha256
                FROM question_action_consents
                WHERE user_id=? AND action_id=? AND question_epoch=?
            """, (snap["user_id"], action_id, epoch)).fetchone()
            if existing is not None:
                if (existing["action_hash"] != expected_hash
                        or existing["body_sha256"] != expected_body_sha256):
                    raise Rejected("original_human_consent_conflict")
                return {"approval_ref": existing["approval_ref"],
                        "action_id": action_id, "epoch": epoch, "status": "approved",
                        "duplicate": True}
            approval_ref = "qca_" + secrets.token_hex(16)
            self.store._connection.execute("""
                INSERT INTO question_action_consents(
                    user_id,action_id,question_id,question_epoch,
                    action_hash,body_sha256,source_message_id,approved_by,
                    approved_at,approval_ref)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (snap["user_id"], action_id, question_id, epoch,
                  expected_hash, expected_body_sha256,
                  snap["source_message_id"], principal.username,
                  time.time(), approval_ref))
        return {"approval_ref": approval_ref, "action_id": action_id,
                "epoch": epoch, "status": "approved", "duplicate": False}

    def verify_before_provider(self, principal, envelope, question_id, epoch):
        """Same existing Messenger.approve fence, never a new send permission."""
        action_id = envelope.get("action_id")
        body = envelope.get("payload", {}).get("text")
        if not isinstance(body, str) or not isinstance(action_id, str):
            raise AccessDenied("original_human_consent_required")
        with self.store._lock:
            action = self.store._connection.execute("""
                SELECT hash,status FROM messenger_actions
                WHERE user_id=? AND id=?
            """, (principal.user_id, action_id)).fetchone()
            approved = self.store._connection.execute("""
                SELECT action_hash,body_sha256,source_message_id,approved_by
                FROM question_action_consents
                WHERE user_id=? AND action_id=? AND question_id=?
                  AND question_epoch=?
            """, (principal.user_id, action_id, question_id, epoch)).fetchone()
        if (action is None or action["status"] != "proposed"
                or action["hash"] != digest(envelope)
                or approved is None
                or not approved["approved_by"]
                or approved["source_message_id"] != envelope.get("source_message_id")
                or action["hash"] != approved["action_hash"]
                or not hmac.compare_digest(_digest_text(body), approved["body_sha256"])):
            raise AccessDenied("original_human_consent_required")
        return True

    def read_record(self, principal, action_id, epoch):
        """Read-only auditable consent reference, never a synthetic receipt."""
        self._owner(principal)
        with self.store._lock:
            row = self.store._connection.execute("""
                SELECT approval_ref,question_id,question_epoch,action_id,
                       approved_by,approved_at
                FROM question_action_consents
                WHERE user_id=? AND action_id=? AND question_epoch=?
            """, (principal.user_id, action_id, epoch)).fetchone()
        return dict(row) if row else None

    def send_approved_original(self, principal, *, action_id, expected_hash,
                               event_seq, revision, epoch, lease_token):
        """Dot-only callback; delegates to the existing Messenger provider fence.

        A repeated accepted call is read-only. An UNKNOWN is never sent again.
        """
        actor = self.runtime._actor(principal)
        if actor.holder != "dot":
            raise AccessDenied("bound_dot_service_principal_required")
        if (type(event_seq) is not int or type(epoch) is not int
                or not isinstance(revision, str)
                or not isinstance(lease_token, str)
                or not isinstance(expected_hash, str)):
            raise Rejected("exact_original_question_proof_required")
        messenger = Messenger(self.store, self.service, principal)
        action = messenger.get(action_id)
        if not hmac.compare_digest(action["hash"], expected_hash):
            raise Rejected("original_action_hash_mismatch")
        env = action["envelope"]
        question = self.runtime._question_for_action(principal, env)
        if question != (event_seq, revision):
            raise Rejected("original_question_source_mismatch")
        _actor, source = self.runtime.coordinator._scope(
            principal, event_seq, revision)
        self.runtime.coordinator._assert_owner(
            self.runtime.coordinator._existing(source),
            actor, lease_token, epoch)
        with self.store._lock:
            current = self.store._connection.execute("""
                SELECT state,delivery_state,result_ref,delivery_request_id,
                       receipt_ref,epoch
                FROM question_coordination WHERE question_id=?
            """, (source["question_id"],)).fetchone()
        if current is None or current["result_ref"] != "messenger:" + action_id:
            raise Rejected("original_action_not_bound_to_question")
        if action["status"] == "sending":
            raise Busy("delivery_uncertain_do_not_retry")
        if action["status"] == "proposed":
            self.verify_before_provider(
                principal, env, source["question_id"], epoch)
            messenger.approve(action_id, expected_hash, True, question={
                "event_seq": event_seq, "revision": revision,
                "epoch": epoch, "lease_token": lease_token,
            })
        elif action["status"] != "accepted":
            raise Rejected("original_action_not_approved")
        # Pure readback of the SAME action. Messenger.approve already recorded
        # the receipt; if that projection failed, reconcile without resending.
        return self.project_original_receipt(
            principal, action_id, event_seq, revision, epoch)

    def project_original_receipt(self, principal, action_id, event_seq,
                                 revision, epoch):
        actor, src = self.runtime.coordinator._scope(
            principal, event_seq, revision)
        if actor.holder != "dot":
            raise AccessDenied("bound_dot_service_principal_required")
        if self.runtime._question_for_action(
                principal, Messenger(self.store, self.service, principal)
                .get(action_id)["envelope"]) != (event_seq, revision):
            raise AccessDenied("original_action_source_changed")
        with self.store._lock:
            row = self.store._connection.execute("""
                SELECT holder,epoch,delivery_state,delivery_request_id,
                       receipt_ref,result_ref
                FROM question_coordination WHERE question_id=?
            """, (src["question_id"],)).fetchone()
        if (row is None or row["holder"] != "dot" or row["epoch"] != epoch
                or row["result_ref"] != "messenger:" + action_id):
            raise Rejected("original_question_lease_changed")
        messenger = Messenger(self.store, self.service, principal)
        item = messenger.get(action_id)
        env = item["envelope"]
        if item["status"] != "accepted" or not item["receipt"]:
            raise Busy("original_delivery_unknown")
        # Acceptance alone is not proof of a user's exact-action decision.
        # Older receipts without a matching human admission cannot be
        # presented to Mirror as an approved question response.
        with self.store._lock:
            consent = self.store._connection.execute("""
                SELECT action_hash,body_sha256,source_message_id,approved_by
                FROM question_action_consents
                WHERE user_id=? AND action_id=? AND question_id=?
                  AND question_epoch=?
            """, (principal.user_id, action_id, src["question_id"], epoch)).fetchone()
        if (consent is None or not consent["approved_by"]
                or consent["action_hash"] != item["hash"]
                or consent["source_message_id"] != env.get("source_message_id")
                or not hmac.compare_digest(
                    str(consent["body_sha256"]), _digest_text(
                        str(env.get("payload", {}).get("text", ""))))):
            raise AccessDenied("original_human_consent_required")
        if (row["delivery_state"] != "sent"
                or row["receipt_ref"] != "messenger:" + action_id):
            # Reconcile, never approve again. A prior receipt read ID can
            # cache UNKNOWN, hence a new stable ID is scoped to this read.
            reconciliation = self.runtime.coordinator.reconcile_delivery(
                principal, event_seq, revision, "receipt-read:" + secrets.token_hex(12))
            if reconciliation["delivery_state"] != "sent":
                raise Busy("original_delivery_unknown")
        if row["delivery_request_id"] != env["request_id"]:
            raise Rejected("original_outgoing_request_changed")
        receipt = item["receipt"]
        provider_id = receipt.get("provider_message_id")
        if (not isinstance(provider_id, str) or not provider_id.isdigit()
                or int(provider_id) <= 0 or not receipt.get("receipt")
                or receipt.get("account_ref") != env["account_ref"]
                or str(receipt.get("peer_id")) != str(env["peer_id"])):
            raise Rejected("original_provider_receipt_identity_mismatch")
        source_id = str(env["source_message_id"])
        original_id = str(env.get("source_provider_message_id") or "")
        if (source_id != env["account_ref"] + "|" + str(env["peer_id"]) + ":" + original_id
                or not original_id.isdigit()):
            raise Rejected("original_source_provider_id_mismatch")
        body = env["payload"]["text"]
        return {
            "status": "accepted",
            "question_delivery_state": "sent",
            "account_ref": env["account_ref"],
            "peer_id": str(env["peer_id"]),
            "source_provider_message_id": original_id,
            "source_message_id": source_id,
            "conversation_id": env["conversation_id"],
            "event_seq": event_seq,
            "revision": revision,
            "epoch": epoch,
            "request_id": env["request_id"],
            "action_id": action_id,
            "receipt_ref": "messenger:" + action_id,
            "provider_message_id": provider_id,
            "message_sha256": _digest_text(body),
        }

    def propose_dot_original(self, principal, *, event_seq, revision, epoch,
                             lease_token, request_id, body):
        """Stage one original source-bound reply for human review, NO SEND."""
        actor, src = self.runtime.coordinator._scope(principal, event_seq, revision)
        if actor.holder != "dot":
            raise AccessDenied("bound_dot_service_principal_required")
        row = self.runtime.coordinator._existing(src)
        self.runtime.coordinator._assert_owner(row, actor, lease_token, epoch)
        if (not isinstance(body, str) or not body.strip() or len(body) > 16000
                or not isinstance(request_id, str)
                or not 16 <= len(request_id) <= 128):
            raise Rejected("bounded_original_reply_required")
        if row["delivery_state"] != "none":
            raise Busy("original_delivery_must_be_reconciled")
        messenger = Messenger(self.store, self.service, principal)
        if row["result_ref"]:
            # A second request ID must not create a competing proposed
            # original action even if the older action has not been sent.
            existing_ref = str(row["result_ref"])
            if not existing_ref.startswith("messenger:"):
                raise Busy("original_result_already_saved")
            action = messenger.get(existing_ref.removeprefix("messenger:"))
            envelope = action["envelope"]
            if (action["status"] != "proposed"
                    or envelope.get("request_id") != request_id
                    or envelope.get("source_message_id") != src["message_id"]
                    or envelope.get("payload", {}).get("text") != body):
                raise Busy("original_result_already_saved")
        else:
            action = messenger.propose(
                src["account_ref"], src["conversation_id"], src["message_id"],
                "reply", {"text": body}, request_id,
            )
        if action["status"] != "proposed":
            raise Busy("existing_original_outgoing_action_not_proposed")
        result_ref = "messenger:" + action["id"]
        self.runtime.coordinator.result_saved(
            principal, event_seq, revision, lease_token, epoch,
            "question-result:" + action["id"], result_ref,
        )
        return {
            "status": "awaiting_human_approval",
            "action_id": action["id"],
            "expected_hash": action["hash"],
            "question_id": src["question_id"],
            "event_seq": event_seq,
            "revision": revision,
            "epoch": epoch,
            "source_message_id": src["message_id"],
            "approval_path": "/v1/questions/actions/" + action["id"] + "/review",
            "sent": False,
        }
