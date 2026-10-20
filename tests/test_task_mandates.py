"""Durable task mandate policy tests. All provider boundaries stay untouched."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import sqlite3
import time

import pytest

from universal_userio.contracts import InboxMessage, UserPrincipal
from universal_userio.personal_tokens import PersonalTokens
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore
from universal_userio.task_mandates import TaskMandateError, TaskMandates


class NeverSendProvider:
    account_ref = "telegram:mandate-test"

    def __init__(self):
        self.calls = []

    def execute(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("U51 reservation must stop before provider I/O")


@pytest.fixture
def case(tmp_path):
    db = tmp_path / "userio.sqlite3"
    store = SQLiteUserIOStore(db)
    store.seed_owner("mandate-owner", "mandate-owner-password")
    owner = store.authenticate_credentials("mandate-owner", "mandate-owner-password")
    assert owner is not None
    account_ref = NeverSendProvider.account_ref
    store.register_account(account_id=account_ref, provider="telegram", display_name="Test",
                           can_read=True, can_reply=True, credential_ref="test-only")
    service = UserIOService(store, object(), object())
    provider = NeverSendProvider()
    service.messenger_providers = {account_ref: provider}
    incoming = InboxMessage("telegram", "message-1", "sender", "test source", 1,
                            peer_id="qualified-peer-1", conversation_kind="direct")
    conversation_id, _ = service.receive(incoming, route_id="telegram", account_ref=account_ref)
    yield db, store, owner, account_ref, conversation_id, provider
    store.close()


def issue(case, *, expires_at=None, max_sends=2, recipient_ids=None, principal=None):
    _, store, owner, account_ref, _, _provider = case
    return TaskMandates(store).issue(
        issuer=principal or owner,
        task_id="task-42",
        actor_id="integrator-7",
        account_ref=account_ref,
        channel="telegram",
        recipient_ids=recipient_ids or ["qualified-peer-1"],
        purpose="Contact the named partner about the scoped project",
        max_sends=max_sends,
        expires_at=expires_at or time.time() + 3600,
    )


def operation(case, *, operation_id="operation-00000001", **changes):
    _, _, _, account_ref, conversation_id, _provider = case
    value = {
        "operation_id": operation_id,
        "task_id": "task-42",
        "actor_id": "integrator-7",
        "account_ref": account_ref,
        "channel": "telegram",
        "conversation_id": conversation_id,
        "recipient_id": "qualified-peer-1",
        "purpose": "Contact the named partner about the scoped project",
        "body": "A bounded message for this exact mandate.",
        "attachments": ["a" * 64],
    }
    value.update(changes)
    return value


def test_issued_credential_is_private_and_reservation_is_durable(case):
    db, store, owner, _, _, _provider = case
    mandate = issue(case, max_sends=1)
    assert store.authenticate_token(mandate["token"]) is None
    principal = TaskMandates(store).authenticate_token(mandate["token"])
    assert principal.user_id == owner.user_id
    assert principal.mandate_id == mandate["mandate_id"]

    reserved = TaskMandates(store).reserve(principal, operation(case))
    assert reserved["status"] == "reserved"
    assert "body" not in reserved["operation"]
    assert reserved["operation"]["content_sha256"] == hashlib.sha256(
        operation(case)["body"].encode()).hexdigest()
    assert reserved["operation_hash"] == TaskMandates.operation_hash(
        operation(case), mandate_version=principal.mandate_version)
    assert TaskMandates(store).reserve(principal, operation(case)) == reserved

    store.close()
    reopened = SQLiteUserIOStore(db)
    try:
        resumed_principal = TaskMandates(reopened).authenticate_token(mandate["token"])
        assert TaskMandates(reopened).reserve(resumed_principal, operation(case)) == reserved
        with pytest.raises(TaskMandateError):
            TaskMandates(reopened).reserve(
                resumed_principal, operation(case, operation_id="operation-after-reopen"))
        assert reopened.authenticate_token(mandate["token"]) is None
    finally:
        reopened.close()
    assert case[5].calls == []


@pytest.mark.parametrize("changes", [
    {"task_id": "other-task"},
    {"actor_id": "forged-actor"},
    {"account_ref": "telegram:other-account"},
    {"channel": "max"},
    {"recipient_id": "not-qualified"},
    {"purpose": "different purpose"},
    {"owner_confirmed": True},
])
def test_operation_mismatch_fails_closed(case, changes):
    mandate = issue(case)
    principal = TaskMandates(case[1]).authenticate_token(mandate["token"])
    with pytest.raises(TaskMandateError):
        TaskMandates(case[1]).reserve(principal, operation(case, **changes))
    assert case[5].calls == []


@pytest.mark.parametrize("account_change", ["delete", "disable", "no_reply", "provider", "owner"])
def test_new_reservation_rechecks_live_account_but_exact_replay_is_historical(case, account_change):
    _, store, owner, account_ref, _, provider = case
    mandate = issue(case, max_sends=3)
    manager = TaskMandates(store)
    principal = manager.authenticate_token(mandate["token"])
    first = operation(case, operation_id="operation-historical-0001")
    saved = manager.reserve(principal, first)

    if account_change == "owner":
        new_owner = store.register_user("replacement-owner", "replacement-owner-password")
        update = ("UPDATE provider_accounts SET user_id=? WHERE user_id=? AND id=?",
                  (new_owner.user_id, owner.user_id, account_ref))
    elif account_change == "delete":
        update = ("DELETE FROM provider_accounts WHERE user_id=? AND id=?",
                  (owner.user_id, account_ref))
    elif account_change == "disable":
        update = ("UPDATE provider_accounts SET enabled=0 WHERE user_id=? AND id=?",
                  (owner.user_id, account_ref))
    elif account_change == "no_reply":
        update = ("UPDATE provider_accounts SET can_reply=0 WHERE user_id=? AND id=?",
                  (owner.user_id, account_ref))
    else:
        update = ("UPDATE provider_accounts SET provider='max' WHERE user_id=? AND id=?",
                  (owner.user_id, account_ref))
    with store._lock, store._connection:
        store._connection.execute(*update)

    # Exact retries are historical lookups only; they never create a new use.
    assert manager.reserve(principal, first) == saved
    with pytest.raises(TaskMandateError, match="account_not_currently_send_capable"):
        manager.reserve(principal, operation(case, operation_id="operation-after-account-change"))
    assert store._connection.execute(
        "SELECT COUNT(*) FROM task_mandate_operations WHERE mandate_id=?",
        (mandate["mandate_id"],),
    ).fetchone()[0] == 1
    assert provider.calls == []


def test_changed_idempotency_reuse_expiry_revocation_and_volume_are_denied(case, monkeypatch):
    mandate = issue(case, max_sends=1)
    manager = TaskMandates(case[1])
    principal = manager.authenticate_token(mandate["token"])
    op = operation(case)
    manager.reserve(principal, op)
    with pytest.raises(TaskMandateError):
        manager.reserve(principal, operation(case, body="changed", operation_id=op["operation_id"]))
    with pytest.raises(TaskMandateError):
        manager.reserve(principal, operation(case, attachments=["b" * 64],
                                              operation_id=op["operation_id"]))
    with pytest.raises(TaskMandateError):
        manager.reserve(principal, operation(case, operation_id="operation-00000002"))

    expired = issue(case)
    expired_principal = manager.authenticate_token(expired["token"])
    assert expired_principal is not None
    monkeypatch.setattr("universal_userio.task_mandates.time.time",
                        lambda: float(expired["expires_at"]) + 1)
    assert manager.authenticate_token(expired["token"]) is None
    with pytest.raises(TaskMandateError):
        manager.reserve(expired_principal, operation(case, operation_id="operation-expired"))
    monkeypatch.undo()

    revoked = issue(case)
    revoked_principal = manager.authenticate_token(revoked["token"])
    assert manager.revoke(case[2], revoked["mandate_id"])
    assert manager.authenticate_token(revoked["token"]) is None
    with pytest.raises(TaskMandateError):
        manager.reserve(revoked_principal, operation(case, operation_id="operation-revoked"))
    assert case[5].calls == []


def test_issuance_requires_live_owner_principal_and_rejects_asserted_authority(case):
    _, store, owner, _, _, _provider = case
    read_only = PersonalTokens(store).create(owner.user_id, "readonly", scope="read_only")
    readonly_principal = store.authenticate_token(read_only["token"])
    with pytest.raises(PermissionError):
        issue(case, principal=readonly_principal)
    with pytest.raises(PermissionError):
        issue(case, principal=UserPrincipal("forged-user", owner.username, "owner"))
    with pytest.raises(TypeError):
        TaskMandates(store).issue(
            issuer=owner, task_id="task-42", actor_id="integrator-7",
            account_ref=NeverSendProvider.account_ref, channel="telegram",
            recipient_ids=["qualified-peer-1"], purpose="scoped",
            max_sends=1, expires_at=time.time() + 3600, owner_confirmed=True)
    assert case[5].calls == []


def test_concurrent_distinct_reservations_cannot_exceed_limit(case):
    mandate = issue(case, max_sends=1)
    other_store = SQLiteUserIOStore(case[0])
    managers = (TaskMandates(case[1]), TaskMandates(other_store))
    token = mandate["token"]
    operations = [operation(case, operation_id=f"operation-{i:08d}") for i in (1, 2)]

    def reserve(index, value):
        manager = managers[index]
        current = manager.authenticate_token(token)
        try:
            return manager.reserve(current, value)["status"]
        except TaskMandateError:
            return "denied"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda pair: reserve(*pair), enumerate(operations)))
        assert sorted(results) == ["denied", "reserved"]
        assert case[5].calls == []
    finally:
        other_store.close()


def test_concurrent_revoke_and_reserve_never_reopens_the_mandate(case):
    mandate = issue(case, max_sends=2)
    other_store = SQLiteUserIOStore(case[0])
    manager_a, manager_b = TaskMandates(case[1]), TaskMandates(other_store)
    stale_principal = manager_a.authenticate_token(mandate["token"])

    def reserve():
        try:
            return manager_a.reserve(stale_principal,
                                     operation(case, operation_id="operation-race-0001"))["status"]
        except TaskMandateError:
            return "denied"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (pool.submit(reserve),
                       pool.submit(manager_b.revoke, case[2], mandate["mandate_id"]))
            reserve_result, revoked = (future.result() for future in futures)
        assert reserve_result in {"reserved", "denied"}
        assert revoked
        assert manager_b.authenticate_token(mandate["token"]) is None
        with pytest.raises(TaskMandateError):
            manager_a.reserve(stale_principal,
                              operation(case, operation_id="operation-after-revoke"))
        assert case[5].calls == []
    finally:
        other_store.close()


def test_attachment_hash_validation_is_strict(case):
    mandate = issue(case)
    principal = TaskMandates(case[1]).authenticate_token(mandate["token"])
    with pytest.raises(TaskMandateError):
        TaskMandates(case[1]).reserve(principal, operation(case, attachments=["not-a-sha256"]))
    assert case[5].calls == []


def test_mandate_policy_and_operation_reservations_are_database_immutable(case):
    mandate = issue(case)
    store = case[1]
    manager = TaskMandates(store)
    principal = manager.authenticate_token(mandate["token"])
    reserved = manager.reserve(principal, operation(case))
    with pytest.raises(sqlite3.IntegrityError):
        store._connection.execute("UPDATE task_mandates SET purpose='widened' WHERE id=?",
                                  (mandate["mandate_id"],))
    with pytest.raises(sqlite3.IntegrityError):
        store._connection.execute("UPDATE task_mandate_operations SET status='sent' "
                                  "WHERE operation_id=?", (reserved["operation_id"],))
    with pytest.raises(sqlite3.IntegrityError):
        store._connection.execute("DELETE FROM task_mandate_operations WHERE operation_id=?",
                                  (reserved["operation_id"],))
    assert store._connection.execute("SELECT purpose FROM task_mandates WHERE id=?",
                                     (mandate["mandate_id"],)).fetchone()[0] == \
        "Contact the named partner about the scoped project"
    assert case[5].calls == []


def test_pre_u51_database_migrates_and_preserves_existing_tokens_and_data(tmp_path):
    db = tmp_path / "pre-u51.sqlite3"
    store = SQLiteUserIOStore(db)
    store.seed_owner("legacy-owner", "legacy-owner-password")
    owner, owner_token = store.login("legacy-owner", "legacy-owner-password")
    readonly_token = PersonalTokens(store).create(owner.user_id, "legacy-readonly", scope="read_only")["token"]
    account_ref = NeverSendProvider.account_ref
    store.register_account(account_id=account_ref, provider="telegram", display_name="Legacy",
                           can_read=True, can_reply=True, credential_ref="test-only")
    service = UserIOService(store, object(), object())
    incoming = InboxMessage("telegram", "legacy-message", "sender", "kept history", 2,
                            peer_id="qualified-peer-1", conversation_kind="direct")
    conversation_id, _ = service.receive(incoming, route_id="telegram", account_ref=account_ref)
    store.close()

    # Strip only the U51 additions to form a disposable pre-U51 SQLite image
    # with representative existing owner/token/account/conversation rows.
    legacy = sqlite3.connect(db)
    for trigger in (
        "task_mandates_immutable_update", "task_mandates_no_delete",
        "task_mandate_operations_no_update", "task_mandate_operations_no_delete",
    ):
        legacy.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    legacy.execute("DROP TABLE task_mandate_operations")
    legacy.execute("DROP TABLE task_mandates")
    legacy.execute("DROP INDEX api_tokens_task_mandate_idx")
    legacy.execute("ALTER TABLE api_tokens DROP COLUMN mandate_id")
    legacy.commit()
    assert "mandate_id" not in {
        row[1] for row in legacy.execute("PRAGMA table_info(api_tokens)")
    }
    legacy.close()

    migrated = SQLiteUserIOStore(db)
    try:
        columns = {row["name"] for row in migrated._connection.execute("PRAGMA table_info(api_tokens)")}
        assert "mandate_id" in columns
        assert migrated.authenticate_token(owner_token).user_id == owner.user_id
        readonly = migrated.authenticate_token(readonly_token)
        assert readonly.user_id == owner.user_id and readonly.read_only
        assert any(a["id"] == account_ref and a["enabled"] for a in migrated.accounts(user_id=owner.user_id))
        conversation = migrated._connection.execute(
            "SELECT account_ref,source,peer_id FROM conversations WHERE user_id=? AND id=?",
            (owner.user_id, conversation_id),
        ).fetchone()
        assert tuple(conversation) == (account_ref, "telegram", "qualified-peer-1")
    finally:
        migrated.close()
