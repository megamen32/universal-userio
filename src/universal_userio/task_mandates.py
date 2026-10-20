"""Internal durable policy and reservation for task-bound outbound mandates.

This module intentionally has no MCP/HTTP registration. The current product has
no trusted B11 task-origin or human mandate-issuance route. Only trusted server
code holding a freshly authenticated full owner principal may issue a mandate;
the dedicated token is rejected by normal UserIO authentication and is accepted
only by this internal reservation boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
import secrets
import sqlite3
import time
import uuid

from .contracts import UserPrincipal


_MAX_RECIPIENTS = 500
_MAX_SENDS = 10_000
_MAX_TEXT_LENGTH = 12_000
_MAX_ATTACHMENTS = 8
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_OPERATION_FIELDS = frozenset({
    "operation_id", "task_id", "actor_id", "account_ref", "channel",
    "conversation_id", "recipient_id", "purpose", "body", "attachments",
})


class TaskMandateError(ValueError):
    """The mandate does not authorize this exact reservation."""


@dataclass(frozen=True, slots=True)
class TaskMandatePrincipal:
    """Narrow internal identity; deliberately not a UserPrincipal."""

    user_id: str
    mandate_id: str
    mandate_version: int


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class TaskMandates:
    """Owner-issued immutable policy plus atomic, idempotent reservations."""

    def __init__(self, store) -> None:
        self.store = store

    def _assert_issuer(self, issuer: UserPrincipal) -> None:
        if (not isinstance(issuer, UserPrincipal) or issuer.role != "owner"
                or issuer.service_account or issuer.read_only
                or issuer.messenger_accounts is not None
                or issuer.messenger_grants is not None
                or issuer.meeting_recorder
                or getattr(issuer, "task_mandate_id", None) is not None):
            raise PermissionError("task_mandate_owner_authentication_required")
        row = self.store._connection.execute(
            "SELECT username,role FROM users WHERE id=?", (issuer.user_id,)
        ).fetchone()
        if (row is None or str(row["role"]) != "owner"
                or str(row["username"]).casefold() != issuer.username.casefold()):
            raise PermissionError("task_mandate_owner_authentication_required")

    @staticmethod
    def _required_text(name: str, value: object, *, limit: int) -> str:
        if not isinstance(value, str):
            raise TaskMandateError(f"invalid_{name}")
        value = value.strip()
        if not value or len(value) > limit:
            raise TaskMandateError(f"invalid_{name}")
        return value

    def issue(
        self,
        *,
        issuer: UserPrincipal,
        task_id: str,
        actor_id: str,
        account_ref: str,
        channel: str,
        recipient_ids: list[str],
        purpose: str,
        max_sends: int,
        expires_at: float,
    ) -> dict[str, object]:
        """Create an internal grant. No current model/API path invokes this."""
        task_id = self._required_text("task_id", task_id, limit=200)
        actor_id = self._required_text("actor_id", actor_id, limit=200)
        account_ref = self._required_text("account_ref", account_ref, limit=256)
        channel = self._required_text("channel", channel, limit=80).lower()
        purpose = self._required_text("purpose", purpose, limit=1000)
        if type(max_sends) is not int or not 1 <= max_sends <= _MAX_SENDS:
            raise TaskMandateError("invalid_max_sends")
        if (isinstance(expires_at, bool) or not isinstance(expires_at, (int, float))
                or not math.isfinite(float(expires_at))):
            raise TaskMandateError("invalid_expiry")
        expires_at = float(expires_at)
        if expires_at <= time.time():
            raise TaskMandateError("mandate_expired")
        if (not isinstance(recipient_ids, list) or not 1 <= len(recipient_ids) <= _MAX_RECIPIENTS
                or any(not isinstance(value, str) or not value.strip() for value in recipient_ids)):
            raise TaskMandateError("invalid_recipient_allowlist")
        recipient_ids = [value.strip() for value in recipient_ids]
        if len(set(recipient_ids)) != len(recipient_ids):
            raise TaskMandateError("invalid_recipient_allowlist")

        with self.store._lock, self.store._connection:
            self.store._connection.execute("BEGIN IMMEDIATE")
            now = time.time()
            if expires_at <= now:
                raise TaskMandateError("mandate_expired")
            self._assert_issuer(issuer)
            account = self.store._connection.execute(
                "SELECT provider,enabled,can_reply FROM provider_accounts "
                "WHERE user_id=? AND id=?",
                (issuer.user_id, account_ref),
            ).fetchone()
            if (account is None or not bool(account["enabled"])
                    or not bool(account["can_reply"])
                    or str(account["provider"]).lower() != channel):
                raise TaskMandateError("account_channel_not_owned_and_send_capable")
            for recipient_id in recipient_ids:
                recipient = self.store._connection.execute(
                    "SELECT 1 FROM conversations WHERE user_id=? AND account_ref=? "
                    "AND source=? AND peer_id=? LIMIT 1",
                    (issuer.user_id, account_ref, channel, recipient_id),
                ).fetchone()
                if recipient is None:
                    raise TaskMandateError("recipient_not_qualified_for_account")
            mandate_id = "mandate_" + uuid.uuid4().hex
            token = "uio_" + secrets.token_urlsafe(32)
            policy = {
                "id": mandate_id,
                "issuer_user_id": issuer.user_id,
                "task_id": task_id,
                "actor_id": actor_id,
                "account_ref": account_ref,
                "channel": channel,
                "recipient_ids": recipient_ids,
                "purpose": purpose,
                "max_sends": max_sends,
                "version": 1,
                "created_at": now,
                "expires_at": expires_at,
            }
            policy_hash = _sha256(_canonical(policy))
            self.store._connection.execute(
                "INSERT INTO task_mandates "
                "(user_id,id,issuer_user_id,task_id,actor_id,account_ref,channel,"
                "recipient_ids_json,purpose,max_sends,version,policy_hash,created_at,"
                "expires_at,revoked_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                (issuer.user_id, mandate_id, issuer.user_id, task_id, actor_id,
                 account_ref, channel, json.dumps(recipient_ids, ensure_ascii=False),
                 purpose, max_sends, 1, policy_hash, now, expires_at),
            )
            self.store._connection.execute(
                "INSERT INTO api_tokens "
                "(id,user_id,token_hash,created_at,revoked_at,kind,token_type,expires_at,"
                "scope,mandate_id) VALUES (?,?,?, ?,NULL,'task_mandate','access',?,'task_mandate',?)",
                ("token_" + uuid.uuid4().hex, issuer.user_id,
                 self.store._token_hash(token), now, expires_at, mandate_id),
            )
        return {
            "mandate_id": mandate_id,
            "version": 1,
            "policy_hash": policy_hash,
            "expires_at": expires_at,
            "token": token,
            "token_returned_once": True,
        }

    def authenticate_token(self, token: str) -> TaskMandatePrincipal | None:
        """Authenticate only for internal reservation; general UserIO auth denies it."""
        if not isinstance(token, str) or not token.startswith("uio_"):
            return None
        now = time.time()
        with self.store._lock:
            row = self.store._connection.execute(
                "SELECT t.user_id,t.mandate_id,m.version FROM api_tokens t "
                "JOIN task_mandates m ON m.user_id=t.user_id AND m.id=t.mandate_id "
                "WHERE t.token_hash=? AND t.kind='task_mandate' AND t.scope='task_mandate' "
                "AND t.token_type='access' AND t.revoked_at IS NULL "
                "AND t.expires_at>? AND m.revoked_at IS NULL AND m.expires_at>?",
                (self.store._token_hash(token), now, now),
            ).fetchone()
        if row is None or not row["mandate_id"]:
            return None
        return TaskMandatePrincipal(str(row["user_id"]), str(row["mandate_id"]), int(row["version"]))

    def revoke(self, issuer: UserPrincipal, mandate_id: str) -> bool:
        if not isinstance(mandate_id, str) or not mandate_id.startswith("mandate_"):
            raise TaskMandateError("invalid_mandate_id")
        now = time.time()
        with self.store._lock, self.store._connection:
            self.store._connection.execute("BEGIN IMMEDIATE")
            self._assert_issuer(issuer)
            result = self.store._connection.execute(
                "UPDATE task_mandates SET revoked_at=?,version=version+1 "
                "WHERE user_id=? AND id=? AND revoked_at IS NULL",
                (now, issuer.user_id, mandate_id),
            )
            if result.rowcount != 1:
                return False
            self.store._connection.execute(
                "UPDATE api_tokens SET revoked_at=? WHERE user_id=? AND mandate_id=? "
                "AND kind='task_mandate' AND revoked_at IS NULL",
                (now, issuer.user_id, mandate_id),
            )
            return True

    @staticmethod
    def _normalize_operation(operation: dict[str, object], mandate_version: int) -> dict[str, object]:
        if not isinstance(operation, dict) or set(operation) != _OPERATION_FIELDS:
            raise TaskMandateError("invalid_operation_shape")
        text = TaskMandates._required_text
        operation_id = text("operation_id", operation["operation_id"], limit=128)
        if len(operation_id) < 16:
            raise TaskMandateError("invalid_operation_id")
        body = operation["body"]
        if not isinstance(body, str) or not body.strip() or len(body) > _MAX_TEXT_LENGTH:
            raise TaskMandateError("invalid_operation_body")
        attachments = operation["attachments"]
        if (not isinstance(attachments, list) or len(attachments) > _MAX_ATTACHMENTS
                or any(not isinstance(item, str) or not _SHA256.fullmatch(item) for item in attachments)):
            raise TaskMandateError("invalid_attachment_hashes")
        return {
            "operation_id": operation_id,
            "task_id": text("task_id", operation["task_id"], limit=200),
            "actor_id": text("actor_id", operation["actor_id"], limit=200),
            "account_ref": text("account_ref", operation["account_ref"], limit=256),
            "channel": text("channel", operation["channel"], limit=80).lower(),
            "conversation_id": text("conversation_id", operation["conversation_id"], limit=256),
            "recipient_id": text("recipient_id", operation["recipient_id"], limit=512),
            "purpose": text("purpose", operation["purpose"], limit=1000),
            "content_sha256": _sha256(body.encode("utf-8")),
            "attachment_sha256": list(attachments),
            "mandate_version": mandate_version,
        }

    @staticmethod
    def operation_hash(operation: dict[str, object], *, mandate_version: int = 1) -> str:
        normalized = TaskMandates._normalize_operation(operation, mandate_version)
        return _sha256(_canonical(normalized))

    def reserve(
        self, principal: TaskMandatePrincipal, operation: dict[str, object]
    ) -> dict[str, object]:
        if not isinstance(principal, TaskMandatePrincipal):
            raise TaskMandateError("task_mandate_principal_required")
        normalized = self._normalize_operation(operation, principal.mandate_version)
        operation_hash = _sha256(_canonical(normalized))
        user_id, mandate_id = principal.user_id, principal.mandate_id
        with self.store._lock, self.store._connection:
            self.store._connection.execute("BEGIN IMMEDIATE")
            now = time.time()
            existing = self.store._connection.execute(
                "SELECT operation_id,mandate_version,operation_hash,operation_json,status,created_at "
                "FROM task_mandate_operations WHERE user_id=? AND mandate_id=? AND operation_id=?",
                (user_id, mandate_id, normalized["operation_id"]),
            ).fetchone()
            if existing is not None:
                if existing["operation_hash"] != operation_hash:
                    raise TaskMandateError("idempotency_key_payload_conflict")
                return self._public_operation(existing)

            mandate = self.store._connection.execute(
                "SELECT * FROM task_mandates WHERE user_id=? AND id=?",
                (user_id, mandate_id),
            ).fetchone()
            if mandate is None or mandate["revoked_at"] is not None:
                raise TaskMandateError("mandate_revoked_or_missing")
            if str(mandate["issuer_user_id"]) != user_id:
                raise TaskMandateError("mandate_owner_mismatch")
            if float(mandate["expires_at"]) <= now:
                raise TaskMandateError("mandate_expired")
            version = int(mandate["version"])
            if version != principal.mandate_version:
                raise TaskMandateError("mandate_version_changed")
            account = self.store._connection.execute(
                "SELECT provider,enabled,can_reply FROM provider_accounts "
                "WHERE user_id=? AND id=?",
                (user_id, str(mandate["account_ref"])),
            ).fetchone()
            if (account is None or not bool(account["enabled"])
                    or not bool(account["can_reply"])
                    or str(account["provider"]).lower() != str(mandate["channel"]).lower()):
                raise TaskMandateError("account_not_currently_send_capable")
            for field in ("task_id", "actor_id", "account_ref", "channel", "purpose"):
                if normalized[field] != str(mandate[field]):
                    raise TaskMandateError(f"mandate_{field}_mismatch")
            allowed_recipients = json.loads(str(mandate["recipient_ids_json"]))
            if normalized["recipient_id"] not in allowed_recipients:
                raise TaskMandateError("recipient_not_allowlisted")
            conversation = self.store._connection.execute(
                "SELECT account_ref,source,peer_id FROM conversations "
                "WHERE user_id=? AND id=?",
                (user_id, normalized["conversation_id"]),
            ).fetchone()
            if (conversation is None or str(conversation["account_ref"]) != str(mandate["account_ref"])
                    or str(conversation["source"]).lower() != str(mandate["channel"])
                    or str(conversation["peer_id"]) != normalized["recipient_id"]):
                raise TaskMandateError("recipient_conversation_mismatch")
            count = int(self.store._connection.execute(
                "SELECT COUNT(*) FROM task_mandate_operations WHERE user_id=? AND mandate_id=?",
                (user_id, mandate_id),
            ).fetchone()[0])
            if count >= int(mandate["max_sends"]):
                raise TaskMandateError("mandate_volume_exhausted")
            self.store._connection.execute(
                "INSERT INTO task_mandate_operations "
                "(user_id,mandate_id,operation_id,mandate_version,operation_hash,"
                "operation_json,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (user_id, mandate_id, normalized["operation_id"], version,
                 operation_hash, _canonical(normalized).decode("utf-8"), "reserved", now),
            )
            row = self.store._connection.execute(
                "SELECT operation_id,mandate_version,operation_hash,operation_json,status,created_at "
                "FROM task_mandate_operations WHERE user_id=? AND mandate_id=? AND operation_id=?",
                (user_id, mandate_id, normalized["operation_id"]),
            ).fetchone()
            return self._public_operation(row)

    @staticmethod
    def _public_operation(row: sqlite3.Row) -> dict[str, object]:
        return {
            "operation_id": str(row["operation_id"]),
            "mandate_version": int(row["mandate_version"]),
            "operation_hash": str(row["operation_hash"]),
            "operation": json.loads(str(row["operation_json"])),
            "status": str(row["status"]),
            "created_at": float(row["created_at"]),
        }
