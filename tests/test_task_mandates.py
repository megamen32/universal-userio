"""Durable task mandate policy tests. All provider boundaries stay untouched."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from universal_userio.contracts import InboxMessage, UserPrincipal
from universal_userio.personal_tokens import PersonalTokens
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore
from universal_userio.task_contact_qualification import (
    CatalogContactProof, ContactQualificationError, ProviderContactIdentity, TaskContactQualification,
)
from universal_userio.task_mandates import TaskMandateError, TaskMandates


class NeverSendProvider:
    account_ref = "test:mandate-test"

    def __init__(self):
        self.calls = []

    def execute(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("U51 reservation must stop before provider I/O")


class FakeCatalogOrigin:
    def __init__(self):
        self.revision = "source-v1"
        self.revoked = False
        self._authority_lock = threading.RLock()
        self.on_guard_acquired = None

    def resolve_contact(self, *, user_id, task_id, actor_id, contact_ref):
        if contact_ref != "catalog-contact-ref-1":
            return None
        return CatalogContactProof(
            user_id=user_id, task_id=task_id, actor_id=actor_id,
            project_id="integrator-project-1",
            catalog_namespace="airlockplan.integrators",
            catalog_record_id="integrator-record-1",
            catalog_contact_id="integrator-contact-1",
            catalog_revision=self.revision, contact_version="contact-v1",
            provider="test-provider", account_ref=NeverSendProvider.account_ref,
            provider_locator_kind="provider_native_ref",
            provider_locator="catalog-native-ref-1", expires_at=time.time() + 7200,
        )

    def revalidate_contact(self, qualification):
        return (not self.revoked
                and qualification.get("catalog_namespace") == "airlockplan.integrators"
                and qualification.get("catalog_record_id") == "integrator-record-1"
                and qualification.get("catalog_contact_id") == "integrator-contact-1"
                and qualification.get("catalog_revision") == self.revision)

    def revoke(self):
        with self._authority_lock:
            self.revoked = True

    @contextmanager
    def acquire_authority_guard(self, *, proof, action, binding):
        with self._authority_lock:
            proofs = proof if isinstance(proof, list) else [proof]
            if (self.revoked or any(
                item.get("catalog_revision") != self.revision
                or item.get("catalog_namespace") != "airlockplan.integrators"
                for item in proofs
            )):
                raise ContactQualificationError("source authority denied")
            lease = FakeCatalogLease(self, proof, action, binding)
            if self.on_guard_acquired is not None:
                self.on_guard_acquired(action, dict(binding))
            yield lease


class FakeCatalogLease:
    def __init__(self, owner, proof, action, binding):
        self.owner = owner
        self.proofs = [dict(item) for item in proof] if isinstance(proof, list) else [dict(proof)]
        self.action = action
        self.binding = None
        self.bind(binding)

    def bind(self, binding):
        if binding.get("action") != self.action:
            raise ContactQualificationError("authority action conflict")
        if ("endpoint_id" in binding
                and binding["endpoint_id"] != binding.get("provider")):
            raise ContactQualificationError("authority endpoint conflict")
        if any(item["endpoint_id"] != item["provider"]
               for item in binding.get("qualified_contacts", [])):
            raise ContactQualificationError("authority qualified endpoint conflict")
        if self.binding is not None and any(
            key not in binding or binding[key] != value
            for key, value in self.binding.items()
        ):
            raise ContactQualificationError("authority binding changed")
        if any(binding.get(key) != value for proof in self.proofs
               for key, value in proof.items() if key in binding and key != "expires_at"):
            raise ContactQualificationError("authority source scope conflict")
        if "qualified_contacts" in binding and any(
            not any(all(contact.get(key) == value for key, value in proof.items()
                        if key != "expires_at")
                    for contact in binding["qualified_contacts"])
            for proof in self.proofs
        ):
            raise ContactQualificationError("authority batch source scope conflict")
        self.binding = dict(binding)

    def assert_current(self, binding):
        if (self.owner.revoked
                or any(proof.get("catalog_revision") != self.owner.revision
                       for proof in self.proofs)
                or dict(binding) != self.binding):
            raise ContactQualificationError("authority fence denied")


class FakeContactProvider:
    def __init__(self):
        self.lookup_calls = []

    def resolve_catalog_contact(self, *, account_ref, locator_kind, locator, user_id):
        self.lookup_calls.append((account_ref, locator_kind, locator, user_id))
        if locator != "catalog-native-ref-1" or locator_kind != "provider_native_ref":
            raise ValueError("unexpected catalog locator")
        return ProviderContactIdentity(
            provider="test-provider", account_ref=account_ref,
            peer_id="qualified-peer-1", peer_kind="direct",
            route_id="test-provider", display_name="Verified Contact",
        )


@pytest.fixture
def case(tmp_path):
    db = tmp_path / "userio.sqlite3"
    store = SQLiteUserIOStore(db)
    store.seed_owner("mandate-owner", "mandate-owner-password")
    owner = store.authenticate_credentials("mandate-owner", "mandate-owner-password")
    assert owner is not None
    account_ref = NeverSendProvider.account_ref
    store.register_account(account_id=account_ref, provider="test-provider", display_name="Test",
                           can_read=True, can_reply=True, credential_ref="test-only")
    origin, resolver = FakeCatalogOrigin(), FakeContactProvider()
    provider = NeverSendProvider()
    service = UserIOService(store, object(), provider,
                            task_catalog_origin=origin,
                            task_contact_provider_resolvers={"test-provider": resolver})
    store.bind_channel_route(user_id=owner.user_id, source="test-provider", route_id="test-provider")
    incoming = InboxMessage("test-provider", "message-1", "sender", "test source", 1,
                            peer_id="qualified-peer-1", conversation_kind="direct")
    conversation_id, _ = service.receive(incoming, route_id="test-provider", account_ref=account_ref)
    qualification = service.qualify_task_contact(
        issuer=owner, task_id="task-42", actor_id="integrator-7",
        contact_ref="catalog-contact-ref-1", account_ref=account_ref,
    )
    assert qualification["conversation_id"] == conversation_id
    yield db, store, owner, account_ref, conversation_id, provider, qualification, service, origin, resolver
    store.close()


def manager(case, store=None):
    target_store = store or case[1]
    qualification = TaskContactQualification(
        target_store, catalog_origin=case[8],
        provider_resolvers={"test-provider": case[9]},
    )
    return TaskMandates(target_store, qualification)


def issue(case, *, expires_at=None, max_sends=2, qualification_ids=None, principal=None):
    _, store, owner, account_ref, _, _provider, qualification, _service, *_rest = case
    return manager(case).issue(
        issuer=principal or owner,
        task_id="task-42",
        actor_id="integrator-7",
        account_ref=account_ref,
        channel="test-provider",
        qualification_ids=qualification_ids or [qualification["qualification_id"]],
        purpose="Contact the named partner about the scoped project",
        max_sends=max_sends,
        expires_at=expires_at or time.time() + 3600,
    )


def operation(case, *, operation_id="operation-00000001", **changes):
    _, _, _, account_ref, conversation_id, _provider, qualification, *_rest = case
    value = {
        "operation_id": operation_id,
        "task_id": "task-42",
        "actor_id": "integrator-7",
        "account_ref": account_ref,
        "channel": "test-provider",
        "conversation_id": conversation_id,
        "endpoint_id": qualification["endpoint_id"],
        "recipient_id": "qualified-peer-1",
        "qualification_id": qualification["qualification_id"],
        "catalog_binding_hash": qualification["binding_hash"],
        "purpose": "Contact the named partner about the scoped project",
        "body": "A bounded message for this exact mandate.",
        "attachments": ["a" * 64],
    }
    value.update(changes)
    return value


def test_issued_credential_is_private_and_reservation_is_durable(case):
    db, store, owner, *_rest = case
    mandate = issue(case, max_sends=1)
    assert store.authenticate_token(mandate["token"]) is None
    principal = manager(case).authenticate_token(mandate["token"])
    assert principal.user_id == owner.user_id
    assert principal.mandate_id == mandate["mandate_id"]

    reserved = manager(case).reserve(principal, operation(case))
    assert reserved["status"] == "reserved"
    assert "body" not in reserved["operation"]
    assert reserved["operation"]["content_sha256"] == hashlib.sha256(
        operation(case)["body"].encode()).hexdigest()
    assert reserved["operation_hash"] == TaskMandates.operation_hash(
        operation(case), mandate_version=principal.mandate_version)
    assert manager(case).reserve(principal, operation(case)) == reserved

    store.close()
    reopened = SQLiteUserIOStore(db)
    try:
        resumed = manager(case, reopened)
        resumed_principal = resumed.authenticate_token(mandate["token"])
        assert resumed.reserve(resumed_principal, operation(case)) == reserved
        with pytest.raises(TaskMandateError):
            resumed.reserve(
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
    {"endpoint_id": "different-endpoint"},
    {"recipient_id": "not-qualified"},
    {"purpose": "different purpose"},
    {"owner_confirmed": True},
])
def test_operation_mismatch_fails_closed(case, changes):
    mandate = issue(case)
    principal = manager(case).authenticate_token(mandate["token"])
    with pytest.raises(TaskMandateError):
        manager(case).reserve(principal, operation(case, **changes))
    assert case[5].calls == []


@pytest.mark.parametrize("account_change", ["delete", "disable", "no_reply", "provider", "owner"])
def test_new_reservation_rechecks_live_account_but_exact_replay_is_historical(case, account_change):
    _, store, owner, account_ref, _, provider, *_rest = case
    mandate = issue(case, max_sends=3)
    mandates = manager(case, store)
    principal = mandates.authenticate_token(mandate["token"])
    first = operation(case, operation_id="operation-historical-0001")
    saved = mandates.reserve(principal, first)

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
    assert mandates.reserve(principal, first) == saved
    with pytest.raises(TaskMandateError, match="account_not_currently_send_capable"):
        mandates.reserve(principal, operation(case, operation_id="operation-after-account-change"))
    assert store._connection.execute(
        "SELECT COUNT(*) FROM task_mandate_operations WHERE mandate_id=?",
        (mandate["mandate_id"],),
    ).fetchone()[0] == 1
    assert provider.calls == []


def test_changed_idempotency_reuse_expiry_revocation_and_volume_are_denied(case, monkeypatch):
    mandate = issue(case, max_sends=1)
    mandates = manager(case)
    principal = mandates.authenticate_token(mandate["token"])
    op = operation(case)
    mandates.reserve(principal, op)
    with pytest.raises(TaskMandateError):
        mandates.reserve(principal, operation(case, body="changed", operation_id=op["operation_id"]))
    with pytest.raises(TaskMandateError):
        mandates.reserve(principal, operation(case, attachments=["b" * 64],
                                              operation_id=op["operation_id"]))
    with pytest.raises(TaskMandateError):
        mandates.reserve(principal, operation(case, endpoint_id="changed-endpoint",
                                              operation_id=op["operation_id"]))
    with pytest.raises(TaskMandateError):
        mandates.reserve(principal, operation(case, operation_id="operation-00000002"))

    expired = issue(case)
    expired_principal = mandates.authenticate_token(expired["token"])
    assert expired_principal is not None
    monkeypatch.setattr("universal_userio.task_mandates.time.time",
                        lambda: float(expired["expires_at"]) + 1)
    assert mandates.authenticate_token(expired["token"]) is None
    with pytest.raises(TaskMandateError):
        mandates.reserve(expired_principal, operation(case, operation_id="operation-expired"))
    monkeypatch.undo()

    revoked = issue(case)
    revoked_principal = mandates.authenticate_token(revoked["token"])
    assert mandates.revoke(case[2], revoked["mandate_id"])
    assert mandates.authenticate_token(revoked["token"]) is None
    with pytest.raises(TaskMandateError):
        mandates.reserve(revoked_principal, operation(case, operation_id="operation-revoked"))
    assert case[5].calls == []


def test_issuance_requires_live_owner_principal_and_rejects_asserted_authority(case):
    _, store, owner, *_rest = case
    read_only = PersonalTokens(store).create(owner.user_id, "readonly", scope="read_only")
    readonly_principal = store.authenticate_token(read_only["token"])
    with pytest.raises(PermissionError):
        issue(case, principal=readonly_principal)
    with pytest.raises(PermissionError):
        issue(case, principal=UserPrincipal("forged-user", owner.username, "owner"))
    with pytest.raises(TaskMandateError, match="catalog_origin_unavailable"):
        TaskMandates(store).issue(
            issuer=owner, task_id="task-42", actor_id="integrator-7",
            account_ref=NeverSendProvider.account_ref, channel="test-provider",
            qualification_ids=[case[6]["qualification_id"]], purpose="scoped",
            max_sends=1, expires_at=time.time() + 3600,
        )
    with pytest.raises(TypeError):
        TaskMandates(store).issue(
            issuer=owner, task_id="task-42", actor_id="integrator-7",
            account_ref=NeverSendProvider.account_ref, channel="test-provider",
            qualification_ids=[case[6]["qualification_id"]], purpose="scoped",
            max_sends=1, expires_at=time.time() + 3600, owner_confirmed=True)
    assert case[5].calls == []


def test_mandate_issue_source_revocation_between_boolean_check_and_guard_is_denied(case):
    _, store, owner, account_ref, _, _provider, _qualification, _service, origin, resolver = case

    class RevokeAfterBooleanCheck:
        def __getattr__(self, name):
            return getattr(origin, name)

        def revalidate_contact(self, qualification):
            result = origin.revalidate_contact(qualification)
            origin.revoke()
            return result

    guarded = TaskMandates(store, TaskContactQualification(
        store, catalog_origin=RevokeAfterBooleanCheck(),
        provider_resolvers={"test-provider": resolver},
    ))
    with pytest.raises(TaskMandateError, match="catalog_authority_guard_denied"):
        guarded.issue(
            issuer=owner, task_id="task-42", actor_id="integrator-7",
            account_ref=account_ref, channel="test-provider",
            qualification_ids=[_qualification["qualification_id"]],
            purpose="Contact the named partner about the scoped project",
            max_sends=1, expires_at=time.time() + 3600,
        )

    assert store._connection.execute(
        "SELECT COUNT(*) FROM task_mandates WHERE user_id=?", (owner.user_id,),
    ).fetchone()[0] == 0
    assert store._connection.execute(
        "SELECT COUNT(*) FROM task_mandate_qualifications WHERE user_id=?",
        (owner.user_id,),
    ).fetchone()[0] == 0
    assert case[5].calls == []


def test_mandate_issue_expiring_after_final_source_assert_persists_nothing(case, monkeypatch):
    _, store, owner, *_rest = case
    deadline = time.time() + 5
    clock = [deadline - 1]
    from universal_userio import task_mandates as mandate_module
    monkeypatch.setattr(mandate_module, "time", SimpleNamespace(time=lambda: clock[0]))
    original_assert = FakeCatalogLease.assert_current
    issue_assertions = 0

    def expire_after_issue_lease_check(lease, binding):
        nonlocal issue_assertions
        original_assert(lease, binding)
        if lease.action == "mandate_issue":
            issue_assertions += 1
            if issue_assertions == 2:
                clock[0] = deadline + 1

    monkeypatch.setattr(FakeCatalogLease, "assert_current", expire_after_issue_lease_check)
    with pytest.raises(TaskMandateError, match="mandate_expired"):
        issue(case, expires_at=deadline)

    assert store._connection.execute(
        "SELECT COUNT(*) FROM task_mandates WHERE user_id=?", (owner.user_id,),
    ).fetchone()[0] == 0
    assert store._connection.execute(
        "SELECT COUNT(*) FROM api_tokens WHERE user_id=? AND kind='task_mandate'",
        (owner.user_id,),
    ).fetchone()[0] == 0


def test_reservation_expiring_after_final_source_assert_writes_no_operation(case, monkeypatch):
    mandate = issue(case)
    mandates = manager(case)
    principal = mandates.authenticate_token(mandate["token"])
    assert principal is not None
    deadline = float(mandate["expires_at"])
    clock = [deadline - 1]
    from universal_userio import task_mandates as mandate_module
    monkeypatch.setattr(mandate_module, "time", SimpleNamespace(time=lambda: clock[0]))
    original_assert = FakeCatalogLease.assert_current
    reservation_assertions = 0

    def expire_after_reservation_lease_check(lease, binding):
        nonlocal reservation_assertions
        original_assert(lease, binding)
        if lease.action == "operation_reservation":
            reservation_assertions += 1
            if reservation_assertions == 2:
                clock[0] = deadline + 1

    monkeypatch.setattr(FakeCatalogLease, "assert_current", expire_after_reservation_lease_check)
    with pytest.raises(TaskMandateError, match="mandate_expired"):
        mandates.reserve(principal, operation(case))

    assert case[1]._connection.execute(
        "SELECT COUNT(*) FROM task_mandate_operations WHERE user_id=? AND mandate_id=?",
        (case[2].user_id, mandate["mandate_id"]),
    ).fetchone()[0] == 0


def test_operation_binding_source_revocation_between_boolean_check_and_guard_is_denied(case):
    _, store, _owner, _account_ref, _, _provider, _qualification, _service, origin, resolver = case
    mandate = issue(case)
    principal = manager(case).authenticate_token(mandate["token"])

    class RevokeAfterBooleanCheck:
        def __getattr__(self, name):
            return getattr(origin, name)

        def revalidate_contact(self, qualification):
            result = origin.revalidate_contact(qualification)
            origin.revoke()
            return result

    guarded = TaskMandates(store, TaskContactQualification(
        store, catalog_origin=RevokeAfterBooleanCheck(),
        provider_resolvers={"test-provider": resolver},
    ))
    with pytest.raises(TaskMandateError, match="catalog_authority_guard_denied"):
        guarded.reserve(principal, operation(case))

    assert store._connection.execute(
        "SELECT COUNT(*) FROM task_mandate_operations WHERE user_id=? AND mandate_id=?",
        (case[2].user_id, mandate["mandate_id"]),
    ).fetchone()[0] == 0


def test_concurrent_distinct_reservations_cannot_exceed_limit(case):
    mandate = issue(case, max_sends=1)
    other_store = SQLiteUserIOStore(case[0])
    managers = (manager(case), manager(case, other_store))
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
    manager_a, manager_b = manager(case), manager(case, other_store)
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
    principal = manager(case).authenticate_token(mandate["token"])
    with pytest.raises(TaskMandateError):
        manager(case).reserve(principal, operation(case, attachments=["not-a-sha256"]))
    assert case[5].calls == []


def test_mandate_policy_and_operation_reservations_are_database_immutable(case):
    mandate = issue(case)
    store = case[1]
    mandates = manager(case, store)
    principal = mandates.authenticate_token(mandate["token"])
    reserved = mandates.reserve(principal, operation(case))
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
    store.register_account(account_id=account_ref, provider="test-provider", display_name="Legacy",
                           can_read=True, can_reply=True, credential_ref="test-only")
    service = UserIOService(store, object(), object())
    incoming = InboxMessage("test-provider", "legacy-message", "sender", "kept history", 2,
                            peer_id="qualified-peer-1", conversation_kind="direct")
    conversation_id, _ = service.receive(incoming, route_id="test-provider", account_ref=account_ref)
    store.close()

    # Strip only U51/U52 additions to form a disposable legacy SQLite image
    # with representative existing owner/token/account/conversation rows.
    legacy = sqlite3.connect(db)
    for trigger in (
        "task_mandates_immutable_update", "task_mandates_no_delete",
        "task_mandate_operations_no_update", "task_mandate_operations_no_delete",
    ):
        legacy.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in (
        "task_mandate_draft_claims", "task_mandate_qualifications",
        "task_contact_qualifications", "task_contact_identity_bindings",
    ):
        legacy.execute(f"DROP TABLE IF EXISTS {table}")
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
        tables = {row["name"] for row in migrated._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "task_contact_identity_bindings", "task_contact_qualifications",
            "task_mandate_qualifications", "task_mandate_draft_claims",
        } <= tables
        for table in ("task_contact_identity_bindings", "task_contact_qualifications"):
            assert "endpoint_id" in {
                row["name"] for row in migrated._connection.execute(
                    f"PRAGMA table_info({table})",
                )
            }
        assert migrated.authenticate_token(owner_token).user_id == owner.user_id
        readonly = migrated.authenticate_token(readonly_token)
        assert readonly.user_id == owner.user_id and readonly.read_only
        assert any(a["id"] == account_ref and a["enabled"] for a in migrated.accounts(user_id=owner.user_id))
        conversation = migrated._connection.execute(
            "SELECT account_ref,source,peer_id FROM conversations WHERE user_id=? AND id=?",
            (owner.user_id, conversation_id),
        ).fetchone()
        assert tuple(conversation) == (account_ref, "test-provider", "qualified-peer-1")
    finally:
        migrated.close()
