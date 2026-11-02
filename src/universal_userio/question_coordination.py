"""Address-scoped, durable question lease/fence for the existing UserIO SQLite store.

Server-only component: never pass a caller, account, source or holder from an
untrusted request body. The HTTP/MCP adapter must resolve the current caller and
current saved delivery binding, and MUST route actual sends through the same
guarded journal/transport boundary. Importing this module does not turn it on.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import functools
import inspect
import hashlib
import hmac
import json
import sqlite3
import threading
import time
from typing import Callable


class Rejected(ValueError):
    pass


class Busy(Rejected):
    pass


class RevisionConflict(Rejected):
    pass


class AccessDenied(PermissionError):
    pass


@dataclass(frozen=True)
class Actor:
    tenant: str
    user_id: str
    holder: str  # verified service identity: "dot" or "manager"
    subject: str
    accounts: frozenset[str]
    projects: frozenset[str]
    can_coordinate: bool = False


@dataclass(frozen=True)
class Binding:
    project: str
    binding_id: str
    permitted_holders: frozenset[str]


class QuestionCoordination:
    """A single SQLite-transaction state machine, not a second worker/queue.

    resolve_actor MUST read server-authenticated principal metadata (and current
    revocation state), never worker_id, a claim body or a model message.
    resolve_binding MUST inspect the saved verified account/conversation binding;
    textual recipient/source claims are not authority. These resolvers are rerun
    for every operation, including reconciliation/audit.
    """

    def __init__(
        self,
        db: sqlite3.Connection,
        lock: threading.RLock,
        *,
        tenant: str,
        token_key: bytes,
        resolve_actor: Callable[[object], Actor],
        resolve_binding: Callable[[Actor, dict], Binding],
        journal_reserve: Callable[[sqlite3.Connection, dict, str], None],
        journal_read: Callable[[dict, str], dict | None],
        result_exists: Callable[[dict, str], bool],
        delivery_authorized: Callable[[Actor, dict, str], bool],
        clock: Callable[[], float] = time.time,
    ):
        if not token_key or len(token_key) < 32:
            raise ValueError("existing app key of at least 32 bytes required")
        self.db, self.lock = db, lock
        self.tenant = tenant
        self.key = token_key
        self.actor_for = resolve_actor
        self.binding_for = resolve_binding
        self.journal_reserve = journal_reserve
        self.journal_read = journal_read
        self.result_exists = result_exists
        self.delivery_authorized = delivery_authorized
        self.clock = clock

    def install(self) -> None:
        """Call from the normal UserIO schema migration, not on HTTP requests."""
        with self.lock, self.db:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS question_coordination (
                    question_id TEXT PRIMARY KEY,
                    tenant TEXT NOT NULL, user_id TEXT NOT NULL,
                    account_ref TEXT NOT NULL, conversation_id TEXT NOT NULL,
                    source TEXT NOT NULL, message_id TEXT NOT NULL,
                    event_seq INTEGER NOT NULL, source_revision TEXT NOT NULL,
                    project TEXT NOT NULL, binding_id TEXT NOT NULL,
                    holder TEXT NOT NULL, epoch INTEGER NOT NULL,
                    state TEXT NOT NULL, lease_expires_at REAL NOT NULL,
                    pending_holder TEXT, pending_question TEXT, card_id TEXT,
                    result_ref TEXT, delivery_request_id TEXT,
                    delivery_state TEXT NOT NULL DEFAULT 'none',
                    receipt_ref TEXT, updated_at REAL NOT NULL,
                    UNIQUE(tenant,user_id,account_ref,conversation_id,source,message_id)
                );
                CREATE TABLE IF NOT EXISTS question_operations (
                    question_id TEXT NOT NULL,request_id TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,response_json TEXT NOT NULL,
                    PRIMARY KEY(question_id,request_id)
                );
                CREATE TABLE IF NOT EXISTS question_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question_id TEXT NOT NULL,tenant TEXT NOT NULL,user_id TEXT NOT NULL,
                    actor TEXT NOT NULL,source TEXT NOT NULL,message_id TEXT NOT NULL,
                    event_seq INTEGER NOT NULL,action TEXT NOT NULL,
                    old_holder TEXT,new_holder TEXT,epoch INTEGER NOT NULL,
                    at REAL NOT NULL,reason TEXT,request_id TEXT,
                    source_revision TEXT NOT NULL,result_ref TEXT,receipt_ref TEXT
                );
                CREATE INDEX IF NOT EXISTS question_audit_key_idx
                    ON question_audit(question_id,id);
            """)

    @contextmanager
    def _tx(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self.db.rollback()
                raise
            else:
                self.db.commit()

    @staticmethod
    def _required(value: str, name: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise Rejected(f"invalid {name}")
        return value

    def _scope(self, context: object, event_seq: int, expected_revision: str | None = None):
        actor = self.actor_for(context)
        if not isinstance(actor, Actor) or actor.holder not in {"dot", "manager"} or actor.tenant != self.tenant:
            raise AccessDenied("verified service actor required")
        if type(event_seq) is not int or event_seq <= 0:
            raise Rejected("invalid event_seq")
        record = self.db.execute("""
            SELECT e.seq,e.user_id,e.source,e.message_id,e.conversation_id,e.eligible,
                   m.sender,m.direction,m.sender_is_bot,m.edited_at,
                   c.account_ref,c.peer_id,c.route_id
              FROM workspace_events e
              JOIN messages m ON m.user_id=e.user_id
                 AND m.source=e.source AND m.message_id=e.message_id
              JOIN conversations c ON c.user_id=e.user_id AND c.id=e.conversation_id
             WHERE e.user_id=? AND e.seq=?
        """, (actor.user_id, event_seq)).fetchone()
        if record is None:
            raise AccessDenied("source_not_available")
        names = ["seq", "user_id", "source", "message_id", "conversation_id",
                 "eligible", "sender", "direction", "sender_is_bot", "edited_at",
                 "account_ref", "peer_id", "route_id"]
        src = dict(zip(names, record))
        if not src["eligible"] or src["direction"] != "incoming" or src["sender_is_bot"]:
            raise AccessDenied("source_not_eligible")
        if str(src["source"]).split(":", 1)[0] == "chatgpt":
            raise AccessDenied("automated_source")
        if src["account_ref"] not in actor.accounts:
            raise AccessDenied("account_not_authorized")
        binding = self.binding_for(actor, src)
        if not isinstance(binding, Binding) or not binding.binding_id:
            raise AccessDenied("saved_binding_required")
        if binding.project not in actor.projects or actor.holder not in binding.permitted_holders:
            raise AccessDenied("project_or_role_not_authorized")
        src["revision"] = str(src["edited_at"]) if src["edited_at"] is not None else "0"
        if expected_revision is not None and src["revision"] != expected_revision:
            raise RevisionConflict("source_revision_changed")
        identity = [actor.tenant, actor.user_id, src["account_ref"],
                    src["conversation_id"], src["source"], src["message_id"]]
        question_id = hashlib.sha256(
            json.dumps(identity, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        src.update(question_id=question_id, tenant=actor.tenant, project=binding.project,
                   binding_id=binding.binding_id, permitted_holders=binding.permitted_holders,
                   caller_subject=actor.subject, caller_holder=actor.holder)
        return actor, src

    def _existing(self, source: dict):
        row = self.db.execute("SELECT * FROM question_coordination WHERE question_id=?",
                              (source["question_id"],)).fetchone()
        if row is None:
            return None
        columns = [d[1] for d in self.db.execute("PRAGMA table_info(question_coordination)")]
        out = dict(zip(columns, row))
        if out["source_revision"] != source["revision"]:
            raise RevisionConflict("source_changed_requires_explicit_reconciliation")
        if out["binding_id"] != source["binding_id"] or out["project"] != source["project"]:
            raise AccessDenied("binding_changed")
        return out

    def _token(self, question_id: str, epoch: int, holder: str):
        data = f"{question_id}/{epoch}/{holder}".encode()
        return "qcl_" + hmac.new(self.key, data, hashlib.sha256).hexdigest()

    def _assert_owner(self, row, actor, token, epoch):
        if row is None or row["state"] != "active":
            raise Rejected("claim_not_active")
        if row["holder"] != actor.holder or row["epoch"] != epoch:
            raise AccessDenied("stale_holder_or_epoch")
        if not isinstance(token, str) or not hmac.compare_digest(
            self._token(row["question_id"], row["epoch"], row["holder"]), token
        ):
            raise AccessDenied("invalid_lease")
        if row["lease_expires_at"] <= self.clock():
            raise Rejected("lease_expired")

    def _seen(self, source, request_id, payload):
        self._required(request_id, "request_id")
        # A request ID cannot be replayed by a different authenticated worker,
        # even if the original untrusted payload bytes are unchanged.
        digest = hashlib.sha256(json.dumps(
            {"actor": [source["tenant"], source["caller_subject"],
                       source["caller_holder"]], "payload": payload},
            ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        record = self.db.execute("""
            SELECT payload_sha256,response_json FROM question_operations
             WHERE question_id=? AND request_id=?
        """, (source["question_id"], request_id)).fetchone()
        if record:
            if record[0] != digest:
                raise Rejected("request_id_payload_conflict")
            result = json.loads(record[1])
            if "epoch" in result and result.get("holder") in {"dot", "manager"}:
                current = self.db.execute(
                    "SELECT epoch,holder,state FROM question_coordination WHERE question_id=?",
                    (source["question_id"],)).fetchone()
                if current and current[0] == result["epoch"] and current[1] == result["holder"] and current[2] == "active":
                    result["lease_token"] = self._token(source["question_id"], current[0], current[1])
            result["idempotent"] = True
            return digest, result
        return digest, None

    def _record(self, src, request_id, digest, result):
        sanitized = {k: v for k, v in result.items() if k != "lease_token"}
        self.db.execute("INSERT INTO question_operations VALUES (?,?,?,?)", (
            src["question_id"], request_id, digest,
            json.dumps(sanitized, ensure_ascii=False, sort_keys=True),
        ))

    def _audit(self, source, actor, action, old, new, epoch, request_id, reason=None,
               result_ref=None, receipt_ref=None):
        self.db.execute("""
            INSERT INTO question_audit
            (question_id,tenant,user_id,actor,source,message_id,event_seq,action,
             old_holder,new_holder,epoch,at,reason,request_id,source_revision,result_ref,receipt_ref)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (source["question_id"], actor.tenant, actor.user_id, actor.subject,
              source["source"], source["message_id"], source["seq"], action,
              old, new, epoch, self.clock(), reason, request_id, source["revision"],
              result_ref, receipt_ref))

    def acquire(self, context, event_seq: int, revision: str, request_id: str,
                ttl: int = 300, pending_question: str | None = None, card_id: str | None = None):
        if type(ttl) is not int or not 30 <= ttl <= 900:
            raise Rejected("invalid_ttl")
        with self._tx():
            actor, source = self._scope(context, event_seq, revision)
            payload = ["acquire", event_seq, revision, ttl, pending_question, card_id]
            digest, duplicate = self._seen(source, request_id, payload)
            if duplicate is not None:
                return duplicate
            row = self._existing(source)
            now = self.clock()
            if row and row["state"] in {"completed", "paused", "handoff", "transferring"}:
                raise Busy("question_unavailable")
            if row and row["delivery_state"] != "none":
                raise Busy("delivery_receipt_must_be_reconciled")
            if row and row["state"] == "active" and row["lease_expires_at"] > now:
                raise Busy("already_claimed")
            epoch = (row["epoch"] + 1) if row else 1
            if row:
                self._audit(source, actor, "expired", row["holder"], None, row["epoch"], request_id)
                self.db.execute("""
                    UPDATE question_coordination
                    SET holder=?,epoch=?,state='active',lease_expires_at=?,pending_holder=NULL,
                        updated_at=? WHERE question_id=?
                """, (actor.holder, epoch, now + ttl, now, source["question_id"]))
                result_ref = row["result_ref"]
            else:
                self.db.execute("""
                    INSERT INTO question_coordination
                    (question_id,tenant,user_id,account_ref,conversation_id,source,message_id,
                     event_seq,source_revision,project,binding_id,holder,epoch,state,lease_expires_at,
                     pending_question,card_id,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?,?,?,?)
                """, (source["question_id"], actor.tenant, actor.user_id, source["account_ref"],
                      source["conversation_id"], source["source"], source["message_id"],
                      event_seq, source["revision"], source["project"], source["binding_id"],
                      actor.holder, epoch, now + ttl, pending_question, card_id, now))
                result_ref = None
            self._audit(source, actor, "acquire", row["holder"] if row else None,
                        actor.holder, epoch, request_id)
            result = dict(question_id=source["question_id"], holder=actor.holder,
                          epoch=epoch, state="active", result_ref=result_ref,
                          lease_expires_at=now + ttl,
                          lease_token=self._token(source["question_id"], epoch, actor.holder))
            self._record(source, request_id, digest, result)
            return result

    def renew(self, context, event_seq, revision, token, epoch, request_id, ttl=300):
        if type(ttl) is not int or not 30 <= ttl <= 900:
            raise Rejected("invalid_ttl")
        with self._tx():
            actor, src = self._scope(context, event_seq, revision)
            digest, duplicate = self._seen(src, request_id, ["renew", epoch, revision, ttl])
            if duplicate is not None:
                return duplicate
            row = self._existing(src)
            self._assert_owner(row, actor, token, epoch)
            expires = self.clock() + ttl
            self.db.execute("UPDATE question_coordination SET lease_expires_at=?,updated_at=? WHERE question_id=?",
                            (expires, self.clock(), src["question_id"]))
            self._audit(src, actor, "renew", actor.holder, actor.holder, epoch, request_id)
            result = dict(question_id=src["question_id"], holder=actor.holder,
                          epoch=epoch, state="active", lease_expires_at=expires)
            self._record(src, request_id, digest, result)
            return result

    def pause(self, context, event_seq, revision, request_id, reason="handoff"):
        with self._tx():
            actor, src = self._scope(context, event_seq, revision)
            digest, duplicate = self._seen(src, request_id, ["pause", revision, reason])
            if duplicate is not None:
                return duplicate
            row = self._existing(src)
            if not row or (row["holder"] != actor.holder and not actor.can_coordinate):
                raise AccessDenied("pause_not_authorized")
            if row["state"] == "completed":
                raise Rejected("already_completed")
            status = "transferring" if row["delivery_state"] in {"in_flight", "unknown"} else "paused"
            self.db.execute("UPDATE question_coordination SET state=?,updated_at=? WHERE question_id=?",
                            (status, self.clock(), src["question_id"]))
            self._audit(src, actor, "pause_requested", row["holder"], row["holder"],
                        row["epoch"], request_id, reason=reason[:150])
            result = dict(question_id=src["question_id"], state=status,
                          holder=row["holder"], epoch=row["epoch"])
            self._record(src, request_id, digest, result)
            return result

    def handoff(self, context, event_seq, revision, request_id, target_holder):
        if target_holder not in {"dot", "manager"}:
            raise Rejected("invalid_target")
        with self._tx():
            actor, src = self._scope(context, event_seq, revision)
            digest, duplicate = self._seen(src, request_id,
                                           ["handoff", revision, target_holder])
            if duplicate is not None:
                return duplicate
            row = self._existing(src)
            if not row or (actor.holder != row["holder"] and not actor.can_coordinate):
                raise AccessDenied("handoff_not_authorized")
            if target_holder not in src["permitted_holders"]:
                raise AccessDenied("target_not_authorized_by_binding")
            if row["state"] == "completed":
                raise Rejected("already_completed")
            unknown = row["delivery_state"] in {"in_flight", "unknown"}
            epoch = row["epoch"] if unknown else row["epoch"] + 1
            holder = row["holder"] if unknown else target_holder
            status = "transferring" if unknown else "handoff"
            self.db.execute("""
                UPDATE question_coordination SET holder=?,epoch=?,state=?,
                    pending_holder=?,lease_expires_at=0,updated_at=? WHERE question_id=?
            """, (holder, epoch, status, target_holder if unknown else None,
                  self.clock(), src["question_id"]))
            self._audit(src, actor, "handoff", row["holder"], target_holder, epoch,
                        request_id, reason="pending_receipt" if unknown else "fenced")
            result = dict(question_id=src["question_id"],state=status,
                          holder=holder, epoch=epoch, pending_holder=target_holder if unknown else None)
            self._record(src, request_id, digest, result)
            return result

    def resume(self, context, event_seq, revision, request_id, ttl=300):
        if type(ttl) is not int or not 30 <= ttl <= 900:
            raise Rejected("invalid_ttl")
        with self._tx():
            actor, src = self._scope(context, event_seq, revision)
            digest, duplicate = self._seen(src, request_id, ["resume", revision, ttl])
            if duplicate is not None:
                return duplicate
            row = self._existing(src)
            if not row or row["state"] not in {"paused", "handoff"} or row["holder"] != actor.holder:
                raise Busy("question_not_resumable_by_caller")
            if row["delivery_state"] in {"in_flight", "unknown"}:
                raise Busy("delivery_unknown")
            epoch = row["epoch"] + 1
            now = self.clock()
            self.db.execute("""
                UPDATE question_coordination SET state='active',epoch=?,
                    lease_expires_at=?,updated_at=? WHERE question_id=?
            """, (epoch, now + ttl, now, src["question_id"]))
            self._audit(src, actor, "resume", row["holder"], actor.holder, epoch, request_id)
            result = dict(question_id=src["question_id"], holder=actor.holder,
                          epoch=epoch,state="active",result_ref=row["result_ref"],
                          delivery_state=row["delivery_state"],
                          lease_token=self._token(src["question_id"], epoch, actor.holder))
            self._record(src, request_id, digest, result)
            return result

    def result_saved(self, context, event_seq, revision, token, epoch, request_id, result_ref):
        self._required(result_ref, "result_ref")
        with self._tx():
            actor, src = self._scope(context, event_seq, revision)
            digest, duplicate = self._seen(src, request_id, ["result_saved", epoch, revision, result_ref])
            if duplicate is not None:
                return duplicate
            row = self._existing(src)
            self._assert_owner(row, actor, token, epoch)
            if not self.result_exists(src, result_ref):
                raise Rejected("durable_result_not_found")
            if row["result_ref"] and row["result_ref"] != result_ref:
                raise Rejected("result_already_saved")
            self.db.execute("UPDATE question_coordination SET result_ref=?,updated_at=? WHERE question_id=?",
                            (result_ref,self.clock(),src["question_id"]))
            self._audit(src, actor, "result_saved", actor.holder, actor.holder, epoch,
                        request_id, result_ref=result_ref)
            result = dict(question_id=src["question_id"], holder=actor.holder,
                          epoch=epoch,result_ref=result_ref,state="active")
            self._record(src, request_id, digest, result)
            return result

    def reserve_delivery(self, context, event_seq, revision, token, epoch, request_id):
        """Atomic same-database reserve in the EXISTING outgoing journal.

        This is a fence, NOT an instruction to send. The production transport
        must require a fresh fence check at the actual send boundary and consume
        this original journal identity. Never offer an unfenced send bypass.
        """
        with self._tx():
            actor, src = self._scope(context, event_seq, revision)
            digest, duplicate = self._seen(src, request_id, ["delivery", epoch, revision])
            if duplicate is not None:
                return duplicate
            row = self._existing(src)
            self._assert_owner(row, actor, token, epoch)
            if not row["result_ref"]:
                raise Rejected("result_must_be_saved")
            if row["delivery_state"] != "none":
                raise Busy("existing_delivery_must_be_reconciled")
            # Explicit human approval and current send capability are resolved
            # from the original UserIO approval + caller ACL, never request text.
            if not self.delivery_authorized(actor, src, request_id):
                raise AccessDenied("current_delivery_authorization_required")
            # Must be the same SQLite transaction, not a second outbox.
            self.journal_reserve(self.db, src, request_id)
            self.db.execute("""
                UPDATE question_coordination SET delivery_request_id=?,
                    delivery_state='in_flight',updated_at=? WHERE question_id=?
            """, (request_id, self.clock(), src["question_id"]))
            self._audit(src, actor, "delivery_unknown", actor.holder, actor.holder,
                        epoch, request_id,reason="outgoing_reserved_no_confirmed_receipt",
                        result_ref=row["result_ref"])
            result = dict(question_id=src["question_id"], state="in_flight",
                          holder=actor.holder, epoch=epoch, delivery_request_id=request_id)
            self._record(src, request_id, digest, result)
            return result

    def reconcile_delivery(self, context, event_seq, revision, request_id):
        """Read ONLY the original journal operation; never send/requeue here."""
        with self._tx():
            actor, src = self._scope(context, event_seq, revision)
            digest, duplicate = self._seen(src, request_id, ["reconcile", revision])
            if duplicate is not None:
                return duplicate
            row = self._existing(src)
            if not row or not row["delivery_request_id"]:
                raise Rejected("no_delivery_operation")
            receipt = self.journal_read(src, row["delivery_request_id"])
            state = (receipt or {}).get("state")
            ref = (receipt or {}).get("receipt_ref")
            if state not in {"sent", "failed"} or not ref:
                state, ref = "unknown", None
            self.db.execute("""
                UPDATE question_coordination SET delivery_state=?,receipt_ref=?,updated_at=?
                WHERE question_id=?
            """, (state, ref, self.clock(), src["question_id"]))
            self._audit(src, actor, "delivery_unknown" if state == "unknown" else "result_saved",
                        row["holder"], row["holder"], row["epoch"], request_id,
                        reason="receipt_readback",result_ref=row["result_ref"],receipt_ref=ref)
            # A deferred handoff only advances after a defined receipt.
            holder, epoch, status = row["holder"], row["epoch"], row["state"]
            if status == "transferring" and state in {"sent", "failed"} and row["pending_holder"]:
                if row["pending_holder"] not in src["permitted_holders"]:
                    # Never resume a revoked holder; leave the settled delivery
                    # as transferring until a current authorized decision.
                    status = "transferring"
                else:
                    holder, epoch, status = row["pending_holder"], epoch + 1, "handoff"
                    self.db.execute("""
                        UPDATE question_coordination SET holder=?,epoch=?,state=?,
                           pending_holder=NULL,updated_at=? WHERE question_id=?
                    """, (holder, epoch, status, self.clock(), src["question_id"]))
                    self._audit(src, actor, "handoff", row["holder"], holder, epoch, request_id,
                                reason="receipt_settled",receipt_ref=ref)
            result = dict(question_id=src["question_id"], holder=holder,
                          epoch=epoch,state=status,delivery_state=state,receipt_ref=ref)
            self._record(src, request_id, digest, result)
            return result

    def complete(self, context, event_seq, revision, token, epoch, request_id):
        with self._tx():
            actor, src = self._scope(context, event_seq, revision)
            digest, duplicate = self._seen(src, request_id, ["complete", epoch, revision])
            if duplicate is not None:
                return duplicate
            row = self._existing(src)
            self._assert_owner(row, actor, token, epoch)
            if not row["result_ref"] or row["delivery_state"] not in {"sent", "failed"} or not row["receipt_ref"]:
                raise Rejected("defined_delivery_and_result_required")
            self.db.execute("""
                UPDATE question_coordination SET state='completed',updated_at=?
                WHERE question_id=?
            """, (self.clock(), src["question_id"]))
            self._audit(src, actor, "completed", actor.holder, None, epoch, request_id,
                        result_ref=row["result_ref"],receipt_ref=row["receipt_ref"])
            result = dict(question_id=src["question_id"],state="completed",
                          result_ref=row["result_ref"],delivery_state=row["delivery_state"],
                          receipt_ref=row["receipt_ref"])
            self._record(src, request_id, digest, result)
            return result

    def send_fenced(self, context, event_seq, revision, token, epoch, dispatch_id, transport):
        """Only the trusted server's EXISTING UserIO transport may invoke this.

        Linearize the effect-start at a durable UNKNOWN marker under the same
        database transaction used to claim the original outgoing journal.
        A later handoff remains pending until receipt reconciliation. External
        network calls MUST NOT hold the SQLite writer lock; the old holder may
        finish only the already-started send, never launch another one.
        """
        with self._tx():
            actor, src = self._scope(context, event_seq, revision)
            digest, duplicate = self._seen(src, dispatch_id, ["send_fenced", epoch, revision])
            if duplicate is not None:
                return duplicate
            row = self._existing(src)
            self._assert_owner(row, actor, token, epoch)
            if row["delivery_state"] != "in_flight" or not row["delivery_request_id"]:
                raise Rejected("delivery_not_reserved")
            # Recheck at the actual provider boundary. Revocation after reserve
            # cannot be bypassed by an already-held lease.
            if not self.delivery_authorized(actor, src, row["delivery_request_id"]):
                raise AccessDenied("current_delivery_authorization_required")
            # This commit precedes any possible provider side-effect. A crash
            # after it is UNKNOWN and MUST NEVER replay this dispatch_id.
            self.db.execute("""
                UPDATE question_coordination SET delivery_state='unknown',updated_at=?
                WHERE question_id=?
            """, (self.clock(), src["question_id"]))
            self._audit(src, actor, "delivery_unknown", actor.holder, actor.holder,
                        epoch, dispatch_id, reason="send_started_irrevocable",
                        result_ref=row["result_ref"])
            result = dict(question_id=src["question_id"], state="unknown",
                          delivery_request_id=row["delivery_request_id"])
            self._record(src, dispatch_id, digest, result)
            original_request = row["delivery_request_id"]
        # The effect was already begun before any concurrent pause/handoff.
        # The trusted transport resolves the recipient from src.binding_id, and
        # uses the original provider operation ID without an alternate route.
        try:
            transport(src, original_request)
        except Exception:
            # A transport exception proves neither delivery nor non-delivery.
            # Only the original journal's durable receipt can settle it.
            pass
        reconcile_request = "receipt_" + hashlib.sha256(dispatch_id.encode()).hexdigest()
        return self.reconcile_delivery(context, event_seq, revision, reconcile_request)

    def _audit_rejection(self, context, event_seq, request_id, reason):
        """No cross-account rejection log or leaked source content on denial."""
        try:
            with self._tx():
                actor, src = self._scope(context, event_seq)
                row = self.db.execute(
                    "SELECT holder,epoch FROM question_coordination WHERE question_id=?",
                    (src["question_id"],)).fetchone()
                if row is None:
                    return
                if request_id:
                    previous = self.db.execute("""
                        SELECT 1 FROM question_audit WHERE question_id=? AND
                        request_id=? AND action='rejected'
                    """, (src["question_id"], request_id)).fetchone()
                    if previous:
                        return
                self._audit(src, actor, "rejected", row[0], row[0], row[1],
                            request_id, reason=reason[:150])
        except (AccessDenied, Rejected, sqlite3.Error):
            pass

    def audit(self, context, event_seq):
        with self.lock:
            actor, src = self._scope(context, event_seq)
            self._existing(src)
            rows = self.db.execute("""
                SELECT action,actor,source,message_id,event_seq,old_holder,new_holder,
                       epoch,at,reason,request_id,source_revision,result_ref,receipt_ref
                FROM question_audit WHERE question_id=? ORDER BY id
            """,(src["question_id"],)).fetchall()
            names = ["action","actor","source","message_id","event_seq","old_holder",
                     "new_holder","epoch","at","reason","request_id","source_revision",
                     "result_ref","receipt_ref"]
            return [dict(zip(names,row)) for row in rows]


def _with_rejection_audit(method):
    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except (Rejected, AccessDenied) as error:
            values = inspect.signature(method).bind(self, *args, **kwargs).arguments
            self._audit_rejection(values.get("context"), values.get("event_seq"),
                                  values.get("request_id") or values.get("dispatch_id"),
                                  str(error))
            raise
    return wrapped


for _method in (
    "acquire", "renew", "pause", "handoff", "resume", "result_saved",
    "reserve_delivery", "send_fenced", "reconcile_delivery", "complete",
):
    setattr(QuestionCoordination, _method,
            _with_rejection_audit(getattr(QuestionCoordination, _method)))
