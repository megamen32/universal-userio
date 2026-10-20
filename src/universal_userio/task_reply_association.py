"""Fail-closed association of an inbound reply with a task-bound send receipt.

The module owns no storage and performs no provider I/O. All three readers are
injected adapters over the canonical current message, task source grants, and
durable operation receipts. Provider/account/conversation/message IDs are used
as exact keys; display names, endpoints, ordering, and timestamps are ignored.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Protocol, Sequence


@dataclass(frozen=True, slots=True)
class InboundMessageKey:
    provider: str
    account_ref: str
    conversation_id: str
    message_id: str


@dataclass(frozen=True, slots=True)
class CanonicalInboundMessage:
    provider: str
    account_ref: str
    conversation_id: str
    message_id: str
    reply_to_provider_message_id: str


@dataclass(frozen=True, slots=True)
class TaskReplyScope:
    task_id: str
    actor_id: str
    project_id: str
    version: int
    provider: str
    account_ref: str
    conversation_id: str
    contact_id: str = ""
    source_namespace: str = ""
    source_id: str = ""


@dataclass(frozen=True, slots=True)
class DurableOperationReceipt:
    operation_id: str
    task_id: str
    actor_id: str
    project_id: str
    version: int
    provider: str
    account_ref: str
    conversation_id: str
    provider_message_id: str
    status: str
    contact_id: str
    source_namespace: str
    source_id: str


class TaskReplyReaders(Protocol):
    """Canonical owner interfaces; implementations must read durable state."""

    def read_current_message(
        self, key: InboundMessageKey,
    ) -> CanonicalInboundMessage | None: ...

    def read_task_scopes(
        self, message: CanonicalInboundMessage,
    ) -> Sequence[TaskReplyScope]: ...

    def read_operation_receipts(
        self, *, provider: str, account_ref: str, conversation_id: str,
        provider_message_id: str,
    ) -> Sequence[DurableOperationReceipt]: ...


@dataclass(frozen=True, slots=True)
class ReplyAssociation:
    status: str
    reason: str
    task_id: str | None = None
    operation_id: str | None = None
    contact_id: str | None = None
    source_namespace: str | None = None
    source_id: str | None = None
    provider_message_id: str | None = None


_ACCEPTED = frozenset({"accepted", "sent"})
_MAX_PROVIDER_ID = re.compile(r"[0-9]+\Z")


class UserIOStoreTaskReplyReaders:
    """Read exact reply provenance from the existing UserIO store.

    The one currently proven provider mapping is MAX: service approval reads
    `get_message(chat_id, message_id=receipt)`, validates the returned native
    id and owner sender, then persists the canonical outgoing message as
    `<account_ref>|<peer_id>:<provider_id>`. Other receipt formats remain
    unresolved until their owner provides an equally exact mapping.
    """

    def __init__(self, store, *, user_id: str) -> None:
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id_required")
        self.store = store
        self.user_id = store._user(user_id)

    def _one(self, sql: str, values: tuple[object, ...]):
        with self.store._lock:
            rows = self.store._connection.execute(sql, values).fetchall()
        if len(rows) > 1:
            raise ValueError("canonical_store_identity_ambiguous")
        return rows[0] if rows else None

    def read_current_message(self, key: InboundMessageKey) -> CanonicalInboundMessage | None:
        row = self.store.message(key.message_id, source=key.provider, user_id=self.user_id)
        if row is None or row.get("direction") != "incoming":
            return None
        conversation = self._one(
            "SELECT id,source,account_ref,peer_id FROM conversations "
            "WHERE user_id=? AND id=?",
            (self.user_id, str(row["conversation_id"])),
        )
        if (conversation is None
                or str(conversation["id"]) != key.conversation_id
                or str(conversation["source"]).lower() != key.provider.lower()
                or str(conversation["account_ref"]) != key.account_ref):
            return None
        return CanonicalInboundMessage(
            provider=str(row["source"]).lower(),
            account_ref=str(conversation["account_ref"]),
            conversation_id=str(row["conversation_id"]),
            message_id=str(row["message_id"]),
            reply_to_provider_message_id=str(row["provider_reply_to"] or ""),
        )

    def read_task_scopes(self, message: CanonicalInboundMessage) -> Sequence[TaskReplyScope]:
        sql = """SELECT DISTINCT m.task_id,m.actor_id,m.account_ref,m.channel,m.version,
                   o.mandate_version,d.conversation_id,q.project_id,q.catalog_contact_id,
                   q.catalog_namespace,q.catalog_record_id
            FROM task_mandate_draft_claims c
            JOIN task_mandates m ON m.user_id=c.user_id AND m.id=c.mandate_id
            JOIN task_mandate_operations o ON o.user_id=c.user_id
                 AND o.mandate_id=c.mandate_id AND o.operation_id=c.operation_id
            JOIN task_contact_qualifications q ON q.user_id=c.user_id
                 AND q.id=c.qualification_id
            JOIN drafts d ON d.user_id=c.user_id AND d.id=c.draft_id
            WHERE c.user_id=? AND d.conversation_id=?
              AND m.account_ref=? AND lower(m.channel)=lower(?)
              AND c.operation_hash=o.operation_hash
              AND c.binding_hash=q.binding_hash
              AND m.version=o.mandate_version
              AND q.account_ref=m.account_ref AND lower(q.provider)=lower(m.channel)
              AND q.conversation_id=d.conversation_id"""
        with self.store._lock:
            rows = self.store._connection.execute(sql, (
                self.user_id, message.conversation_id, message.account_ref,
                message.provider,
            )).fetchall()
        return tuple(TaskReplyScope(
            task_id=str(row["task_id"]), actor_id=str(row["actor_id"]),
            project_id=str(row["project_id"]), version=int(row["mandate_version"]),
            provider=str(row["channel"]).lower(), account_ref=str(row["account_ref"]),
            conversation_id=str(row["conversation_id"]),
            contact_id=str(row["catalog_contact_id"]),
            source_namespace=str(row["catalog_namespace"]),
            source_id=str(row["catalog_record_id"]),
        ) for row in rows)

    def read_operation_receipts(
        self, *, provider: str, account_ref: str, conversation_id: str,
        provider_message_id: str,
    ) -> Sequence[DurableOperationReceipt]:
        # MAX is the only current UserIO path whose draft receipt is proven by
        # source to be the provider's native message ID and mirrored message.
        if (provider.lower() != "max"
                or account_ref != "max" and not account_ref.startswith("max:")
                or not _MAX_PROVIDER_ID.fullmatch(provider_message_id)):
            return ()
        sql = """SELECT m.task_id,m.actor_id,m.account_ref,m.channel,m.version,
                   o.operation_id,o.mandate_version,o.operation_hash,o.operation_json,
                   o.status AS operation_status,c.operation_hash AS claim_hash,
                   c.binding_hash AS claim_binding_hash,d.id AS draft_id,d.body,
                   d.status AS draft_status,d.delivery_state,d.outbox_receipt,
                   q.id AS qualification_id,q.project_id,q.catalog_contact_id,
                   q.catalog_namespace,q.catalog_record_id,q.binding_hash,
                   q.account_ref AS qualification_account_ref,q.provider AS qualification_provider,
                   q.peer_id,q.conversation_id AS qualification_conversation_id,
                   cv.source AS conversation_provider,cv.account_ref AS conversation_account_ref,
                   cv.peer_id AS conversation_peer_id
            FROM drafts d
            JOIN task_mandate_draft_claims c ON c.user_id=d.user_id AND c.draft_id=d.id
            JOIN task_mandates m ON m.user_id=c.user_id AND m.id=c.mandate_id
            JOIN task_mandate_operations o ON o.user_id=c.user_id
                 AND o.mandate_id=c.mandate_id AND o.operation_id=c.operation_id
            JOIN task_contact_qualifications q ON q.user_id=c.user_id
                 AND q.id=c.qualification_id
            JOIN conversations cv ON cv.user_id=d.user_id AND cv.id=d.conversation_id
            WHERE d.user_id=? AND d.conversation_id=? AND d.status='approved'
              AND d.delivery_state='delivered' AND d.outbox_receipt=?
              AND lower(m.channel)='max' AND m.account_ref=?
              AND cv.source='max' AND cv.account_ref=m.account_ref
              AND cv.id=d.conversation_id AND cv.peer_id=q.peer_id
              AND q.account_ref=m.account_ref AND lower(q.provider)='max'
              AND q.conversation_id=d.conversation_id
              AND c.operation_hash=o.operation_hash
              AND c.binding_hash=q.binding_hash
              AND m.version=o.mandate_version"""
        with self.store._lock:
            rows = self.store._connection.execute(sql, (
                self.user_id, conversation_id, provider_message_id, account_ref,
            )).fetchall()
        receipts: list[DurableOperationReceipt] = []
        for row in rows:
            try:
                operation = json.loads(str(row["operation_json"]))
            except (TypeError, ValueError):
                continue
            if not isinstance(operation, dict):
                continue
            try:
                operation_hash = hashlib.sha256(json.dumps(
                    operation, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), allow_nan=False,
                ).encode("utf-8")).hexdigest()
            except (TypeError, ValueError):
                continue
            expected = {
                "task_id": str(row["task_id"]), "actor_id": str(row["actor_id"]),
                "account_ref": str(row["account_ref"]), "channel": "max",
                "conversation_id": conversation_id,
                "recipient_id": str(row["peer_id"]),
                "qualification_id": str(row["qualification_id"]),
                "catalog_binding_hash": str(row["binding_hash"]),
                "mandate_version": int(row["mandate_version"]),
            }
            if (operation_hash != str(row["operation_hash"])
                    or str(row["operation_status"]) != "reserved"
                    or row["claim_hash"] != row["operation_hash"]
                    or any(operation.get(name) != value for name, value in expected.items())
                    or hashlib.sha256(str(row["body"]).encode("utf-8")).hexdigest()
                    != operation.get("content_sha256")
                    or str(row["qualification_account_ref"]) != account_ref
                    or str(row["qualification_provider"]).lower() != "max"
                    or str(row["qualification_conversation_id"]) != conversation_id
                    or str(row["conversation_provider"]).lower() != "max"
                    or str(row["conversation_account_ref"]) != account_ref):
                continue
            # This exact account-scoped message key is produced by the existing
            # MAX receipt hydration path after native-ID and owner-sender checks.
            expected_message_id = (f"{account_ref}|{row['conversation_peer_id']}:{provider_message_id}"
                                   if account_ref.startswith("max:") else
                                   f"max-{row['conversation_peer_id']}-{provider_message_id}")
            try:
                sent = self.store.message(
                    expected_message_id, source="max", user_id=self.user_id,
                )
            except ValueError:
                continue
            if (sent is None or sent.get("direction") != "outgoing"
                    or str(sent.get("conversation_id")) != conversation_id
                    or str(sent.get("body")) != str(row["body"])):
                continue
            receipts.append(DurableOperationReceipt(
                operation_id=str(row["operation_id"]), task_id=str(row["task_id"]),
                actor_id=str(row["actor_id"]), project_id=str(row["project_id"]),
                version=int(row["mandate_version"]), provider="max",
                account_ref=account_ref, conversation_id=conversation_id,
                provider_message_id=provider_message_id, status="accepted",
                contact_id=str(row["catalog_contact_id"]),
                source_namespace=str(row["catalog_namespace"]),
                source_id=str(row["catalog_record_id"]),
            ))
        return tuple(receipts)


def _nonempty(*values: str) -> bool:
    return all(isinstance(value, str) and bool(value.strip()) for value in values)


class TaskReplyAssociation:
    """Resolve one canonical inbound message against one exact durable receipt."""

    def __init__(self, readers: TaskReplyReaders) -> None:
        self.readers = readers

    @staticmethod
    def _same_key(message: CanonicalInboundMessage,
                  key: InboundMessageKey) -> bool:
        return (message.provider, message.account_ref, message.conversation_id,
                message.message_id) == (
                    key.provider, key.account_ref, key.conversation_id,
                    key.message_id,
                )

    @staticmethod
    def _scope_matches(scope: TaskReplyScope,
                       message: CanonicalInboundMessage) -> bool:
        return (scope.provider == message.provider
                and scope.account_ref == message.account_ref
                and scope.conversation_id == message.conversation_id)

    def resolve(self, key: InboundMessageKey) -> ReplyAssociation:
        if not isinstance(key, InboundMessageKey) or not _nonempty(
            key.provider, key.account_ref, key.conversation_id,
            key.message_id,
        ):
            return ReplyAssociation("denied", "invalid_message_key")

        message = self.readers.read_current_message(key)
        if message is None or not self._same_key(message, key):
            return ReplyAssociation("denied", "canonical_message_not_found")
        if not _nonempty(message.reply_to_provider_message_id):
            return ReplyAssociation("unresolved", "message_has_no_exact_reply_target")

        scopes = tuple(self.readers.read_task_scopes(message))
        receipts = tuple(self.readers.read_operation_receipts(
            provider=message.provider,
            account_ref=message.account_ref,
            conversation_id=message.conversation_id,
            provider_message_id=message.reply_to_provider_message_id,
        ))
        # Readers are trust boundaries, not permission to return a near match.
        for receipt in receipts:
            if (receipt.provider != message.provider
                    or receipt.account_ref != message.account_ref
                    or receipt.conversation_id != message.conversation_id):
                return ReplyAssociation("denied", "operation_scope_mismatch")
            if receipt.provider_message_id != message.reply_to_provider_message_id:
                return ReplyAssociation("denied", "reply_target_mismatch")

        exact_receipts = [receipt for receipt in receipts
                          if receipt.provider_message_id
                          == message.reply_to_provider_message_id]
        # Repeated reads of the same immutable receipt are harmless; conflicting
        # rows with one operation ID are corruption and never resolve a task.
        by_operation: dict[str, DurableOperationReceipt] = {}
        for receipt in exact_receipts:
            prior = by_operation.get(receipt.operation_id)
            if prior is not None and prior != receipt:
                return ReplyAssociation("denied", "operation_receipt_conflict")
            by_operation[receipt.operation_id] = receipt
        exact_receipts = list(by_operation.values())
        if not exact_receipts:
            if not scopes:
                return ReplyAssociation("denied", "orphan_reply_target")
            return ReplyAssociation("unresolved", "no_exact_replied_to_operation")

        # An UNKNOWN, reserved, or otherwise nonterminal receipt is never treated
        # as proof of delivery, and may not be replaced by a guessed send.
        if any(receipt.status not in _ACCEPTED for receipt in exact_receipts):
            return ReplyAssociation("unresolved", "operation_outcome_unknown")
        if len(exact_receipts) != 1:
            return ReplyAssociation("unresolved", "multiple_exact_operation_receipts")

        receipt = exact_receipts[0]
        if (not _nonempty(receipt.operation_id, receipt.task_id,
                          receipt.actor_id, receipt.project_id,
                          receipt.contact_id, receipt.source_id)
                or type(receipt.version) is not int or receipt.version < 1):
            return ReplyAssociation("denied", "operation_receipt_scope_invalid")
        matches = [scope for scope in scopes
                   if scope.task_id == receipt.task_id]
        if not matches:
            return ReplyAssociation("denied", "task_source_scope_not_found")
        if len(matches) != 1:
            return ReplyAssociation("unresolved", "task_source_scope_ambiguous")
        scope = matches[0]
        if (not self._scope_matches(scope, message)
                or scope.actor_id != receipt.actor_id
                or scope.project_id != receipt.project_id
                or scope.version != receipt.version
                or scope.provider != receipt.provider
                or scope.account_ref != receipt.account_ref
                or scope.conversation_id != receipt.conversation_id):
            return ReplyAssociation("denied", "operation_scope_mismatch")
        if ((scope.contact_id and scope.contact_id != receipt.contact_id)
                or (scope.source_namespace
                    and scope.source_namespace != receipt.source_namespace)
                or (scope.source_id and scope.source_id != receipt.source_id)):
            return ReplyAssociation("denied", "operation_source_scope_mismatch")

        return ReplyAssociation(
            "linked", "exact_replied_to_operation", task_id=receipt.task_id,
            operation_id=receipt.operation_id, contact_id=receipt.contact_id,
            source_namespace=receipt.source_namespace,
            source_id=receipt.source_id,
            provider_message_id=receipt.provider_message_id,
        )
