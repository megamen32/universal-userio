"""Thin authorization and original-receipt adapter for question coordination.

No new worker, queue or delivery route. All sources come from the existing
authenticated UserIO principal and persisted conversation/action bindings.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .question_coordination import Actor, Binding, QuestionCoordination, AccessDenied, Busy, Rejected

DOT_BINDING = Path("/var/lib/universal-userio/telegram-qr/dot-binding.json")


def trusted_dot_binding(path=DOT_BINDING):
    """Read only the existing connector-owned binding; reject unsafe substitutes."""
    path = Path(path)
    try:
        metadata = path.lstat()
        if (not stat.S_ISREG(metadata.st_mode)
                or metadata.st_mode & 0o077
                or metadata.st_uid not in {0, os.getuid()}):
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if (not isinstance(data, dict) or data.get("enabled") is not True
            or not re.fullmatch(r"telegram:[1-9][0-9]*",
                                str(data.get("botAccountId") or ""))
            or not re.fullmatch(r"[1-9][0-9]*", str(data.get("owner") or ""))
            or not isinstance(data.get("room"), str) or not data["room"]):
        return None
    return {"account": data["botAccountId"], "owner": data["owner"]}


class QuestionRuntime:
    """Binds the merged state machine to UserIO, with no implicit send rights."""

    def __init__(self, store, *, key: bytes, dot_binding_path=DOT_BINDING):
        self.store = store
        self.dot_binding_path = dot_binding_path
        # Production HTTP entrypoint installs one owner-session consent guard.
        # The original question lease / Messenger outgoing journal is unchanged.
        self.human_consent = None
        self.coordinator = QuestionCoordination(
            store._connection, store._lock, tenant="userio",
            token_key=key, resolve_actor=self._actor,
            resolve_binding=self._binding,
            journal_reserve=self._reserve_existing_action,
            journal_read=self._read_original_action,
            result_exists=self._result_exists,
            delivery_authorized=self._delivery_allowed,
        )
        # Constructed at backend startup, never lazily by a request carrying
        # user-supplied source text.
        self.coordinator.install()

    def _actor(self, principal):
        if principal is None or not hasattr(principal, "user_id"):
            raise AccessDenied("authenticated_principal_required")
        user = str(principal.user_id)
        if principal.service_account:
            dot = trusted_dot_binding(self.dot_binding_path)
            if not dot or user != self.store.default_user_id:
                raise AccessDenied("verified_dot_binding_unavailable")
            return Actor("userio", user, "dot", "server:dot-bound-service",
                         frozenset({dot["account"]}), frozenset({"manager"}), True)
        if principal.role != "owner" or principal.read_only:
            raise AccessDenied("authorized_manager_principal_required")
        if principal.messenger_grants is not None:
            accounts = frozenset(
                str(g.get("account_ref")) for g in principal.messenger_grants
                if g.get("access") == "read_write" and g.get("account_ref")
            )
        elif principal.messenger_accounts is not None:
            accounts = frozenset(principal.messenger_accounts)
        else:
            accounts = frozenset(
                str(a["id"]) for a in self.store.accounts(user_id=user)
                if a["enabled"] and "reply" in a["capabilities"]
            )
        if not accounts:
            raise AccessDenied("no_granted_accounts")
        return Actor("userio", user, "manager", "owner:" + str(principal.username),
                     accounts, frozenset({"manager"}))

    def _binding(self, actor, source):
        saved = self.store.conversation(source["conversation_id"], user_id=actor.user_id)
        if (saved is None
                or saved["id"] != source["conversation_id"]
                or saved["account_ref"] != source["account_ref"]
                or saved["route_id"] != source["route_id"]
                or str(saved["peer_id"]) != str(source["peer_id"])):
            raise AccessDenied("persisted_conversation_binding_mismatch")
        if not self.store.route_allowed(
                user_id=actor.user_id, source=source["source"], route_id=source["route_id"]):
            raise AccessDenied("route_no_longer_permitted")
        dot = trusted_dot_binding(self.dot_binding_path)
        exact_dot = (dot is not None and source["account_ref"] == dot["account"]
                     and str(source["peer_id"]) == dot["owner"]
                     and source["source"] == "telegram")
        if actor.holder == "dot" and not exact_dot:
            raise AccessDenied("not_bound_dot_owner_question")
        eligible = frozenset({"dot", "manager"} if exact_dot else {"manager"})
        binding_id = hashlib.sha256(
            json.dumps(
                [actor.tenant, actor.user_id, source["account_ref"],
                 source["conversation_id"], source["route_id"], source["peer_id"]],
                separators=(",", ":"), ensure_ascii=False,
            ).encode()
        ).hexdigest()
        return Binding("manager", binding_id, eligible)

    def _action(self, source, *, action_id=None, request_id=None):
        with self.store._lock:
            if not self.store._table_exists("messenger_actions"):
                return None
            if action_id is not None:
                row = self.store._connection.execute(
                    "SELECT id,request_id,envelope,status,receipt FROM messenger_actions "
                    "WHERE user_id=? AND id=?", (source["user_id"], action_id),
                ).fetchone()
            else:
                row = self.store._connection.execute(
                    "SELECT id,request_id,envelope,status,receipt FROM messenger_actions "
                    "WHERE user_id=? AND request_id=?", (source["user_id"], request_id),
                ).fetchone()
        if row is None:
            return None
        envelope = json.loads(row["envelope"])
        if (envelope.get("user_id") != source["user_id"]
                or envelope.get("account_ref") != source["account_ref"]
                or envelope.get("conversation_id") != source["conversation_id"]
                or envelope.get("source_message_id") != source["message_id"]
                or envelope.get("action") != "reply"
                or not envelope.get("source_hash")):
            return None
        return dict(id=row["id"], request_id=row["request_id"],
                    status=row["status"], receipt=row["receipt"], envelope=envelope)

    def _result_exists(self, source, result_ref):
        if not isinstance(result_ref, str) or not result_ref.startswith("messenger:"):
            return False
        item = self._action(source, action_id=result_ref.removeprefix("messenger:"))
        return bool(item and item["status"] in {"proposed", "sending", "accepted"})

    def _reserve_existing_action(self, conn, source, request_id):
        item = self._action(source, request_id=request_id)
        if not item or item["status"] != "proposed":
            raise Rejected("original_outgoing_action_not_proposed")
        row = conn.execute(
            "SELECT result_ref FROM question_coordination WHERE question_id=?",
            (source["question_id"],),
        ).fetchone()
        if row is None or row["result_ref"] != "messenger:" + item["id"]:
            raise Rejected("result_not_bound_to_original_action")
        # The existing messenger_actions row IS the outgoing journal.
        # No second submission or recipient is created here.

    def _read_original_action(self, source, request_id):
        item = self._action(source, request_id=request_id)
        if item and item["status"] == "accepted" and item["receipt"]:
            return {"state": "sent", "receipt_ref": "messenger:" + item["id"]}
        return {"state": "unknown", "receipt_ref": None}

    def _delivery_allowed(self, actor, source, request_id):
        if source["account_ref"] not in actor.accounts:
            return False
        if not self.store.capability_enabled("send", user_id=actor.user_id):
            return False
        account = next(
            (a for a in self.store.accounts(user_id=actor.user_id)
             if a["id"] == source["account_ref"]), None)
        if not account or not account["enabled"] or "reply" not in account["capabilities"]:
            return False
        return self._action(source, request_id=request_id) is not None

    def _question_for_action(self, principal, envelope):
        # Only a saved original incoming source can activate fencing. The
        # account/peer and immutable message snapshot remain Messenger-owned.
        with self.store._lock:
            row = self.store._connection.execute("""
                SELECT event_seq,source_revision FROM question_coordination
                WHERE tenant='userio' AND user_id=? AND account_ref=?
                  AND conversation_id=? AND message_id=?
            """, (principal.user_id, envelope.get("account_ref"),
                  envelope.get("conversation_id"),
                  envelope.get("source_message_id"))).fetchone()
        return None if row is None else (int(row[0]), str(row[1]))

    def before_existing_messenger_send(self, principal, envelope, question_proof=None):
        """Called ONLY from Messenger.approve after its exact human confirmation.

        The network call remains Messenger's existing provider.execute. This
        commits a send fence first, so a crash or handoff cannot replay it.
        """
        question = self._question_for_action(principal, envelope)
        if question is None:
            if question_proof is not None:
                raise Rejected("no_claim_for_provided_question_proof")
            if self.human_consent is not None and principal.service_account:
                dot = trusted_dot_binding(self.dot_binding_path)
                if dot and (envelope.get("account_ref") == dot["account"]
                            and str(envelope.get("peer_id")) == dot["owner"]):
                    # A dot service bearer cannot avoid the approval guard
                    # by sending to the original owner source without a claim.
                    raise AccessDenied("dot_original_question_claim_required")
            return None
        if not isinstance(question_proof, dict) or set(question_proof) != {
                "event_seq", "revision", "epoch", "lease_token"}:
            raise AccessDenied("current_question_lease_required")
        seq, revision = question
        if (type(question_proof["event_seq"]) is not int
                or seq != question_proof["event_seq"]
                or question_proof["revision"] != revision):
            raise Rejected("question_source_revision_mismatch")
        req = envelope.get("request_id")
        action_id = envelope.get("action_id")
        if not isinstance(req, str) or not isinstance(action_id, str):
            raise Rejected("original_messenger_identity_required")
        if self.human_consent is not None:
            actor, src = self.coordinator._scope(principal, seq, revision)
            self.human_consent.verify_before_provider(
                principal, envelope, src["question_id"], question_proof["epoch"])
        result = self.coordinator.begin_original_send(
            principal, seq, revision, question_proof["lease_token"],
            question_proof["epoch"], "provider-start:" + action_id, req)
        if result.get("idempotent"):
            raise Busy("provider_attempt_already_started_do_not_replay")
        return {"event_seq": seq, "revision": revision,
                "action_id": action_id, "question_id": result["question_id"]}

    def after_existing_messenger_receipt(self, principal, receipt_context):
        """Read back original Messenger receipt; never send in this method."""
        if receipt_context is None:
            return None
        result = self.coordinator.reconcile_delivery(
            principal, receipt_context["event_seq"], receipt_context["revision"],
            "receipt-final:" + receipt_context["action_id"])
        return result

    def dispatch(self, principal, operation, args):
        """No arbitrary send, role, user, source or recipient can be supplied."""
        if not isinstance(args, dict) or set(args) - {
                "event_seq", "revision", "request_id", "ttl", "lease_token",
                "epoch", "pending_question", "card_id", "target_holder",
                "result_ref"}:
            raise Rejected("invalid_question_request_fields")
        methods = {
            "acquire": ("event_seq", "revision", "request_id"),
            "renew": ("event_seq", "revision", "lease_token", "epoch", "request_id"),
            "pause": ("event_seq", "revision", "request_id"),
            "handoff": ("event_seq", "revision", "request_id", "target_holder"),
            "resume": ("event_seq", "revision", "request_id"),
            "result_saved": ("event_seq", "revision", "lease_token",
                             "epoch", "request_id", "result_ref"),
            "reconcile": ("event_seq", "revision", "request_id"),
            "complete": ("event_seq", "revision", "lease_token", "epoch", "request_id"),
            "audit": ("event_seq",),
        }
        required = methods.get(operation)
        if required is None:
            raise Rejected("unsupported_question_operation")
        if any(name not in args for name in required):
            raise Rejected("required_question_arguments_missing")
        # Check caller before any source/audit/claim lookup. This prevents
        # nonmembers from learning that a cross-account question exists.
        self._actor(principal)
        permitted = set(required) | {
            "acquire": {"ttl", "pending_question", "card_id"},
            "renew": {"ttl"},
            "pause": set(),
            "handoff": set(),
            "resume": {"ttl"},
            "result_saved": set(),
            "reconcile": set(),
            "complete": set(),
            "audit": set(),
        }[operation]
        if set(args) - permitted:
            raise Rejected("unexpected_question_operation_argument")
        method = getattr(self.coordinator, operation if operation != "reconcile"
                         else "reconcile_delivery")
        # The wire uses lease_token; the existing state machine accepts token.
        internal = dict(args)
        if "lease_token" in internal:
            internal["token"] = internal.pop("lease_token")
        return method(principal, **internal)
