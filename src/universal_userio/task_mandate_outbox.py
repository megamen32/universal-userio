"""Internal task-mandated admission through UserIO's existing draft/outbox.

This module stores only the immutable authorization-to-draft join. Provider
attempts and receipts remain owned by the existing service and channel outbox.
No HTTP/MCP registration or second sender is introduced.
"""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager

from .contracts import DeliveryNotSubmittedError
from .task_contact_qualification import ContactQualificationError
from .task_mandates import TaskMandateError, TaskMandatePrincipal, _canonical


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class TaskMandateOutbox:
    def __init__(self, store, service, contact_qualification) -> None:
        self.store = store
        self.service = service
        self.contact_qualification = contact_qualification

    @contextmanager
    def _source_authority_guard(self, *, qualification_id: str, user_id: str,
                                action: str, binding: dict[str, object]):
        try:
            with self.contact_qualification.source_authority_guard(
                qualification_id, action=action, binding=binding, user_id=user_id,
            ) as lease:
                yield lease
        except ContactQualificationError as error:
            raise TaskMandateError("catalog_authority_guard_denied") from error

    @staticmethod
    def _table_exists(connection, name: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,),
        ).fetchone() is not None

    def _operation_qualification_id(self, principal: TaskMandatePrincipal,
                                    operation_id: str) -> str:
        row = self.store._connection.execute(
            "SELECT operation_json FROM task_mandate_operations "
            "WHERE user_id=? AND mandate_id=? AND operation_id=?",
            (principal.user_id, principal.mandate_id, operation_id),
        ).fetchone()
        if row is None:
            raise TaskMandateError("task_mandate_operation_not_found")
        try:
            value = json.loads(str(row["operation_json"]))["qualification_id"]
        except (ValueError, TypeError, KeyError):
            raise TaskMandateError("operation_qualification_missing") from None
        if not isinstance(value, str) or not value:
            raise TaskMandateError("operation_qualification_missing")
        return value

    def _context(self, principal: TaskMandatePrincipal,
                 operation_id: str) -> dict[str, object]:
        if not isinstance(principal, TaskMandatePrincipal):
            raise TaskMandateError("task_mandate_principal_required")
        qualification_id = self._operation_qualification_id(principal, operation_id)
        row = self.store._connection.execute(
            "SELECT m.id AS mandate_id,m.issuer_user_id,m.task_id,m.actor_id,m.account_ref,"
            "m.channel,m.purpose,m.max_sends,m.version,m.policy_hash,m.expires_at,m.revoked_at,"
            "o.operation_id,o.mandate_version,o.operation_hash,o.operation_json,o.status AS op_status,"
            "mq.qualification_id,mq.task_id AS linked_task_id,mq.actor_id AS linked_actor_id,"
            "mq.account_ref AS linked_account_ref,mq.provider AS linked_provider,"
            "mq.peer_id AS linked_peer_id,mq.binding_hash AS qualified_binding_hash "
            "FROM task_mandates m JOIN task_mandate_operations o "
            "ON o.user_id=m.user_id AND o.mandate_id=m.id "
            "JOIN task_mandate_qualifications mq "
            "ON mq.user_id=m.user_id AND mq.mandate_id=m.id "
            "WHERE m.user_id=? AND m.id=? AND o.operation_id=? "
            "AND mq.qualification_id=?",
            (principal.user_id, principal.mandate_id, operation_id, qualification_id),
        ).fetchone()
        if row is None:
            raise TaskMandateError("task_mandate_operation_not_found")
        if row["revoked_at"] is not None or float(row["expires_at"]) <= time.time():
            raise TaskMandateError("mandate_revoked_expired_or_changed")
        if (int(row["version"]) != principal.mandate_version
                or int(row["mandate_version"]) != principal.mandate_version):
            raise TaskMandateError("mandate_version_changed")
        operation = json.loads(str(row["operation_json"]))
        qualification = self.store.task_contact_qualification(
            qualification_id, user_id=principal.user_id,
        )
        if qualification is None:
            raise TaskMandateError("qualified_contact_not_found")
        if str(row["op_status"]) != "reserved":
            raise TaskMandateError("operation_not_reservable")
        if (str(row["task_id"]) != str(qualification["task_id"])
                or str(row["actor_id"]) != str(qualification["actor_id"])
                or str(row["account_ref"]) != str(qualification["account_ref"])
                or str(row["channel"]).lower() != str(qualification["provider"]).lower()
                or str(row["linked_task_id"]) != str(qualification["task_id"])
                or str(row["linked_actor_id"]) != str(qualification["actor_id"])
                or str(row["linked_account_ref"]) != str(qualification["account_ref"])
                or str(row["linked_provider"]).lower() != str(qualification["provider"]).lower()
                or str(row["linked_peer_id"]) != str(qualification["peer_id"])
                or operation.get("task_id") != str(row["task_id"])
                or operation.get("actor_id") != str(row["actor_id"])
                or operation.get("account_ref") != str(row["account_ref"])
                or operation.get("channel") != str(row["channel"])
                or operation.get("endpoint_id") != str(qualification["endpoint_id"])
                or operation.get("conversation_id") != str(qualification["conversation_id"])
                or operation.get("recipient_id") != str(qualification["peer_id"])
                or operation.get("purpose") != str(row["purpose"])
                or operation.get("qualification_id") != qualification_id
                or operation.get("catalog_binding_hash") != qualification["binding_hash"]
                or row["qualified_binding_hash"] != qualification["binding_hash"]):
            raise TaskMandateError("operation_catalog_binding_mismatch")
        if hashlib.sha256(_canonical(operation)).hexdigest() != str(row["operation_hash"]):
            raise TaskMandateError("operation_hash_conflict")
        return {**dict(row), **dict(qualification), "operation": operation}

    def _verify_catalog(self, context: dict[str, object], user_id: str) -> None:
        try:
            self.contact_qualification.verify_current(
                str(context["id"]), user_id=user_id,
                task_id=str(context["task_id"]), actor_id=str(context["actor_id"]),
                account_ref=str(context["account_ref"]), provider=str(context["channel"]),
                peer_id=str(context["peer_id"]),
                conversation_id=str(context["conversation_id"]),
                endpoint_id=str(context["endpoint_id"]),
                binding_hash=str(context["binding_hash"]),
            )
        except (ContactQualificationError, PermissionError, ValueError, KeyError) as error:
            raise TaskMandateError("catalog_qualification_not_current") from error

    def _verify_account(self, context: dict[str, object], user_id: str) -> None:
        account = self.store._connection.execute(
            "SELECT provider,enabled,can_reply FROM provider_accounts "
            "WHERE user_id=? AND id=?",
            (user_id, str(context["account_ref"])),
        ).fetchone()
        if (account is None or not bool(account["enabled"])
                or not bool(account["can_reply"])
                or str(account["provider"]).lower() != str(context["channel"]).lower()):
            raise TaskMandateError("account_not_currently_send_capable")
        if not self.store.send_enabled(user_id=user_id):
            raise TaskMandateError("outbound_delivery_disabled")

    def _attachment_hashes(self, *, user_id: str, draft_id: str,
                           conversation: dict[str, object]) -> list[str]:
        """Read the exact staged local file bytes; this performs no provider IO."""
        source = str(conversation["source"]).lower()
        table = {"vk": "vk_web_files", "max": "max_web_files"}.get(source)
        if table is None or not self._table_exists(self.store._connection, table):
            return []
        native_column = "native_ref" if source == "vk" else "native_attachment"
        link = self.store._connection.execute(
            f"SELECT upload_id,{native_column} AS native_ref FROM {table} "
            "WHERE user_id=? AND draft_id=?",
            (user_id, draft_id),
        ).fetchone()
        if link is None:
            return []
        upload = self.store._connection.execute(
            "SELECT * FROM messenger_uploads WHERE user_id=? AND id=?",
            (user_id, str(link["upload_id"])),
        ).fetchone()
        if upload is None or upload["action_id"] != draft_id or upload["data"] is None:
            raise TaskMandateError("draft_attachment_unavailable")
        try:
            from .messenger_uploads import MessengerUploads
            MessengerUploads._verify(upload)
            binding = json.loads(str(upload["binding"]))
            metadata = json.loads(str(upload["metadata"]))
        except (TypeError, ValueError, KeyError) as error:
            raise TaskMandateError("draft_attachment_unverifiable") from error
        expected = {key: conversation[key] for key in ("id", "account_ref", "peer_id")}
        digest = _sha256(bytes(upload["data"]))
        if binding != expected or digest != str(metadata.get("sha256") or ""):
            raise TaskMandateError("draft_attachment_source_or_hash_conflict")
        return [digest]

    def _verify_snapshot(self, context: dict[str, object], *, draft_id: str,
                         draft, conversation: dict[str, object],
                         attachments: list[str]) -> None:
        operation = context["operation"]
        checks = {
            "task_id": context["task_id"],
            "actor_id": context["actor_id"],
            "account_ref": context["account_ref"],
            "channel": context["channel"],
            "endpoint_id": context["endpoint_id"],
            "conversation_id": draft.conversation_id,
            "recipient_id": context["peer_id"],
            "qualification_id": context["id"],
            "catalog_binding_hash": context["binding_hash"],
            "purpose": context["purpose"],
        }
        if any(operation.get(key) != value for key, value in checks.items()):
            raise TaskMandateError("draft_operation_identity_conflict")
        if (conversation["id"] != draft.conversation_id
                or conversation["source"] != context["channel"]
                or conversation["account_ref"] != context["account_ref"]
                or conversation["peer_id"] != context["peer_id"]
                or conversation["route_id"] != context["endpoint_id"]):
            raise TaskMandateError("draft_conversation_binding_conflict")
        if _sha256(draft.body.encode("utf-8")) != operation.get("content_sha256"):
            raise TaskMandateError("draft_content_hash_conflict")
        if attachments != operation.get("attachment_sha256"):
            raise TaskMandateError("draft_attachment_snapshot_conflict")
        if not draft_id:
            raise TaskMandateError("invalid_draft_id")

    def _release_claimed(self, user_id: str, draft_id: str) -> None:
        self.store._connection.execute(
            "UPDATE drafts SET status='proposed',sending_at=NULL,delivery_state='not_submitted' "
            "WHERE user_id=? AND id=? AND status='sending' "
            "AND delivery_state='task_mandate_claimed'",
            (user_id, draft_id),
        )

    def claim(self, *, principal: TaskMandatePrincipal,
              operation_id: str, draft_id: str):
        """Join a reserved operation to one exact draft, under BEGIN IMMEDIATE."""
        if not isinstance(principal, TaskMandatePrincipal):
            raise TaskMandateError("task_mandate_principal_required")
        user_id = principal.user_id
        # An accepted receipt is a durable historical lookup. It remains
        # readable after revocation/expiry but can never invoke the provider.
        prior = self.store._connection.execute(
            "SELECT d.status,c.mandate_id,c.operation_id,c.operation_hash,"
            "o.operation_hash AS reserved_hash FROM drafts d "
            "JOIN task_mandate_draft_claims c ON c.user_id=d.user_id AND c.draft_id=d.id "
            "JOIN task_mandate_operations o ON o.user_id=c.user_id "
            "AND o.mandate_id=c.mandate_id AND o.operation_id=c.operation_id "
            "WHERE d.user_id=? AND d.id=?",
            (user_id, draft_id),
        ).fetchone()
        if prior is not None and prior["status"] == "approved":
            if (prior["mandate_id"] != principal.mandate_id
                    or prior["operation_id"] != operation_id
                    or prior["operation_hash"] != prior["reserved_hash"]):
                raise TaskMandateError("approved_draft_mandate_conflict")
            return self.store.draft(draft_id, user_id=user_id)
        self.store.expire_proposed_drafts(user_id=user_id)
        context = self._context(principal, operation_id)
        self._verify_catalog(context, user_id)
        draft = self.store.draft(draft_id, user_id=user_id)
        conversation = self.store.conversation(draft.conversation_id, user_id=user_id)
        if conversation is None:
            raise TaskMandateError("draft_conversation_not_found")
        attachments = self._attachment_hashes(
            user_id=user_id, draft_id=draft_id, conversation=conversation,
        )
        self._verify_snapshot(
            context, draft_id=draft_id, draft=draft,
            conversation=conversation, attachments=attachments,
        )
        authority_binding = self.contact_qualification.authority_binding(
            context, action="draft_claim",
            qualification_id=str(context["id"]),
            qualification_binding_hash=str(context["binding_hash"]),
            mandate_id=principal.mandate_id,
            mandate_version=principal.mandate_version,
            policy_hash=str(context["policy_hash"]),
            operation_id=operation_id,
            operation_hash=str(context["operation_hash"]),
            endpoint_id=str(context["endpoint_id"]),
            conversation_id=str(context["conversation_id"]),
            peer_id=str(context["peer_id"]), draft_id=draft_id,
            content_sha256=str(context["operation"]["content_sha256"]),
            attachment_sha256=list(context["operation"]["attachment_sha256"]),
        )
        guard = self._source_authority_guard(
            qualification_id=str(context["id"]), user_id=user_id,
            action="draft_claim", binding=authority_binding,
        )
        with guard as lease, self.store._lock, self.store._connection:
            self.store._connection.execute("BEGIN IMMEDIATE")
            context = self._context(principal, operation_id)
            mandate = self.store._connection.execute(
                "SELECT revoked_at,expires_at,version,issuer_user_id,policy_hash "
                "FROM task_mandates WHERE user_id=? AND id=?",
                (user_id, principal.mandate_id),
            ).fetchone()
            if (mandate is None or mandate["revoked_at"] is not None
                    or float(mandate["expires_at"]) <= time.time()
                    or str(mandate["issuer_user_id"]) != user_id
                    or str(mandate["policy_hash"]) != str(context["policy_hash"])
                    or int(mandate["version"]) != principal.mandate_version):
                raise TaskMandateError("mandate_revoked_expired_or_changed")
            self._verify_account(context, user_id)
            row = self.store._connection.execute(
                "SELECT status FROM drafts WHERE user_id=? AND id=?",
                (user_id, draft_id),
            ).fetchone()
            if row is None:
                raise KeyError("draft not found")
            if row["status"] == "approved":
                claim = self.store._connection.execute(
                    "SELECT mandate_id,operation_id,operation_hash,qualification_id,binding_hash "
                    "FROM task_mandate_draft_claims WHERE user_id=? AND draft_id=?",
                    (user_id, draft_id),
                ).fetchone()
                if (claim is None or claim["mandate_id"] != principal.mandate_id
                        or claim["operation_id"] != operation_id
                        or claim["operation_hash"] != context["operation_hash"]
                        or claim["qualification_id"] != context["id"]
                        or claim["binding_hash"] != context["binding_hash"]):
                    raise TaskMandateError("approved_draft_mandate_conflict")
                return self.store.draft(draft_id, user_id=user_id)
            if row["status"] == "sending":
                raise TaskMandateError("delivery_outcome_unknown")
            if row["status"] != "proposed":
                raise TaskMandateError("draft_not_sendable")
            draft = self.store.draft(draft_id, user_id=user_id)
            conversation = self.store.conversation(draft.conversation_id, user_id=user_id)
            if conversation is None:
                raise TaskMandateError("draft_conversation_not_found")
            attachments = self._attachment_hashes(
                user_id=user_id, draft_id=draft_id, conversation=conversation,
            )
            self._verify_snapshot(
                context, draft_id=draft_id, draft=draft,
                conversation=conversation, attachments=attachments,
            )
            claim = self.store._connection.execute(
                "SELECT mandate_id,operation_id,operation_hash,qualification_id,binding_hash "
                "FROM task_mandate_draft_claims WHERE user_id=? AND draft_id=?",
                (user_id, draft_id),
            ).fetchone()
            if claim is not None and (
                claim["mandate_id"] != principal.mandate_id
                or claim["operation_id"] != operation_id
                or claim["operation_hash"] != context["operation_hash"]
                or claim["qualification_id"] != context["id"]
                or claim["binding_hash"] != context["binding_hash"]
            ):
                raise TaskMandateError("draft_already_bound_to_another_operation")
            lease.assert_current(authority_binding)
            claim_now = time.time()
            if float(mandate["expires_at"]) <= claim_now:
                raise TaskMandateError("mandate_revoked_expired_or_changed")
            if claim is None:
                self.store._connection.execute(
                    "INSERT INTO task_mandate_draft_claims "
                    "(user_id,draft_id,mandate_id,operation_id,qualification_id,"
                    "operation_hash,binding_hash,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (user_id, draft_id, principal.mandate_id, operation_id,
                     str(context["id"]), str(context["operation_hash"]),
                     str(context["binding_hash"]), claim_now),
                )
            self.store._connection.execute(
                "UPDATE drafts SET status='sending',sending_at=?,delivery_state='task_mandate_claimed' "
                "WHERE user_id=? AND id=? AND status='proposed'",
                (claim_now, user_id, draft_id),
            )
            return draft

    def admit_provider(self, *, principal: TaskMandatePrincipal,
                       operation_id: str, draft_id: str) -> None:
        """Serialize revocation/account checks at the provider-admission point."""
        user_id = principal.user_id
        context = self._context(principal, operation_id)
        self._verify_catalog(context, user_id)
        claimed = self.store.draft(draft_id, user_id=user_id)
        conversation_before = self.store.conversation(
            claimed.conversation_id, user_id=user_id,
        )
        if conversation_before is None:
            raise TaskMandateError("draft_conversation_not_found")
        attachments_before = self._attachment_hashes(
            user_id=user_id, draft_id=draft_id, conversation=conversation_before,
        )
        self._verify_snapshot(
            context, draft_id=draft_id, draft=claimed,
            conversation=conversation_before, attachments=attachments_before,
        )
        authority_binding = self.contact_qualification.authority_binding(
            context, action="provider_admission",
            qualification_id=str(context["id"]),
            qualification_binding_hash=str(context["binding_hash"]),
            mandate_id=principal.mandate_id,
            mandate_version=principal.mandate_version,
            policy_hash=str(context["policy_hash"]),
            operation_id=operation_id,
            operation_hash=str(context["operation_hash"]),
            endpoint_id=str(context["endpoint_id"]),
            conversation_id=str(context["conversation_id"]),
            peer_id=str(context["peer_id"]), draft_id=draft_id,
            content_sha256=str(context["operation"]["content_sha256"]),
            attachment_sha256=list(context["operation"]["attachment_sha256"]),
        )
        guard = self._source_authority_guard(
            qualification_id=str(context["id"]), user_id=user_id,
            action="provider_admission", binding=authority_binding,
        )
        denied = None
        with guard as lease, self.store._lock, self.store._connection:
            self.store._connection.execute("BEGIN IMMEDIATE")
            context = self._context(principal, operation_id)
            mandate = self.store._connection.execute(
                "SELECT revoked_at,expires_at,version,issuer_user_id,policy_hash "
                "FROM task_mandates WHERE user_id=? AND id=?",
                (user_id, principal.mandate_id),
            ).fetchone()
            if (mandate is None or mandate["revoked_at"] is not None
                    or float(mandate["expires_at"]) <= time.time()
                    or str(mandate["issuer_user_id"]) != user_id
                    or str(mandate["policy_hash"]) != str(context["policy_hash"])
                    or int(mandate["version"]) != principal.mandate_version):
                denied = "mandate_revoked_expired_or_changed"
            else:
                try:
                    self._verify_account(context, user_id)
                except TaskMandateError as error:
                    denied = str(error)
            row = self.store._connection.execute(
                "SELECT conversation_id,status,body,delivery_state FROM drafts "
                "WHERE user_id=? AND id=?", (user_id, draft_id),
            ).fetchone()
            claim = self.store._connection.execute(
                "SELECT mandate_id,operation_id,operation_hash,qualification_id,binding_hash "
                "FROM task_mandate_draft_claims WHERE user_id=? AND draft_id=?",
                (user_id, draft_id),
            ).fetchone()
            if (row is None or row["status"] != "sending"
                    or row["delivery_state"] != "task_mandate_claimed"
                    or claim is None or claim["mandate_id"] != principal.mandate_id
                    or claim["operation_id"] != operation_id
                    or claim["operation_hash"] != context["operation_hash"]
                    or claim["qualification_id"] != context["id"]
                    or claim["binding_hash"] != context["binding_hash"]):
                denied = denied or "draft_pre_provider_claim_conflict"
            if row is not None and denied is None:
                draft = self.store.draft(draft_id, user_id=user_id)
                conversation = self.store.conversation(draft.conversation_id, user_id=user_id)
                if conversation is None:
                    denied = "draft_conversation_not_found"
                else:
                    try:
                        attachments = self._attachment_hashes(
                            user_id=user_id, draft_id=draft_id, conversation=conversation,
                        )
                        self._verify_snapshot(
                            context, draft_id=draft_id, draft=draft,
                            conversation=conversation, attachments=attachments,
                        )
                    except TaskMandateError as error:
                        denied = str(error)
            if denied is None:
                lease.assert_current(authority_binding)
                # This fresh trusted-clock read immediately before the durable
                # state change is the mandate-expiry linearization point.
                admission_now = time.time()
                if float(mandate["expires_at"]) <= admission_now:
                    denied = "mandate_revoked_expired_or_changed"
                else:
                    self.store._connection.execute(
                        "UPDATE drafts SET delivery_state='task_mandate_admitted' "
                        "WHERE user_id=? AND id=? AND status='sending' "
                        "AND delivery_state='task_mandate_claimed'",
                        (user_id, draft_id),
                    )
                    state = self.store._connection.execute(
                        "SELECT delivery_state FROM drafts WHERE user_id=? AND id=?",
                        (user_id, draft_id),
                    ).fetchone()
                    if state is None or state["delivery_state"] != "task_mandate_admitted":
                        denied = "provider_admission_conflict"
            if denied is not None:
                self._release_claimed(user_id, draft_id)
        if denied is not None:
            raise TaskMandateError(denied)

    def approve(self, *, principal: TaskMandatePrincipal,
                operation_id: str, draft_id: str):
        """Send once through the original UserIO provider closure and receipt."""
        draft = self.claim(principal=principal, operation_id=operation_id, draft_id=draft_id)
        if draft.status == "approved":
            return draft
        user_id = principal.user_id
        try:
            send = self.service._prepare_claimed_draft(draft, user_id=user_id)
        except Exception:
            with self.store._lock, self.store._connection:
                self._release_claimed(user_id, draft_id)
            raise
        try:
            self.admit_provider(
                principal=principal, operation_id=operation_id, draft_id=draft_id,
            )
        except Exception:
            # Only a not-yet-admitted claim can be released here.
            with self.store._lock, self.store._connection:
                self._release_claimed(user_id, draft_id)
            raise
        try:
            receipt = send()
        except DeliveryNotSubmittedError:
            self.store.release_draft_send(draft_id, user_id=user_id)
            raise
        return self.service._commit_accepted_draft(
            draft_id, receipt, user_id=user_id,
        )
