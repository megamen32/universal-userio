"""Telegram delivery: a proven non-submission is recoverable, an uncertain one is not.

The 20.10.2026 incident: the telegram-qr connector never started, so an
approved send answered "connector unreachable". The outbox reported that as an
uncertain error, UserIO kept its durable claim, and the draft stayed in
`sending` forever with no way out.

These cases pin the repaired contract:

- a connector that is provably not accepting connections is a proven
  non-submission, so the claim is released and the exact retry is possible;
- every other transport failure stays uncertain and is never replayed;
- an uncertain attempt is resolved against the connector's durable journal of
  approved attempts, never against a message body.
"""

from __future__ import annotations

import io
import json
import urllib.error

from connector_probe import reachable_probe, refused_probe
from universal_userio.adapters import TelegramQrHttpOutbox
from universal_userio.contracts import DeliveryNotSubmittedError, InboxMessage
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

    def send_reply(self, *, chat, body, draft_id, chat_id="", account_ref="", slot=""):
        self.sent.append({"draft_id": draft_id, "body": body})
        # The provider accepted nothing and never answered: the outcome is
        # undecided, so the claim must survive.
        raise RuntimeError("telegram-qr reply outcome is unknown")

    def send_attempt(self, attempt_id: str) -> dict:
        return dict(self.record, attempt_id=attempt_id)

    def reconcile_history(self, *, account_ref: str, peer_id: str, limit: int = 200) -> dict:
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
    assert result["reason"] == "attempt_predates_connector_journal"
    assert service._store.draft(draft_id).status == "sending"


def test_reconcile_releases_an_attempt_the_connector_never_received(tmp_path):
    service, draft_id = _uncertain_draft(tmp_path, {"state": "absent", "epoch": 1})
    result = service.reconcile_draft_delivery(draft_id)
    assert result["resolution"] == "not_submitted"
    assert result["reason"] == "connector_never_received_attempt"
    assert service._store.draft(draft_id).status == "proposed"


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


def test_provider_history_releases_a_legacy_claim_with_an_attested_connector(tmp_path):
    """The provider holds no such message and the connector could not have served."""
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
    assert result["resolution"] == "not_submitted"
    assert result["reason"] == "not_delivered_by_provider_history"
    assert service._store.draft(draft_id).status == "proposed"
    assert outbox.history_reads == ["-1003724214344"]


def test_provider_history_needs_the_connector_availability_attestation(tmp_path):
    outbox = _JournalOutbox({"state": "absent", "epoch": 2 ** 31},
                             history={"mirrored": 200, "fetched": 200, "oldest_id": "100"})
    service, draft_id = _telegram_service(tmp_path, outbox)
    try:
        service.approve(draft_id)
    except RuntimeError:
        pass
    result = service.reconcile_draft_delivery(draft_id, provider_history=True)
    assert result["resolution"] == "unknown"
    assert result["reason"] == "connector_availability_not_attested"
    assert service._store.draft(draft_id).status == "sending"
    assert outbox.history_reads == []


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
    assert result["reason"] == "provider_history_does_not_cover_the_attempt"
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
    assert result["reason"] == "provider_has_a_matching_message"
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
