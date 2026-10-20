"""First-contact qualification uses fake catalog/provider seams and never sends."""

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import multiprocessing
import threading
import time
from types import SimpleNamespace

import pytest

from universal_userio.contracts import UserPrincipal
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore
from universal_userio.task_contact_qualification import (
    CatalogContactProof,
    ContactQualificationError,
    ProviderContactIdentity,
)


ACCOUNT = "test:catalog-account"
PROVIDER = "test-provider"
TASK = "task-catalog-01"
ACTOR = "integrator-actor-01"


class FixtureCatalog:
    def __init__(self):
        self.revision = "catalog-rev-1"
        self.record_revision = self.revision
        self.resolve_calls = []
        self.revalidate_calls = []
        self._revoked = False
        self._shared_state = None
        self._authority_lock = threading.RLock()
        self.on_guard_acquired = None

    @property
    def revoked(self):
        return bool(self._shared_state["revoked"]) if self._shared_state is not None else self._revoked

    def enable_process_sharing(self, manager):
        self._authority_lock = manager.RLock()
        self._shared_state = manager.dict(revoked=False)

    def resolve_contact(self, *, user_id, task_id, actor_id, contact_ref):
        self.resolve_calls.append((user_id, task_id, actor_id, contact_ref))
        if contact_ref != "opaque-contact-ref":
            return None
        return CatalogContactProof(
            user_id=user_id,
            task_id=task_id,
            actor_id=actor_id,
            project_id="project-integrators",
            catalog_namespace="airlockplan.integrators",
            catalog_record_id="integrator-record-01",
            catalog_contact_id="contact-record-02",
            catalog_revision=self.record_revision,
            contact_version="contact-v3",
            provider=PROVIDER,
            account_ref=ACCOUNT,
            provider_locator_kind="provider_native_ref",
            provider_locator="opaque-provider-locator",
            expires_at=time.time() + 3600,
        )

    def revalidate_contact(self, qualification):
        self.revalidate_calls.append(dict(qualification))
        return (not self.revoked
                and qualification.get("catalog_revision") == self.revision
                and qualification.get("catalog_namespace") == "airlockplan.integrators"
                and qualification.get("task_id") == TASK
                and qualification.get("actor_id") == ACTOR)

    def revoke(self):
        with self._authority_lock:
            if self._shared_state is None:
                self._revoked = True
            else:
                self._shared_state["revoked"] = True

    @contextmanager
    def acquire_authority_guard(self, *, proof, action, binding):
        with self._authority_lock:
            if (self.revoked or proof.get("catalog_revision") != self.revision
                    or proof.get("catalog_namespace") != "airlockplan.integrators"
                    or proof.get("task_id") != TASK or proof.get("actor_id") != ACTOR):
                raise ContactQualificationError("catalog_authority_guard_denied")
            lease = FakeCatalogLease(self, proof, action, binding)
            if self.on_guard_acquired is not None:
                self.on_guard_acquired(action, dict(binding))
            yield lease


class FakeCatalogLease:
    def __init__(self, owner, proof, action, initial_binding):
        self.owner = owner
        self.proofs = [dict(item) for item in proof] if isinstance(proof, list) else [dict(proof)]
        self.action = action
        self.binding = None
        self.bind(initial_binding)

    def bind(self, binding):
        if binding.get("action") != self.action:
            raise ContactQualificationError("catalog_authority_action_conflict")
        if ("endpoint_id" in binding
                and binding["endpoint_id"] != binding.get("provider")):
            raise ContactQualificationError("catalog_authority_endpoint_conflict")
        if self.binding is not None and any(
            key not in binding or binding[key] != value
            for key, value in self.binding.items()
        ):
            raise ContactQualificationError("catalog_authority_binding_changed")
        if any(binding.get(key) != value for proof in self.proofs
               for key, value in proof.items() if key in binding and key != "expires_at"):
            raise ContactQualificationError("catalog_authority_scope_conflict")
        if "qualified_contacts" in binding and any(
            not any(all(contact.get(key) == value for key, value in proof.items()
                        if key != "expires_at")
                    for contact in binding["qualified_contacts"])
            for proof in self.proofs
        ):
            raise ContactQualificationError("catalog_authority_batch_scope_conflict")
        self.binding = dict(binding)

    def assert_current(self, binding):
        if (self.owner.revoked
                or any(proof.get("catalog_revision") != self.owner.revision
                       for proof in self.proofs)
                or dict(binding) != self.binding):
            raise ContactQualificationError("catalog_authority_guard_denied")


class LegacyBooleanCatalog:
    """Pre-lease adapter shape: a bool check cannot fence a later commit."""

    def __init__(self, source):
        self.resolve_contact = source.resolve_contact
        self.revalidate_contact = source.revalidate_contact


class FakeProvider:
    def __init__(self):
        self.lookup_calls = []
        self.peer_id = "provider-peer-9001"
        self.send_calls = []

    def resolve_catalog_contact(self, *, account_ref, locator_kind, locator, user_id):
        self.lookup_calls.append((account_ref, locator_kind, locator, user_id))
        if locator != "opaque-provider-locator" or locator_kind != "provider_native_ref":
            raise ValueError("unexpected or unqualified provider locator")
        return ProviderContactIdentity(
            provider=PROVIDER,
            account_ref=account_ref,
            peer_id=self.peer_id,
            peer_kind="direct",
            route_id=PROVIDER,
            display_name="Catalog-qualified person",
        )

    def execute(self, *_args, **_kwargs):
        self.send_calls.append(True)
        raise AssertionError("contact qualification must not send")


def _revoke_catalog_from_process(origin, attempted, acknowledged):
    attempted.set()
    origin.revoke()
    acknowledged.set()


@pytest.fixture
def qualification_case(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "qualification.sqlite3")
    store.seed_owner("qualifier", "qualifier-password")
    owner = store.authenticate_credentials("qualifier", "qualifier-password")
    assert owner is not None
    store.register_account(
        account_id=ACCOUNT, provider=PROVIDER, display_name="Fixture account",
        can_read=True, can_reply=True, credential_ref="fake-only",
    )
    store.bind_channel_route(user_id=owner.user_id, source=PROVIDER, route_id=PROVIDER)
    origin, provider = FixtureCatalog(), FakeProvider()
    service = UserIOService(
        store, object(), provider, task_catalog_origin=origin,
        task_contact_provider_resolvers={PROVIDER: provider},
    )
    yield store, owner, origin, provider, service
    store.close()


def test_verified_catalog_contact_creates_empty_account_bound_conversation(qualification_case):
    store, owner, origin, provider, service = qualification_case

    result = service.qualify_task_contact(
        issuer=owner, task_id=TASK, actor_id=ACTOR,
        contact_ref="opaque-contact-ref", account_ref=ACCOUNT,
    )

    conversation = store.conversation(result["conversation_id"], user_id=owner.user_id)
    assert result["provider"] == PROVIDER
    assert result["account_ref"] == ACCOUNT
    assert result["peer_id"] == provider.peer_id
    assert result["endpoint_id"] == PROVIDER
    assert conversation["source"] == PROVIDER
    assert conversation["account_ref"] == ACCOUNT
    assert conversation["route_id"] == result["endpoint_id"]
    assert conversation["peer_id"] == provider.peer_id
    assert conversation["conversation_kind"] == "direct"
    assert conversation["messages"] == []
    assert store._connection.execute(
        "SELECT COUNT(*) FROM messages WHERE user_id=?", (owner.user_id,)
    ).fetchone()[0] == 0
    assert store._connection.execute(
        "SELECT COUNT(*) FROM task_contact_qualifications WHERE user_id=?",
        (owner.user_id,),
    ).fetchone()[0] == 1
    assert len(origin.revalidate_calls) == 1
    assert len(provider.lookup_calls) == 1
    assert provider.send_calls == []


def test_duplicate_qualification_reuses_the_same_immutable_identity(qualification_case):
    store, owner, _origin, provider, service = qualification_case
    kwargs = dict(
        issuer=owner, task_id=TASK, actor_id=ACTOR,
        contact_ref="opaque-contact-ref", account_ref=ACCOUNT,
    )

    first = service.qualify_task_contact(**kwargs)
    second = service.qualify_task_contact(**kwargs)

    assert second["conversation_id"] == first["conversation_id"]
    assert second["qualification_id"] == first["qualification_id"]
    assert store._connection.execute(
        "SELECT COUNT(*) FROM conversations WHERE user_id=? AND source=? "
        "AND account_ref=? AND peer_id=?",
        (owner.user_id, PROVIDER, ACCOUNT, provider.peer_id),
    ).fetchone()[0] == 1
    assert provider.send_calls == []


def test_proof_expiry_inside_store_transaction_persists_no_identity(qualification_case, monkeypatch):
    store, owner, origin, provider, service = qualification_case
    original_resolve = origin.resolve_contact
    deadline = time.time() + 5
    clock = [deadline - 1]

    def short_lived_proof(**kwargs):
        return replace(original_resolve(**kwargs), expires_at=deadline)

    origin.resolve_contact = short_lived_proof
    from universal_userio import store as store_module
    from universal_userio import task_contact_qualification as qualification_module
    fake_time = SimpleNamespace(time=lambda: clock[0])
    monkeypatch.setattr(store_module, "time", fake_time)
    monkeypatch.setattr(qualification_module, "time", fake_time)
    original_assert_current = FakeCatalogLease.assert_current
    assertions = 0

    def expire_after_transaction_authority_check(lease, binding):
        nonlocal assertions
        assertions += 1
        original_assert_current(lease, binding)
        # Third assertion is inside create_task_contact_conversation after
        # BEGIN IMMEDIATE and account recheck, before any persisted identity.
        if assertions == 3:
            clock[0] = deadline + 1

    monkeypatch.setattr(FakeCatalogLease, "assert_current", expire_after_transaction_authority_check)
    with pytest.raises(ValueError, match="task_contact_qualification_expired"):
        service.qualify_task_contact(
            issuer=owner, task_id=TASK, actor_id=ACTOR,
            contact_ref="opaque-contact-ref", account_ref=ACCOUNT,
        )

    for table in ("task_contact_qualifications", "task_contact_identity_bindings", "conversations"):
        assert store._connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (owner.user_id,),
        ).fetchone()[0] == 0
    assert len(provider.lookup_calls) == 1
    assert provider.send_calls == []


def test_source_revoked_after_boolean_check_but_before_guard_denies_lookup(qualification_case):
    store, owner, origin, provider, service = qualification_case

    class RevokeAfterBooleanCheck:
        def __getattr__(self, name):
            return getattr(origin, name)

        def revalidate_contact(self, qualification):
            result = origin.revalidate_contact(qualification)
            origin.revoke()
            return result

    service._task_contact_qualification.catalog_origin = RevokeAfterBooleanCheck()
    with pytest.raises(ContactQualificationError, match="catalog_authority_guard_denied"):
        service.qualify_task_contact(
            issuer=owner, task_id=TASK, actor_id=ACTOR,
            contact_ref="opaque-contact-ref", account_ref=ACCOUNT,
        )

    assert provider.lookup_calls == []
    assert store._connection.execute(
        "SELECT COUNT(*) FROM task_contact_qualifications WHERE user_id=?",
        (owner.user_id,),
    ).fetchone()[0] == 0
    assert store._connection.execute(
        "SELECT COUNT(*) FROM conversations WHERE user_id=?", (owner.user_id,),
    ).fetchone()[0] == 0


def test_process_source_revoke_after_boolean_check_before_qualification_guard_denies(qualification_case):
    store, owner, origin, provider, service = qualification_case
    ctx = multiprocessing.get_context("fork")
    manager = ctx.Manager()
    try:
        origin.enable_process_sharing(manager)

        class RevokeInSeparateProcessAfterBooleanCheck:
            def __getattr__(self, name):
                return getattr(origin, name)

            def revalidate_contact(self, qualification):
                result = origin.revalidate_contact(qualification)
                attempted, acknowledged = ctx.Event(), ctx.Event()
                process = ctx.Process(
                    target=_revoke_catalog_from_process,
                    args=(origin, attempted, acknowledged),
                )
                process.start()
                assert attempted.wait(5)
                assert acknowledged.wait(5)
                process.join(5)
                assert process.exitcode == 0
                return result

        service._task_contact_qualification.catalog_origin = RevokeInSeparateProcessAfterBooleanCheck()
        with pytest.raises(ContactQualificationError, match="catalog_authority_guard_denied"):
            service.qualify_task_contact(
                issuer=owner, task_id=TASK, actor_id=ACTOR,
                contact_ref="opaque-contact-ref", account_ref=ACCOUNT,
            )
        assert provider.lookup_calls == []
        assert store._connection.execute(
            "SELECT COUNT(*) FROM task_contact_qualifications WHERE user_id=?",
            (owner.user_id,),
        ).fetchone()[0] == 0
        assert store._connection.execute(
            "SELECT COUNT(*) FROM conversations WHERE user_id=?", (owner.user_id,),
        ).fetchone()[0] == 0
    finally:
        manager.shutdown()


def test_qualification_guard_serializes_threaded_revoke_until_commit(qualification_case):
    store, owner, origin, provider, service = qualification_case
    guard_entered, continue_commit = threading.Event(), threading.Event()
    revoke_requested, revoke_ack = threading.Event(), threading.Event()

    def hold_guard(action, _binding):
        if action == "qualification_persist":
            guard_entered.set()
            assert continue_commit.wait(5)

    origin.on_guard_acquired = hold_guard
    with ThreadPoolExecutor(max_workers=2) as pool:
        qualification_future = pool.submit(
            service.qualify_task_contact,
            issuer=owner, task_id=TASK, actor_id=ACTOR,
            contact_ref="opaque-contact-ref", account_ref=ACCOUNT,
        )
        assert guard_entered.wait(5)

        def revoke():
            revoke_requested.set()
            origin.revoke()
            revoke_ack.set()

        revoke_future = pool.submit(revoke)
        assert revoke_requested.wait(5)
        assert not revoke_ack.wait(0.05)
        continue_commit.set()
        qualified = qualification_future.result(timeout=5)
        revoke_future.result(timeout=5)

    assert revoke_ack.is_set()
    assert store.task_contact_qualification(
        qualified["qualification_id"], user_id=owner.user_id,
    ) is not None
    assert origin.revalidate_contact(store.task_contact_qualification(
        qualified["qualification_id"], user_id=owner.user_id,
    )) is False
    assert provider.send_calls == []


def test_qualification_source_lease_serializes_process_revoke_until_commit(qualification_case):
    store, owner, origin, provider, service = qualification_case
    ctx = multiprocessing.get_context("fork")
    manager = ctx.Manager()
    try:
        origin.enable_process_sharing(manager)
        guard_entered, continue_commit = threading.Event(), threading.Event()
        attempted, acknowledged = ctx.Event(), ctx.Event()

        def hold_guard(action, _binding):
            if action == "qualification_persist":
                guard_entered.set()
                assert continue_commit.wait(5)

        origin.on_guard_acquired = hold_guard
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                service.qualify_task_contact,
                issuer=owner, task_id=TASK, actor_id=ACTOR,
                contact_ref="opaque-contact-ref", account_ref=ACCOUNT,
            )
            assert guard_entered.wait(5)
            process = ctx.Process(
                target=_revoke_catalog_from_process,
                args=(origin, attempted, acknowledged),
            )
            process.start()
            assert attempted.wait(5)
            assert not acknowledged.wait(0.05)
            continue_commit.set()
            qualified = future.result(timeout=5)
            process.join(5)
            assert process.exitcode == 0
        assert acknowledged.is_set()
        row = store.task_contact_qualification(
            qualified["qualification_id"], user_id=owner.user_id,
        )
        assert row is not None
        assert origin.revalidate_contact(row) is False
        assert provider.send_calls == []
    finally:
        manager.shutdown()


def test_missing_catalog_origin_fails_before_provider_lookup(qualification_case):
    store, owner, _origin, provider, _service = qualification_case
    service = UserIOService(store, object(), provider)

    with pytest.raises(ContactQualificationError, match="catalog_origin_unavailable"):
        service.qualify_task_contact(
            issuer=owner, task_id=TASK, actor_id=ACTOR,
            contact_ref="opaque-contact-ref", account_ref=ACCOUNT,
        )

    assert provider.lookup_calls == []
    assert store._connection.execute(
        "SELECT COUNT(*) FROM conversations WHERE user_id=?", (owner.user_id,)
    ).fetchone()[0] == 0
    assert provider.send_calls == []


def test_boolean_only_catalog_adapter_fails_before_provider_lookup(qualification_case):
    store, owner, origin, provider, service = qualification_case
    service._task_contact_qualification.catalog_origin = LegacyBooleanCatalog(origin)

    with pytest.raises(ContactQualificationError, match="catalog_authority_guard_unavailable"):
        service.qualify_task_contact(
            issuer=owner, task_id=TASK, actor_id=ACTOR,
            contact_ref="opaque-contact-ref", account_ref=ACCOUNT,
        )

    assert provider.lookup_calls == []
    assert store._connection.execute(
        "SELECT COUNT(*) FROM task_contact_qualifications WHERE user_id=?",
        (owner.user_id,),
    ).fetchone()[0] == 0


@pytest.mark.parametrize("change", ["owner", "task", "actor", "account", "provider"])
def test_conflicting_catalog_proof_fails_before_provider_lookup(qualification_case, change):
    store, owner, origin, provider, service = qualification_case

    class ConflictingCatalog(FixtureCatalog):
        def resolve_contact(self, **kwargs):
            proof = super().resolve_contact(**kwargs)
            fields = dict(proof.__dict__) if hasattr(proof, "__dict__") else {
                name: getattr(proof, name) for name in proof.__dataclass_fields__
            }
            if change == "owner":
                fields["user_id"] = "another-user"
            elif change == "task":
                fields["task_id"] = "other-task"
            elif change == "actor":
                fields["actor_id"] = "other-actor"
            elif change == "account":
                fields["account_ref"] = "test:other-account"
            else:
                fields["provider"] = "other-provider"
            return CatalogContactProof(**fields)

    service._task_contact_qualification.catalog_origin = ConflictingCatalog()
    with pytest.raises(ContactQualificationError, match="catalog_contact_scope_conflict"):
        service.qualify_task_contact(
            issuer=owner, task_id=TASK, actor_id=ACTOR,
            contact_ref="opaque-contact-ref", account_ref=ACCOUNT,
        )
    assert provider.lookup_calls == []
    assert store._connection.execute(
        "SELECT COUNT(*) FROM conversations WHERE user_id=?", (owner.user_id,)
    ).fetchone()[0] == 0


def test_free_text_name_locator_is_rejected_before_provider_lookup(qualification_case):
    store, owner, _origin, provider, service = qualification_case
    origin = service._task_contact_qualification.catalog_origin
    original = origin.resolve_contact

    def name_locator(**kwargs):
        proof = original(**kwargs)
        values = {name: getattr(proof, name) for name in proof.__dataclass_fields__}
        values["provider_locator_kind"] = "display_name"
        values["provider_locator"] = "someone's display name"
        return CatalogContactProof(**values)

    origin.resolve_contact = name_locator
    with pytest.raises(ContactQualificationError, match="provider_native_reference"):
        service.qualify_task_contact(
            issuer=owner, task_id=TASK, actor_id=ACTOR,
            contact_ref="opaque-contact-ref", account_ref=ACCOUNT,
        )
    assert provider.lookup_calls == []
    assert store._connection.execute(
        "SELECT COUNT(*) FROM conversations WHERE user_id=?", (owner.user_id,)
    ).fetchone()[0] == 0


def test_stale_catalog_version_denies_before_provider_lookup(qualification_case):
    store, owner, origin, provider, service = qualification_case
    origin.record_revision = "catalog-rev-stale"

    with pytest.raises(ContactQualificationError, match="catalog_contact_source_stale"):
        service.qualify_task_contact(
            issuer=owner, task_id=TASK, actor_id=ACTOR,
            contact_ref="opaque-contact-ref", account_ref=ACCOUNT,
        )

    assert provider.lookup_calls == []
    assert store._connection.execute(
        "SELECT COUNT(*) FROM conversations WHERE user_id=?", (owner.user_id,)
    ).fetchone()[0] == 0


def test_changed_provider_peer_cannot_rebind_the_catalog_contact(qualification_case):
    store, owner, _origin, provider, service = qualification_case
    first = service.qualify_task_contact(
        issuer=owner, task_id=TASK, actor_id=ACTOR,
        contact_ref="opaque-contact-ref", account_ref=ACCOUNT,
    )
    provider.peer_id = "different-provider-peer"

    with pytest.raises(ValueError, match="catalog_contact_provider_identity_changed"):
        service.qualify_task_contact(
            issuer=owner, task_id=TASK, actor_id=ACTOR,
            contact_ref="opaque-contact-ref", account_ref=ACCOUNT,
        )
    assert store.conversation(first["conversation_id"], user_id=owner.user_id)["peer_id"] == \
        "provider-peer-9001"
    assert store._connection.execute(
        "SELECT COUNT(*) FROM conversations WHERE user_id=?", (owner.user_id,)
    ).fetchone()[0] == 1
    assert provider.send_calls == []


def test_read_only_principal_cannot_qualify_or_create_a_conversation(qualification_case):
    store, owner, _origin, provider, service = qualification_case
    from universal_userio.personal_tokens import PersonalTokens
    token = PersonalTokens(store).create(owner.user_id, "readonly", scope="read_only")
    readonly = store.authenticate_token(token["token"])

    with pytest.raises(PermissionError, match="full_owner_authentication_required"):
        service.qualify_task_contact(
            issuer=readonly, task_id=TASK, actor_id=ACTOR,
            contact_ref="opaque-contact-ref", account_ref=ACCOUNT,
        )
    assert provider.lookup_calls == []
    assert provider.send_calls == []
