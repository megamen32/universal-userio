"""U52 joins one catalog-qualified operation to UserIO's existing draft/outbox."""

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from universal_userio.contracts import DeliveryNotSubmittedError
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore
from universal_userio.task_contact_qualification import (
    CatalogContactProof,
    ContactQualificationError,
    ProviderContactIdentity,
    TaskContactQualification,
)
from universal_userio.task_mandates import TaskMandateError, TaskMandates


ACCOUNT = "test:task-outbox"
PROVIDER = "test-provider"
TASK = "task-outbox-01"
ACTOR = "integrator-outbox-01"
PURPOSE = "Contact the qualified integrator about the authorized project"
BODY = "One bounded message covered by the original task instruction."


class FakeCatalogOrigin:
    def __init__(self):
        self.revision = "source-v1"
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
        if contact_ref != "contact-ref":
            return None
        return CatalogContactProof(
            user_id=user_id, task_id=task_id, actor_id=actor_id,
            project_id="integrator-project", catalog_namespace="airlockplan.integrators",
            catalog_record_id="integrator-100", catalog_contact_id="contact-200",
            catalog_revision=self.revision, contact_version="contact-v1",
            provider=PROVIDER, account_ref=ACCOUNT,
            provider_locator_kind="provider_native_ref",
            provider_locator="provider-ref-200", expires_at=time.time() + 7200,
        )

    def revalidate_contact(self, qualification):
        return (not self.revoked
                and qualification.get("catalog_revision") == self.revision
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
            proofs = proof if isinstance(proof, list) else [proof]
            if (self.revoked or any(
                item.get("catalog_revision") != self.revision
                or item.get("task_id") != TASK or item.get("actor_id") != ACTOR
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


def _revoke_catalog_from_process(origin, attempted, acknowledged):
    attempted.set()
    origin.revoke()
    acknowledged.set()


class FakeProviderResolver:
    def resolve_catalog_contact(self, *, account_ref, locator_kind, locator, user_id):
        assert account_ref == ACCOUNT
        assert locator_kind == "provider_native_ref"
        assert locator == "provider-ref-200"
        return ProviderContactIdentity(
            provider=PROVIDER, account_ref=ACCOUNT, peer_id="peer-200",
            peer_kind="direct", route_id=PROVIDER, display_name="Contact",
        )


class FakeOutbox:
    def __init__(self):
        self.calls = []
        self.accepted = []
        self.mode = "success"
        self.during_send = None
        self.before_send = None

    def send_reply(self, *, route_id, conversation_id, draft_id, body):
        if self.before_send is not None:
            self.before_send()
        self.calls.append((route_id, conversation_id, draft_id, body))
        if self.during_send is not None:
            self.during_send()
        if self.mode == "refused":
            self.mode = "success"
            raise DeliveryNotSubmittedError("fake refused before submission")
        if self.mode == "unknown":
            raise RuntimeError("fake outcome unknown after submission")
        self.accepted.append(draft_id)
        return "fake-receipt:" + draft_id


@pytest.fixture
def case(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "task-outbox.sqlite3")
    store.seed_owner("task-owner", "task-owner-password")
    owner = store.authenticate_credentials("task-owner", "task-owner-password")
    assert owner is not None
    store.register_account(account_id=ACCOUNT, provider=PROVIDER, display_name="Test",
                           can_read=True, can_reply=True, credential_ref="fake-only")
    store.bind_channel_route(user_id=owner.user_id, source=PROVIDER, route_id=PROVIDER)
    catalog = FakeCatalogOrigin()
    resolver = FakeProviderResolver()
    outbox = FakeOutbox()
    service = UserIOService(
        store, object(), outbox, task_catalog_origin=catalog,
        task_contact_provider_resolvers={PROVIDER: resolver},
    )
    qualification = service.qualify_task_contact(
        issuer=owner, task_id=TASK, actor_id=ACTOR,
        contact_ref="contact-ref", account_ref=ACCOUNT,
    )
    mandate = TaskMandates(store, service._task_contact_qualification).issue(
        issuer=owner, task_id=TASK, actor_id=ACTOR, account_ref=ACCOUNT,
        channel=PROVIDER, qualification_ids=[qualification["qualification_id"]],
        purpose=PURPOSE, max_sends=3, expires_at=time.time() + 3600,
    )
    authority = TaskMandates(store, service._task_contact_qualification).authenticate_token(
        mandate["token"],
    )
    assert authority is not None
    yield store, owner, service, catalog, outbox, qualification, mandate, authority
    store.close()


def make_operation(qualification, *, operation_id="operation-task-000001",
                   body=BODY, attachments=None):
    return {
        "operation_id": operation_id,
        "task_id": TASK,
        "actor_id": ACTOR,
        "account_ref": ACCOUNT,
        "channel": PROVIDER,
        "conversation_id": qualification["conversation_id"],
        "endpoint_id": qualification["endpoint_id"],
        "recipient_id": qualification["peer_id"],
        "qualification_id": qualification["qualification_id"],
        "catalog_binding_hash": qualification["binding_hash"],
        "purpose": PURPOSE,
        "body": body,
        "attachments": list(attachments or []),
    }


def reserve(case, operation=None):
    store, _owner, service, _catalog, _outbox, qualification, mandate, authority = case
    operation = operation or make_operation(qualification)
    saved = TaskMandates(store, service._task_contact_qualification).reserve(authority, operation)
    return saved, operation


def draft(case, *, draft_id="draft-task-000001", body=BODY):
    store, owner, _service, _catalog, _outbox, qualification, *_rest = case
    from universal_userio.contracts import ReplyDraft
    item = ReplyDraft(draft_id, qualification["conversation_id"], body, "proposed")
    store.add_draft(item, user_id=owner.user_id)
    return item


def send(case, *, operation_id="operation-task-000001", draft_id="draft-task-000001"):
    _store, _owner, service, _catalog, _outbox, _qualification, _mandate, authority = case
    return service._approve_task_mandated_draft(
        principal=authority, operation_id=operation_id, draft_id=draft_id,
    )


def test_task_operation_uses_existing_draft_claim_and_receipt(case):
    store, owner, _service, _catalog, outbox, qualification, _mandate, _authority = case
    reserve(case)
    draft(case)

    approved = send(case)

    assert approved.status == "approved"
    assert approved.receipt == "fake-receipt:draft-task-000001"
    assert len(outbox.calls) == 1
    assert outbox.accepted == ["draft-task-000001"]
    assert store.draft_delivery_state(approved.id, user_id=owner.user_id)["delivery_state"] == "delivered"
    assert store._connection.execute(
        "SELECT operation_id,qualification_id,binding_hash FROM task_mandate_draft_claims "
        "WHERE user_id=? AND draft_id=?",
        (owner.user_id, approved.id),
    ).fetchone()["qualification_id"] == qualification["qualification_id"]

    assert send(case).receipt == approved.receipt
    assert len(outbox.calls) == 1


def test_task_draft_claim_is_immutable_after_provider_acceptance(case):
    store, owner, _service, _catalog, _outbox, _qualification, _mandate, _authority = case
    reserve(case)
    draft(case)
    approved = send(case)

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store._connection.execute(
            "UPDATE task_mandate_draft_claims SET operation_id='changed-operation' "
            "WHERE user_id=? AND draft_id=?",
            (owner.user_id, approved.id),
        )
    with pytest.raises(sqlite3.IntegrityError, match="durable"):
        store._connection.execute(
            "DELETE FROM task_mandate_draft_claims WHERE user_id=? AND draft_id=?",
            (owner.user_id, approved.id),
        )
def test_draft_body_or_conversation_mismatch_stops_before_outbox(case):
    _store, owner, _service, _catalog, outbox, qualification, _mandate, _authority = case
    reserve(case)
    draft(case, body="changed after the authorized task operation")

    with pytest.raises(TaskMandateError, match="draft_content_hash_conflict"):
        send(case)

    assert outbox.calls == []
    assert _store.draft("draft-task-000001", user_id=owner.user_id).status == "proposed"
    assert _store._connection.execute(
        "SELECT COUNT(*) FROM task_mandate_draft_claims WHERE user_id=?",
        (owner.user_id,),
    ).fetchone()[0] == 0
    assert qualification["peer_id"] == "peer-200"


def test_revoke_between_draft_claim_and_provider_admission_prevents_effect(case):
    store, owner, service, _catalog, outbox, _qualification, mandate, _authority = case
    reserve(case)
    draft(case)
    original_prepare = service._prepare_claimed_draft

    def revoke_after_prepare(claimed, *, user_id):
        send = original_prepare(claimed, user_id=user_id)
        assert TaskMandates(store, service._task_contact_qualification).revoke(
            owner, mandate["mandate_id"],
        )
        return send

    service._prepare_claimed_draft = revoke_after_prepare
    with pytest.raises(TaskMandateError, match="mandate_revoked_expired_or_changed"):
        send(case)

    state = store.draft_delivery_state("draft-task-000001", user_id=owner.user_id)
    assert state["status"] == "proposed"
    assert state["delivery_state"] in {"", "not_submitted"}
    assert outbox.calls == []
    assert outbox.accepted == []


def test_guard_wait_crossing_mandate_expiry_denies_before_provider(case, monkeypatch):
    store, owner, service, catalog, outbox, _qualification, mandate, authority = case
    reserve(case)
    draft(case)
    deadline = float(mandate["expires_at"])
    clock = [deadline - 1]

    from universal_userio import task_mandate_outbox as outbox_module
    monkeypatch.setattr(outbox_module, "time", SimpleNamespace(time=lambda: clock[0]))

    def expire_while_waiting(action, _binding):
        if action == "provider_admission":
            clock[0] = deadline + 1

    catalog.on_guard_acquired = expire_while_waiting
    with pytest.raises(TaskMandateError, match="mandate_revoked_expired_or_changed"):
        service._approve_task_mandated_draft(
            principal=authority, operation_id="operation-task-000001",
            draft_id="draft-task-000001",
        )

    state = store.draft_delivery_state("draft-task-000001", user_id=owner.user_id)
    assert state["status"] == "proposed"
    assert state["delivery_state"] in {"", "not_submitted"}
    assert outbox.calls == []


def test_expiry_after_context_check_inside_admission_transaction_denies(case, monkeypatch):
    store, owner, service, _catalog, outbox, _qualification, mandate, authority = case
    reserve(case)
    draft(case)
    deadline = float(mandate["expires_at"])
    clock = [deadline - 1]

    from universal_userio import task_mandate_outbox as outbox_module
    monkeypatch.setattr(outbox_module, "time", SimpleNamespace(time=lambda: clock[0]))
    from universal_userio.task_mandate_outbox import TaskMandateOutbox
    original_context = TaskMandateOutbox._context
    context_calls = 0

    def advance_after_final_context_check(self, principal, operation_id):
        nonlocal context_calls
        context_calls += 1
        result = original_context(self, principal, operation_id)
        # claim uses calls 1-2; admission's transaction context is call 4.
        if context_calls == 4:
            clock[0] = deadline + 1
        return result

    monkeypatch.setattr(TaskMandateOutbox, "_context", advance_after_final_context_check)
    with pytest.raises(TaskMandateError, match="mandate_revoked_expired_or_changed"):
        service._approve_task_mandated_draft(
            principal=authority, operation_id="operation-task-000001",
            draft_id="draft-task-000001",
        )

    state = store.draft_delivery_state("draft-task-000001", user_id=owner.user_id)
    assert state["status"] == "proposed"
    assert state["delivery_state"] == "not_submitted"
    assert outbox.calls == []


def test_expiry_after_context_check_before_claim_write_does_not_bind_draft(case, monkeypatch):
    store, owner, service, _catalog, _outbox, _qualification, mandate, authority = case
    reserve(case)
    draft(case)
    deadline = float(mandate["expires_at"])
    clock = [deadline - 1]

    from universal_userio import task_mandate_outbox as outbox_module
    monkeypatch.setattr(outbox_module, "time", SimpleNamespace(time=lambda: clock[0]))
    from universal_userio.task_mandate_outbox import TaskMandateOutbox
    original_context = TaskMandateOutbox._context
    context_calls = 0

    def advance_after_claim_transaction_context(self, principal, operation_id):
        nonlocal context_calls
        context_calls += 1
        result = original_context(self, principal, operation_id)
        if context_calls == 2:
            clock[0] = deadline + 1
        return result

    monkeypatch.setattr(TaskMandateOutbox, "_context", advance_after_claim_transaction_context)
    outbox = TaskMandateOutbox(
        store, service, service._task_contact_qualification,
    )
    with pytest.raises(TaskMandateError, match="mandate_revoked_expired_or_changed"):
        outbox.claim(
            principal=authority, operation_id="operation-task-000001",
            draft_id="draft-task-000001",
        )

    assert store._connection.execute(
        "SELECT COUNT(*) FROM task_mandate_draft_claims WHERE user_id=?",
        (owner.user_id,),
    ).fetchone()[0] == 0
    state = store.draft_delivery_state("draft-task-000001", user_id=owner.user_id)
    assert state["status"] == "proposed"
    assert state["delivery_state"] in {"", "not_submitted"}


def test_source_lease_expiry_denies_while_mandate_is_still_current(case, monkeypatch):
    store, owner, service, _catalog, outbox, _qualification, mandate, authority = case
    reserve(case)
    draft(case)
    from universal_userio.task_contact_qualification import ContactQualificationError
    original_assert_current = FakeCatalogLease.assert_current

    def expire_source_lease(lease, binding):
        if lease.action == "provider_admission":
            raise ContactQualificationError("fixture_source_lease_expired")
        original_assert_current(lease, binding)

    monkeypatch.setattr(FakeCatalogLease, "assert_current", expire_source_lease)
    assert float(mandate["expires_at"]) > time.time()
    with pytest.raises(TaskMandateError, match="catalog_authority_guard_denied"):
        send(case)

    assert outbox.calls == []
    state = store.draft_delivery_state("draft-task-000001", user_id=owner.user_id)
    assert state["status"] == "proposed"
    assert state["delivery_state"] == "not_submitted"


def test_boolean_only_catalog_denies_outbox_claim(case):
    store, _owner, service, catalog, outbox, _qualification, _mandate, _authority = case
    reserve(case)
    draft(case)

    class LegacyBooleanCatalog:
        def __getattr__(self, name):
            if name == "acquire_authority_guard":
                raise AttributeError(name)
            return getattr(catalog, name)

    service._task_contact_qualification.catalog_origin = LegacyBooleanCatalog()
    with pytest.raises(TaskMandateError, match="catalog_authority_guard_denied"):
        send(case)

    assert outbox.calls == []
    assert store.draft("draft-task-000001", user_id="user_owner").status == "proposed"


def test_revoke_after_draft_claim_boolean_check_but_before_guard_denies(case):
    store, _owner, service, catalog, outbox, _qualification, _mandate, _authority = case
    reserve(case)
    draft(case)

    class RevokeAfterBooleanCheck:
        def __getattr__(self, name):
            return getattr(catalog, name)

        def revalidate_contact(self, qualification):
            result = catalog.revalidate_contact(qualification)
            catalog.revoke()
            return result

    service._task_contact_qualification.catalog_origin = RevokeAfterBooleanCheck()
    with pytest.raises(TaskMandateError, match="catalog_authority_guard_denied"):
        send(case)

    assert outbox.calls == []
    assert store.draft("draft-task-000001", user_id="user_owner").status == "proposed"


def test_revoke_after_final_admission_boolean_check_before_sqlite_commit_denies(case):
    store, _owner, service, catalog, outbox, _qualification, _mandate, _authority = case
    reserve(case)
    draft(case)
    original_prepare = service._prepare_claimed_draft

    class RevokeAfterFinalBooleanCheck:
        def __getattr__(self, name):
            return getattr(catalog, name)

        def revalidate_contact(self, qualification):
            result = catalog.revalidate_contact(qualification)
            catalog.revoke()
            return result

    def prepare_then_revoke(claimed, *, user_id):
        send_closure = original_prepare(claimed, user_id=user_id)
        service._task_contact_qualification.catalog_origin = RevokeAfterFinalBooleanCheck()
        return send_closure

    service._prepare_claimed_draft = prepare_then_revoke
    with pytest.raises(TaskMandateError, match="catalog_authority_guard_denied"):
        send(case)

    state = store.draft_delivery_state("draft-task-000001", user_id="user_owner")
    assert state["status"] == "proposed"
    assert state["delivery_state"] == "not_submitted"
    assert outbox.calls == []


def test_process_revoke_after_final_boolean_check_before_admission_guard_denies(case):
    store, _owner, service, catalog, outbox, _qualification, _mandate, _authority = case
    reserve(case)
    draft(case)
    ctx = multiprocessing.get_context("fork")
    manager = ctx.Manager()
    try:
        catalog.enable_process_sharing(manager)
        original_prepare = service._prepare_claimed_draft

        class RevokeInSeparateProcessAfterBooleanCheck:
            def __getattr__(self, name):
                return getattr(catalog, name)

            def revalidate_contact(self, qualification):
                result = catalog.revalidate_contact(qualification)
                attempted, acknowledged = ctx.Event(), ctx.Event()
                process = ctx.Process(
                    target=_revoke_catalog_from_process,
                    args=(catalog, attempted, acknowledged),
                )
                process.start()
                assert attempted.wait(5)
                assert acknowledged.wait(5)
                process.join(5)
                assert process.exitcode == 0
                return result

        def prepare_then_swap_origin(claimed, *, user_id):
            send_closure = original_prepare(claimed, user_id=user_id)
            service._task_contact_qualification.catalog_origin = \
                RevokeInSeparateProcessAfterBooleanCheck()
            return send_closure

        service._prepare_claimed_draft = prepare_then_swap_origin
        with pytest.raises(TaskMandateError, match="catalog_authority_guard_denied"):
            send(case)

        state = store.draft_delivery_state("draft-task-000001", user_id="user_owner")
        assert state["status"] == "proposed"
        assert state["delivery_state"] == "not_submitted"
        assert outbox.calls == []
    finally:
        manager.shutdown()


def test_revoke_during_admission_waits_for_commit_then_acks_without_canceling_inflight(case):
    store, _owner, service, catalog, outbox, qualification, _mandate, _authority = case
    reserve(case)
    draft(case)
    guard_entered, continue_admission = threading.Event(), threading.Event()
    revoke_requested, revoke_ack = threading.Event(), threading.Event()

    def hold_provider_admission(action, _binding):
        if action == "provider_admission":
            guard_entered.set()
            assert continue_admission.wait(5)

    catalog.on_guard_acquired = hold_provider_admission

    def wait_for_revoke_ack():
        assert revoke_ack.wait(5)

    outbox.before_send = wait_for_revoke_ack

    with ThreadPoolExecutor(max_workers=2) as pool:
        send_future = pool.submit(send, case)
        assert guard_entered.wait(5)

        def revoke():
            revoke_requested.set()
            catalog.revoke()
            revoke_ack.set()

        revoke_future = pool.submit(revoke)
        assert revoke_requested.wait(5)
        assert not revoke_ack.wait(0.05)
        continue_admission.set()
        approved = send_future.result(timeout=5)
        revoke_future.result(timeout=5)

    assert approved.status == "approved"
    assert revoke_ack.is_set()
    assert len(outbox.calls) == 1
    assert outbox.accepted == ["draft-task-000001"]
    assert outbox.calls[0][1] == qualification["conversation_id"]
    with pytest.raises(TaskMandateError, match="catalog_qualification_not_current"):
        reserve(case, make_operation(qualification, operation_id="operation-after-revoke-0001"))
    assert len(outbox.calls) == 1


def test_process_revoke_waits_for_admission_commit_and_ack_keeps_inflight_attempt(case):
    store, _owner, service, catalog, outbox, qualification, _mandate, _authority = case
    reserve(case)
    draft(case)
    ctx = multiprocessing.get_context("fork")
    manager = ctx.Manager()
    try:
        catalog.enable_process_sharing(manager)
        guard_entered, continue_admission = threading.Event(), threading.Event()
        attempted, acknowledged = ctx.Event(), ctx.Event()

        def hold_provider_admission(action, _binding):
            if action == "provider_admission":
                guard_entered.set()
                assert continue_admission.wait(5)

        catalog.on_guard_acquired = hold_provider_admission

        def wait_for_ack():
            assert acknowledged.wait(5)

        outbox.before_send = wait_for_ack
        with ThreadPoolExecutor(max_workers=1) as pool:
            send_future = pool.submit(send, case)
            assert guard_entered.wait(5)
            process = ctx.Process(
                target=_revoke_catalog_from_process,
                args=(catalog, attempted, acknowledged),
            )
            process.start()
            assert attempted.wait(5)
            assert not acknowledged.wait(0.05)
            continue_admission.set()
            approved = send_future.result(timeout=5)
            process.join(5)
            assert process.exitcode == 0
        assert acknowledged.is_set()
        assert approved.status == "approved"
        assert len(outbox.calls) == 1
        assert outbox.accepted == ["draft-task-000001"]
        assert outbox.calls[0][1] == qualification["conversation_id"]
        with pytest.raises(TaskMandateError, match="catalog_qualification_not_current"):
            reserve(case, make_operation(qualification,
                                         operation_id="operation-after-process-revoke-0001"))
        assert len(outbox.calls) == 1
    finally:
        manager.shutdown()


def test_account_recheck_after_claim_prevents_provider_admission(case):
    store, owner, service, _catalog, outbox, _qualification, _mandate, _authority = case
    reserve(case)
    draft(case)
    original_prepare = service._prepare_claimed_draft

    def disable_after_prepare(claimed, *, user_id):
        send = original_prepare(claimed, user_id=user_id)
        with store._connection:
            store._connection.execute(
                "UPDATE provider_accounts SET enabled=0 WHERE user_id=? AND id=?",
                (user_id, ACCOUNT),
            )
        return send

    service._prepare_claimed_draft = disable_after_prepare
    with pytest.raises(TaskMandateError, match="account_not_currently_send_capable"):
        send(case)
    assert outbox.calls == []
    assert store.draft("draft-task-000001", user_id=owner.user_id).status == "proposed"


def test_unknown_after_admission_is_not_replayed(case):
    store, owner, _service, _catalog, outbox, _qualification, _mandate, _authority = case
    reserve(case)
    draft(case)
    outbox.mode = "unknown"

    with pytest.raises(RuntimeError, match="outcome unknown"):
        send(case)
    state = store.draft_delivery_state("draft-task-000001", user_id=owner.user_id)
    assert state["status"] == "sending"
    assert state["delivery_state"] == "task_mandate_admitted"
    assert len(outbox.calls) == 1
    with pytest.raises(TaskMandateError, match="delivery_outcome_unknown"):
        send(case)
    assert len(outbox.calls) == 1


def test_proven_pre_submit_refusal_allows_only_same_draft_operation_retry(case):
    _store, _owner, _service, _catalog, outbox, _qualification, _mandate, _authority = case
    reserve(case)
    draft(case)
    outbox.mode = "refused"

    with pytest.raises(DeliveryNotSubmittedError):
        send(case)
    assert send(case).receipt == "fake-receipt:draft-task-000001"
    assert len(outbox.calls) == 2
    assert outbox.accepted == ["draft-task-000001"]


def test_concurrent_claims_admit_the_same_draft_only_once(case):
    _store, _owner, _service, _catalog, outbox, _qualification, _mandate, _authority = case
    reserve(case)
    draft(case)

    def attempt(_index):
        try:
            return send(case).status
        except TaskMandateError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (1, 2)))

    assert results.count("approved") == 1
    assert len(outbox.calls) == 1
    assert outbox.accepted == ["draft-task-000001"]


def test_revocation_after_admission_preserves_the_existing_inflight_attempt(case):
    store, owner, service, _catalog, outbox, _qualification, mandate, _authority = case
    reserve(case)
    draft(case)
    outbox.during_send = lambda: TaskMandates(
        store, service._task_contact_qualification,
    ).revoke(owner, mandate["mandate_id"])

    approved = send(case)

    assert approved.status == "approved"
    assert len(outbox.calls) == 1
    with pytest.raises(TaskMandateError, match="approved_draft_mandate_conflict"):
        send(case, operation_id="operation-after-revoke")
    assert len(outbox.calls) == 1


@pytest.mark.parametrize("state", ["task_mandate_claimed", "task_mandate_admitted"])
def test_restart_never_replays_a_draft_claim(case, state, tmp_path):
    store, owner, service, catalog, outbox, qualification, mandate, authority = case
    _saved, operation = reserve(case)
    draft(case)
    from universal_userio.task_mandate_outbox import TaskMandateOutbox
    lifecycle = TaskMandateOutbox(store, service, service._task_contact_qualification)
    lifecycle.claim(principal=authority, operation_id=operation["operation_id"],
                    draft_id="draft-task-000001")
    if state == "task_mandate_admitted":
        lifecycle.admit_provider(principal=authority, operation_id=operation["operation_id"],
                                 draft_id="draft-task-000001")

    store.close()
    reopened = SQLiteUserIOStore(tmp_path / "task-outbox.sqlite3")
    try:
        new_service = UserIOService(
            reopened, object(), outbox, task_catalog_origin=catalog,
            task_contact_provider_resolvers={PROVIDER: FakeProviderResolver()},
        )
        new_qualification = TaskContactQualification(
            reopened, catalog_origin=catalog,
            provider_resolvers={PROVIDER: FakeProviderResolver()},
        )
        new_service._task_contact_qualification = new_qualification
        new_authority = TaskMandates(reopened, new_qualification).authenticate_token(
            mandate["token"],
        )
        assert new_authority is not None
        with pytest.raises(TaskMandateError, match="delivery_outcome_unknown"):
            new_service._approve_task_mandated_draft(
                principal=new_authority, operation_id=operation["operation_id"],
                draft_id="draft-task-000001",
            )
        assert len(outbox.calls) == 0
        assert reopened.draft_delivery_state(
            "draft-task-000001", user_id=owner.user_id,
        )["delivery_state"] == state
    finally:
        reopened.close()


def test_attachment_bytes_are_bound_to_the_exact_operation(case):
    store, owner, service, _catalog, outbox, qualification, _mandate, _authority = case
    from universal_userio.messenger_uploads import MessengerUploads
    import base64
    import hashlib
    from universal_userio.task_mandate_outbox import TaskMandateOutbox

    draft_id = "draft-task-attachment-01"
    conversation = {
        "id": qualification["conversation_id"],
        "account_ref": qualification["account_ref"],
        "peer_id": qualification["peer_id"],
        "source": "vk",
    }
    binding = {key: conversation[key] for key in ("id", "account_ref", "peer_id")}
    data = b"exact staged bytes"
    digest = hashlib.sha256(data).hexdigest()
    uploaded = MessengerUploads(store, owner.user_id).stage(
        binding, filename="brief.txt", mime_type="text/plain",
        data_base64=base64.b64encode(data).decode(), sha256=digest,
        request_id="task-outbox-attachment-0001",
    )
    store._connection.execute(
        "CREATE TABLE vk_web_files (user_id TEXT NOT NULL,draft_id TEXT NOT NULL,"
        "upload_id TEXT NOT NULL,native_ref TEXT NOT NULL DEFAULT '',"
        "PRIMARY KEY(user_id,draft_id))",
    )
    uploads = MessengerUploads(store, owner.user_id)
    uploads.bind(uploaded["upload_id"], draft_id)
    store._connection.execute(
        "INSERT INTO vk_web_files(user_id,draft_id,upload_id) VALUES(?,?,?)",
        (owner.user_id, draft_id, uploaded["upload_id"]),
    )
    lifecycle = TaskMandateOutbox(store, service, service._task_contact_qualification)
    hashes = lifecycle._attachment_hashes(
        user_id=owner.user_id, draft_id=draft_id, conversation=conversation,
    )
    assert hashes == [digest]
    operation = make_operation(qualification, body=BODY, attachments=hashes)
    operation["attachment_sha256"] = hashes
    operation["content_sha256"] = hashlib.sha256(BODY.encode()).hexdigest()
    context = {
        "operation": operation, "task_id": TASK, "actor_id": ACTOR,
        "account_ref": ACCOUNT, "channel": PROVIDER, "peer_id": qualification["peer_id"],
        "id": qualification["qualification_id"],
        "binding_hash": qualification["binding_hash"], "purpose": PURPOSE,
        "endpoint_id": qualification["endpoint_id"],
    }
    from universal_userio.contracts import ReplyDraft
    draft_item = ReplyDraft(draft_id, qualification["conversation_id"], BODY, "proposed")
    lifecycle._verify_snapshot(
        context, draft_id=draft_id, draft=draft_item,
        conversation={**conversation, "source": PROVIDER,
                      "route_id": qualification["endpoint_id"]}, attachments=hashes,
    )
    with pytest.raises(TaskMandateError, match="draft_attachment_snapshot_conflict"):
        lifecycle._verify_snapshot(
            context, draft_id=draft_id, draft=draft_item,
            conversation={**conversation, "source": PROVIDER,
                          "route_id": qualification["endpoint_id"]}, attachments=["f" * 64],
        )
    assert outbox.calls == []
