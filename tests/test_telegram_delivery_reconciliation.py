"""Telegram delivery: a proven non-submission is recoverable, an uncertain one is not.

The 20.10.2026 incident: the telegram-qr connector never started, so an
approved send answered "connector unreachable". The outbox reported that as an
uncertain error, UserIO kept its durable claim, and the draft stayed in
`sending` forever with no way out.

These cases pin the repaired contract:

- a connector that is provably not accepting connections is a proven
  non-submission, so the claim is released and the exact retry is possible;
- every other transport failure stays uncertain and is never replayed;
- an uncertain attempt uses the journal first, then exact fresh account/peer
  scoped provider history. A cached body alone is never delivery evidence.
"""

from __future__ import annotations

import io
import json
import threading
import time
import urllib.error

from connector_probe import reachable_probe, refused_probe
from universal_userio.adapters import TelegramQrHttpOutbox
from universal_userio.contracts import DeliveryNotSubmittedError, InboxMessage
from universal_userio.delivery_reconciliation import TelegramDeliveryReconciliationWorker
from universal_userio.mcp_surface import UserIOMcpSurface
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def suggest(self, *, conversation_id: str, latest_message: InboxMessage) -> str:
        return "ответ"


class RecordingOutbox:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def send_reply(self, *, route_id, conversation_id, draft_id, body) -> str:
        self.calls.append({"draft_id": draft_id, "body": body})
        return "notice-receipt"


def _telegram_service(tmp_path, outbox) -> tuple[UserIOService, str]:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), RecordingOutbox(), telegram_outbox=outbox)
    message = InboxMessage("telegram", "telegram:540308572|-1003724214344:305", "Никита",
                           "проверка доставки", 1.0, peer_id="-1003724214344")
    conversation_id, _ = service.receive(message, route_id="telegram",
                                          account_ref="telegram:540308572")
    draft = service.propose(conversation_id, message)
    return service, draft.id


def _unreachable_outbox(probe) -> TelegramQrHttpOutbox:
    def runner(request, **_kwargs):
        raise urllib.error.URLError("connection refused")

    return TelegramQrHttpOutbox("http://127.0.0.1:18095", "token", runner=runner, probe=probe)


def test_connector_that_is_not_listening_is_a_proven_non_submission(tmp_path):
    """The connector never accepted the request, so the claim is released."""
    service, draft_id = _telegram_service(tmp_path, _unreachable_outbox(refused_probe))
    with __import__("pytest").raises(DeliveryNotSubmittedError):
        service.approve(draft_id)
    # The exact retry stays possible: the draft is proposed again.
    assert service._store.draft(draft_id).status == "proposed"
    assert service._store.draft_delivery_state(draft_id)["delivery_state"] == "not_submitted"


def test_a_request_phase_failure_stays_uncertain(tmp_path):
    """The connector was reachable, so a failed request may already be on the wire."""
    service, draft_id = _telegram_service(tmp_path, _unreachable_outbox(reachable_probe))
    try:
        service.approve(draft_id)
    except DeliveryNotSubmittedError as error:
        raise AssertionError("a request-phase failure must stay uncertain") from error
    except RuntimeError:
        pass
    else:
        raise AssertionError("an unreachable request must raise")
    assert service._store.draft(draft_id).status == "sending"
    # The claim time is recorded, so the attempt can be reconciled later.
    state = service._store.draft_delivery_state(draft_id)
    assert state["sending_at"] and state["delivery_state"] == "claimed"


class _JournalOutbox:
    """A Telegram outbox whose connector journal answers with one record."""

    def __init__(self, record: dict, *, history: dict | None = None) -> None:
        self.record = record
        self.history = history or {"mirrored": 0, "fetched": 0, "oldest_id": 0, "has_more": False}
        self.sent: list[dict] = []
        self.history_reads: list[str] = []
        self.attempt_reads: list[str] = []

    def send_reply(self, *, chat, body, draft_id, chat_id="", account_ref="", slot=""):
        self.sent.append({"draft_id": draft_id, "body": body})
        # The provider accepted nothing and never answered: the outcome is
        # undecided, so the claim must survive.
        raise RuntimeError("telegram-qr reply outcome is unknown")

    def send_attempt(self, attempt_id: str) -> dict:
        self.attempt_reads.append(attempt_id)
        return dict(self.record, attempt_id=attempt_id)

    def reconcile_history(self, *, account_ref: str, peer_id: str, limit: int = 200,
                          before_id: int = 0) -> dict:
        self.history_reads.append(peer_id)
        return dict(self.history)


def _uncertain_draft(tmp_path, record: dict) -> tuple[UserIOService, str]:
    outbox = _JournalOutbox(record)
    service, draft_id = _telegram_service(tmp_path, outbox)
    try:
        service.approve(draft_id)
    except RuntimeError:
        pass
    assert service._store.draft(draft_id).status == "sending"
    return service, draft_id


def test_reconcile_records_the_provider_receipt_of_a_delivered_attempt(tmp_path):
    service, draft_id = _uncertain_draft(tmp_path, {
        "state": "sent", "slot": "account-1", "account_id": "telegram:540308572",
        "peer_id": "-1003724214344", "message_id": "306"})
    result = service.reconcile_draft_delivery(draft_id)
    assert result["resolution"] == "delivered"
    assert result["receipt"] == f"telegram-qr:account-1:306:{draft_id}"
    assert service._store.draft(draft_id).status == "approved"
    assert service._store.draft(draft_id).receipt == result["receipt"]


def test_reconcile_releases_a_proven_refusal(tmp_path):
    service, draft_id = _uncertain_draft(tmp_path, {
        "state": "refused", "code": "account_not_connected"})
    result = service.reconcile_draft_delivery(draft_id)
    assert result["resolution"] == "not_submitted"
    assert result["retry_allowed"] is True
    assert service._store.draft(draft_id).status == "proposed"


def test_reconcile_keeps_an_undecided_attempt_uncertain(tmp_path):
    service, draft_id = _uncertain_draft(tmp_path, {"state": "pending"})
    result = service.reconcile_draft_delivery(draft_id)
    assert result["resolution"] == "unknown"
    assert result["retry_allowed"] is False
    assert service._store.draft(draft_id).status == "sending"


def test_reconcile_keeps_a_legacy_claim_uncertain(tmp_path):
    """A claim taken before the connector could journal it proves nothing."""
    service, draft_id = _uncertain_draft(tmp_path, {"state": "absent", "epoch": 2 ** 31})
    result = service.reconcile_draft_delivery(draft_id)
    assert result["resolution"] == "unknown"
    assert result["reason"] == "journal_absence_is_not_final_submission_evidence"
    assert service._store.draft(draft_id).status == "sending"


def test_reconcile_keeps_an_absent_journal_unknown_while_request_might_still_arrive(tmp_path):
    service, draft_id = _uncertain_draft(tmp_path, {"state": "absent", "epoch": 1})
    result = service.reconcile_draft_delivery(draft_id)
    assert result["resolution"] == "unknown"
    assert result["reason"] == "journal_absence_is_not_final_submission_evidence"
    assert service._store.draft(draft_id).status == "sending"


def test_reconcile_refuses_a_mismatched_provider_record(tmp_path):
    service, draft_id = _uncertain_draft(tmp_path, {
        "state": "sent", "slot": "account-1", "account_id": "telegram:999",
        "peer_id": "-1003724214344", "message_id": "306"})
    result = service.reconcile_draft_delivery(draft_id)
    assert result["resolution"] == "unknown"
    assert result["reason"] == "provider_account_mismatch"
    assert service._store.draft(draft_id).status == "sending"


def test_mcp_requires_the_complete_draft_snapshot(tmp_path):
    outbox = _JournalOutbox({"state": "sent", "slot": "account-1",
                              "account_id": "telegram:540308572",
                              "peer_id": "-1003724214344", "message_id": "306"})
    service, draft_id = _telegram_service(tmp_path, outbox)
    surface = UserIOMcpSurface(service._store, service)
    body = service._store.draft(draft_id).body
    conversation = service._store.draft(draft_id).conversation_id
    # A partial snapshot cannot prove the draft did not change.
    partial = surface.dispatch("userio.draft.approve_send", {
        "draft_id": draft_id, "confirm": True, "expected_text": body,
        "expected_chat_id": conversation})
    assert partial["ok"] is False
    assert partial["error"] == "complete_draft_snapshot_required"
    # An absent snapshot would approve whatever the draft says now.
    absent = surface.dispatch("userio.draft.approve_send", {
        "draft_id": draft_id, "confirm": True})
    assert absent["ok"] is False
    assert absent["error"] == "complete_draft_snapshot_required"
    assert service._store.draft(draft_id).status == "proposed"
    assert outbox.sent == []


def test_mcp_reconcile_delivery_resolves_an_uncertain_draft(tmp_path):
    outbox = _JournalOutbox({"state": "sent", "slot": "account-1",
                              "account_id": "telegram:540308572",
                              "peer_id": "-1003724214344", "message_id": "306"})
    service, draft_id = _telegram_service(tmp_path, outbox)
    surface = UserIOMcpSurface(service._store, service)
    body = service._store.draft(draft_id).body
    conversation = service._store.draft(draft_id).conversation_id
    try:
        service.approve(draft_id)
    except RuntimeError:
        pass
    result = surface.dispatch("userio.draft.reconcile_delivery", {
        "draft_id": draft_id, "confirm": True, "expected_text": body,
        "expected_chat_id": conversation, "expected_attachments": []})
    assert result["ok"] is True
    assert result["resolution"] == "delivered"
    assert service._store.draft(draft_id).status == "approved"


def test_mcp_approve_reports_an_unknown_outcome_with_a_stable_code(tmp_path):
    outbox = _JournalOutbox({"state": "unknown"})
    service, draft_id = _telegram_service(tmp_path, outbox)
    surface = UserIOMcpSurface(service._store, service)
    body = service._store.draft(draft_id).body
    conversation = service._store.draft(draft_id).conversation_id
    try:
        service.approve(draft_id)
    except RuntimeError:
        pass
    result = surface.dispatch("userio.draft.approve_send", {
        "draft_id": draft_id, "confirm": True, "expected_text": body,
        "expected_chat_id": conversation, "expected_attachments": []})
    assert result["ok"] is False
    assert result["code"] == "delivery_outcome_unknown"
    assert result["retry_allowed"] is False
    assert len(outbox.sent) == 1


def _insert_provider_message(service, draft_id, native_id, received_at, direction="incoming",
                             body="provider message"):
    service._store._connection.execute(
        "INSERT INTO messages (user_id,source,message_id,conversation_id,sender,body,received_at,"
        "direction) VALUES (?,?,?,?,?,?,?,?)",
        (service._store.default_user_id, "telegram",
         f"telegram:540308572|-1003724214344:{native_id}",
         service._store.draft(draft_id).conversation_id, "Никита", body, float(received_at), direction))
    service._store._connection.commit()


def test_provider_history_absence_does_not_release_a_legacy_claim(tmp_path):
    """A bounded history no-match cannot prove an in-flight request won't arrive later."""
    outbox = _JournalOutbox({"state": "absent", "epoch": 2 ** 31},
                             history={"mirrored": 200, "fetched": 200, "oldest_id": "100"})
    service, draft_id = _telegram_service(tmp_path, outbox)
    try:
        service.approve(draft_id)
    except RuntimeError:
        pass
    claimed = service._store.draft_delivery_state(draft_id)
    # The fetched window starts before the attempt, so it covers it.
    _insert_provider_message(service, draft_id, "100", float(claimed["created_at"]) - 3600)
    result = service.reconcile_draft_delivery(
        draft_id, provider_history=True,
        connector_available_since=float(claimed["created_at"]) + 3600)
    assert result["resolution"] == "unknown"
    assert result["reason"] == "provider_history_receipt_identity_or_freshness_missing"
    assert service._store.draft(draft_id).status == "sending"
    assert outbox.history_reads == ["-1003724214344"]


def test_provider_history_needs_fresh_scoped_provider_evidence(tmp_path):
    outbox = _JournalOutbox({"state": "absent", "epoch": 2 ** 31},
                             history={"mirrored": 200, "fetched": 200, "oldest_id": "100"})
    service, draft_id = _telegram_service(tmp_path, outbox)
    try:
        service.approve(draft_id)
    except RuntimeError:
        pass
    result = service.reconcile_draft_delivery(draft_id, provider_history=True)
    assert result["resolution"] == "unknown"
    assert result["reason"] == "provider_history_receipt_identity_or_freshness_missing"
    assert service._store.draft(draft_id).status == "sending"
    assert outbox.history_reads == ['-1003724214344']


def test_provider_history_that_does_not_cover_the_attempt_stays_unknown(tmp_path):
    """The fetched window starts after the attempt: the absence proves nothing."""
    outbox = _JournalOutbox({"state": "absent", "epoch": 2 ** 31},
                             history={"mirrored": 200, "fetched": 200, "oldest_id": "100"})
    service, draft_id = _telegram_service(tmp_path, outbox)
    try:
        service.approve(draft_id)
    except RuntimeError:
        pass
    claimed = service._store.draft_delivery_state(draft_id)
    _insert_provider_message(service, draft_id, "100", float(claimed["created_at"]) + 60)
    result = service.reconcile_draft_delivery(
        draft_id, provider_history=True,
        connector_available_since=float(claimed["created_at"]) + 3600)
    assert result["resolution"] == "unknown"
    assert result["reason"] == "provider_history_receipt_identity_or_freshness_missing"
    assert service._store.draft(draft_id).status == "sending"


def test_provider_history_with_a_matching_message_stays_unknown(tmp_path):
    """The provider still holds a message with this exact body."""
    outbox = _JournalOutbox({"state": "absent", "epoch": 2 ** 31},
                             history={"mirrored": 200, "fetched": 200, "oldest_id": "100"})
    service, draft_id = _telegram_service(tmp_path, outbox)
    try:
        service.approve(draft_id)
    except RuntimeError:
        pass
    claimed = service._store.draft_delivery_state(draft_id)
    body = service._store.draft(draft_id).body
    _insert_provider_message(service, draft_id, "100", float(claimed["created_at"]) - 3600)
    _insert_provider_message(service, draft_id, "101", float(claimed["created_at"]) + 60,
                              direction="outgoing", body=body)
    result = service.reconcile_draft_delivery(
        draft_id, provider_history=True,
        connector_available_since=float(claimed["created_at"]) + 3600)
    assert result["resolution"] == "unknown"
    assert result["reason"] == "provider_history_receipt_identity_or_freshness_missing"
    assert service._store.draft(draft_id).status == "sending"


def test_send_attempt_reads_the_connector_journal(tmp_path):
    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()
            return False

    def run(request, **_kwargs):
        return _Response(json.dumps({
            "state": "sent", "slot": "account-1", "account_id": "telegram:540308572",
            "peer_id": "-1003724214344", "message_id": "306"}).encode())

    outbox = TelegramQrHttpOutbox("http://127.0.0.1:18095", "token", runner=run,
                                  probe=reachable_probe)
    record = outbox.send_attempt("draft_1")
    assert record["state"] == "sent"
    assert record["message_id"] == "306"


def _make_due(service, draft_id):
    service._store._connection.execute(
        "UPDATE drafts SET delivery_next_check_at=0 WHERE user_id=? AND id=?",
        (service._store.default_user_id, draft_id),
    )
    service._store._connection.commit()


def test_background_check_persists_exact_reason_and_does_not_send_again(tmp_path):
    outbox = _JournalOutbox({"state": "absent", "epoch": 2 ** 31})
    service, draft_id = _uncertain_draft(tmp_path, outbox.record)
    # Reuse the same outbox instance wired into the service, then make the row due.
    outbox = service.telegram_outbox
    _make_due(service, draft_id)
    worker = TelegramDeliveryReconciliationWorker(service)

    assert worker.run_once() == 1
    state = service._store.draft_delivery_state(draft_id)
    assert state["status"] == "sending"
    assert state["delivery_state"] == "legacy_unknown"
    assert state["attempts"] == 1
    assert state["reason"] == "provider_history_receipt_identity_or_freshness_missing"
    assert state["evidence"]["attempt_id"] == draft_id
    assert state["evidence"]["state"] == "absent"
    assert state["evidence"]["history"]["reason"] == "receipt_identity_or_freshness_missing"
    assert len(outbox.sent) == 1
    assert len(outbox.attempt_reads) == 1
    draft = service._store.conversation(service._store.draft(draft_id).conversation_id)["drafts"][0]
    assert draft["context_status"] == "current"
    assert draft["delivery_state"] == "legacy_unknown"
    assert draft["delivery_reason"] == state["reason"]
    assert draft["delivery_history"][0]["previous_status"] == "sending"
    assert draft["delivery_history"][0]["new_status"] == "sending"
    assert draft["delivery_history"][0]["evidence"]["attempt_id"] == draft_id


def test_old_or_proposed_drafts_are_not_queued_for_background_checks(tmp_path):
    outbox = _JournalOutbox({"state": "absent", "epoch": 2 ** 31})
    service, proposed_id = _telegram_service(tmp_path, outbox)
    assert service._store.claim_due_telegram_delivery_reconciliations(
        worker_id="test", now=2 ** 31) == []
    assert service._store.draft(proposed_id).status == "proposed"
    assert outbox.attempt_reads == []


def test_absent_journal_during_live_send_does_not_release_or_allow_retry(tmp_path):
    entered = threading.Event()
    finish = threading.Event()

    class BlockingOutbox(_JournalOutbox):
        def send_reply(self, *, chat, body, draft_id, chat_id="", account_ref="", slot=""):
            self.sent.append({"draft_id": draft_id, "body": body})
            entered.set()
            assert finish.wait(5)
            raise RuntimeError("provider send response was lost")

    outbox = BlockingOutbox({"state": "absent", "epoch": 2 ** 31})
    service, draft_id = _telegram_service(tmp_path, outbox)
    errors = []

    def approve_once():
        try:
            service.approve(draft_id)
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=approve_once)
    thread.start()
    assert entered.wait(2)
    _make_due(service, draft_id)
    worker = TelegramDeliveryReconciliationWorker(service)
    assert worker.run_once() == 1
    assert service._store.draft(draft_id).status == "sending"
    try:
        service.approve(draft_id)
    except ValueError as error:
        assert getattr(error, "code", "") == "delivery_outcome_unknown"
    else:
        raise AssertionError("a possibly in-flight send must remain non-retryable")
    assert len(outbox.sent) == 1
    finish.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert len(outbox.sent) == 1
    assert errors and isinstance(errors[0], RuntimeError)


def test_duplicate_worker_claim_is_single_row_and_recovers_after_lease_expiry(tmp_path):
    outbox = _JournalOutbox({"state": "pending"})
    service, draft_id = _uncertain_draft(tmp_path, outbox.record)
    _make_due(service, draft_id)
    now = time.time()
    first = service._store.claim_due_telegram_delivery_reconciliations(
        worker_id="worker-a", now=now, limit=10, lease_seconds=10)
    second = service._store.claim_due_telegram_delivery_reconciliations(
        worker_id="worker-b", now=now + 1, limit=10, lease_seconds=10)
    assert len(first) == 1 and first[0]["id"] == draft_id
    assert second == []
    recovered = service._store.claim_due_telegram_delivery_reconciliations(
        worker_id="worker-b", now=now + 11, limit=10, lease_seconds=10)
    assert len(recovered) == 1
    assert recovered[0]["delivery_attempt_generation"] == first[0]["delivery_attempt_generation"]


def test_expired_worker_result_is_fenced_by_current_lease_owner(tmp_path):
    service, draft_id = _uncertain_draft(tmp_path, {"state": "pending"})
    outbox = service.telegram_outbox
    _make_due(service, draft_id)
    now = time.time()
    first = service._store.claim_due_telegram_delivery_reconciliations(
        worker_id="worker-a", now=now, limit=1, lease_seconds=5)[0]
    second = service._store.claim_due_telegram_delivery_reconciliations(
        worker_id="worker-b", now=now + 6, limit=1, lease_seconds=30)[0]
    generation = int(first["delivery_attempt_generation"])
    outbox.record = {"state": "sent", "slot": "account-1",
                     "account_id": "telegram:540308572", "peer_id": "-1003724214344",
                     "message_id": "411"}
    stale = service.reconcile_draft_delivery(
        draft_id, user_id=str(first["user_id"]), automatic=True,
        expected_generation=generation, expected_lease_owner="worker-a")
    assert stale["resolution"] == "superseded"
    assert service._store.draft(draft_id).status == "sending"
    fresh = service.reconcile_draft_delivery(
        draft_id, user_id=str(second["user_id"]), automatic=True,
        expected_generation=generation, expected_lease_owner="worker-b")
    assert fresh["resolution"] == "delivered"
    assert service._store.draft(draft_id).status == "approved"


def test_generation_change_fences_stale_reconciliation_result(tmp_path):
    outbox = _JournalOutbox({"state": "pending"})
    service, draft_id = _telegram_service(tmp_path, outbox)
    store = service._store
    store.claim_draft_send(draft_id, user_id=store.default_user_id)
    old_generation = int(store.draft_delivery_state(draft_id)["generation"])
    store.release_reconciled_delivery(draft_id, user_id=store.default_user_id,
        expected_generation=old_generation, reason="explicit_refusal")
    store.claim_draft_send(draft_id, user_id=store.default_user_id)
    new_generation = int(store.draft_delivery_state(draft_id)["generation"])
    assert new_generation == old_generation + 1

    result = service.reconcile_draft_delivery(
        draft_id, user_id=store.default_user_id, automatic=True,
        expected_generation=old_generation)
    state = store.draft_delivery_state(draft_id)
    assert result["resolution"] == "superseded"
    assert state["status"] == "sending"
    assert state["generation"] == new_generation
    assert state["delivery_state"] == "claimed"
    assert outbox.attempt_reads == []


def test_late_sent_receipt_resolves_delivery_unknown_without_send_approval(tmp_path):
    service, draft_id = _uncertain_draft(tmp_path, {"state": "pending"})
    outbox = service.telegram_outbox
    state = service._store.draft_delivery_state(draft_id)
    terminal = service.reconcile_draft_delivery(
        draft_id, automatic=True, automatic_terminal_unknown=True,
        expected_generation=int(state["generation"]),
    )
    assert terminal["status"] == "delivery_unknown"
    assert service._store.draft(draft_id).status == "delivery_unknown"
    try:
        service.approve(draft_id)
    except ValueError as error:
        assert getattr(error, "code", "") == "delivery_outcome_unknown"
    else:
        raise AssertionError("terminal uncertainty must not restore send approval")
    try:
        service._store.delete_draft(draft_id)
    except ValueError:
        pass
    else:
        raise AssertionError("terminal uncertainty must preserve the receipt candidate")
    outbox.record = {"state": "sent", "slot": "account-1",
                     "account_id": "telegram:540308572", "peer_id": "-1003724214344",
                     "message_id": "412"}
    resolved = service.reconcile_draft_delivery(draft_id)
    assert resolved["resolution"] == "delivered"
    assert service._store.draft(draft_id).status == "approved"
    assert service._store.draft(draft_id).receipt == f"telegram-qr:account-1:412:{draft_id}"
    assert len(outbox.sent) == 1


def test_automatic_reconciliation_continues_after_delivery_unknown(tmp_path):
    service, draft_id = _uncertain_draft(tmp_path, {"state": "pending"})
    outbox = service.telegram_outbox
    original_send_count = len(outbox.sent)
    worker = TelegramDeliveryReconciliationWorker(service)

    for check in range(1, 8):
        _make_due(service, draft_id)
        assert worker.run_once() == 1
        state = service._store.draft_delivery_state(draft_id)
        assert state["attempts"] == check
        assert state["next_check_at"] is not None
        if check < 6:
            assert state["status"] == "sending"
        else:
            assert state["status"] == "delivery_unknown"

    state = service._store.draft_delivery_state(draft_id)
    assert state["next_check_at"] >= time.time() + 7199
    assert outbox.attempt_reads == [draft_id] * 7
    assert len(outbox.sent) == original_send_count == 1
    assert service._store.claim_due_telegram_delivery_reconciliations(
        worker_id="not-yet-due", now=state["next_check_at"] - 1) == []


def test_adapter_429_retry_after_is_preserved_without_response_body(tmp_path):
    def run(request, **_kwargs):
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests",
                                     {"Retry-After": "3600"}, io.BytesIO(b"SECRET RESPONSE BODY"))

    outbox = TelegramQrHttpOutbox("http://127.0.0.1:18095", "token", runner=run,
                                  probe=reachable_probe)
    service, draft_id = _telegram_service(tmp_path, outbox)
    service._store.claim_draft_send(draft_id, user_id=service._store.default_user_id)
    _make_due(service, draft_id)
    before = time.time()
    worker = TelegramDeliveryReconciliationWorker(service)
    assert worker.run_once() == 1
    state = service._store.draft_delivery_state(draft_id)
    assert state["next_check_at"] >= before + 3600
    assert state["evidence"]["read_error_type"] == "TelegramQrHttpError"
    assert "SECRET RESPONSE BODY" not in json.dumps(state)
    assert service._store.claim_due_telegram_delivery_reconciliations(
        worker_id="early-check", now=state["next_check_at"] - 1) == []


def test_stale_unknown_check_cannot_overwrite_a_concurrent_receipt(tmp_path):
    entered = threading.Event()
    finish = threading.Event()

    class BlockingCheckOutbox(_JournalOutbox):
        def send_attempt(self, attempt_id):
            self.attempt_reads.append(attempt_id)
            entered.set()
            assert finish.wait(5)
            return {"state": "unknown", "attempt_id": attempt_id}

    outbox = BlockingCheckOutbox({"state": "pending"})
    service, draft_id = _telegram_service(tmp_path, outbox)
    try:
        service.approve(draft_id)
    except RuntimeError:
        pass
    result = []
    thread = threading.Thread(target=lambda: result.append(service.reconcile_draft_delivery(draft_id)))
    thread.start()
    assert entered.wait(2)
    generation = int(service._store.draft_delivery_state(draft_id)["generation"])
    receipt = f"telegram-qr:account-1:413:{draft_id}"
    applied = service._store.approve_reconciled_delivery(
        draft_id, receipt, user_id=service._store.default_user_id,
        expected_generation=generation)
    assert applied["applied"] is True
    finish.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    state = service._store.draft_delivery_state(draft_id)
    assert result[0]["resolution"] == "superseded"
    assert result[0]["status"] == "approved"
    assert state["receipt"] == receipt
    assert state["delivery_state"] == "delivered"


def test_ordinary_confirmed_approval_clears_the_scheduled_delivery_check(tmp_path):
    service, draft_id = _telegram_service(tmp_path, _JournalOutbox({"state": "pending"}))
    store = service._store
    store.claim_draft_send(draft_id, user_id=store.default_user_id)
    assert store.draft_delivery_state(draft_id)["next_check_at"] is not None
    store.approve(draft_id, "provider-receipt", user_id=store.default_user_id)
    state = store.draft_delivery_state(draft_id)
    assert state["status"] == "approved"
    assert state["delivery_state"] == "delivered"
    assert state["next_check_at"] is None


def test_restart_worker_resolves_original_attempt_from_same_sqlite_database(tmp_path):
    service, draft_id = _uncertain_draft(tmp_path, {"state": "pending"})
    original_store = service._store
    database_path = tmp_path / "userio.sqlite3"
    outbox = service.telegram_outbox
    _make_due(service, draft_id)
    original_store._connection.close()

    reopened = SQLiteUserIOStore(database_path)
    outbox.record = {"state": "sent", "slot": "account-1",
                     "account_id": "telegram:540308572", "peer_id": "-1003724214344",
                     "message_id": "414"}
    resumed = UserIOService(reopened, Generator(), RecordingOutbox(), telegram_outbox=outbox)
    worker = TelegramDeliveryReconciliationWorker(resumed)

    assert worker.run_once() == 1
    state = reopened.draft_delivery_state(draft_id)
    assert state["status"] == "approved"
    assert state["delivery_state"] == "delivered"
    assert state["receipt"] == f"telegram-qr:account-1:414:{draft_id}"
    assert state["next_check_at"] is None
    assert len(outbox.sent) == 1
    assert outbox.attempt_reads == [draft_id]
