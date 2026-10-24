"""First contact and first delivery: what the tools promise and what they prove.

Covers the four defects found in production use:

1. a Telegram account whose connector session is down must say so in
   capabilities instead of failing at call time;
2. a mail receipt must not claim a delivery that nothing confirmed;
3. approving the same draft twice must not send twice;
4. a draft that was sent must not read as "still to send".
"""
from __future__ import annotations

import urllib.error
from types import SimpleNamespace

import pytest

from universal_userio.adapters import HimalayaGmailOutbox, TelegramQrHttpOutbox
from universal_userio.contracts import ContactResolutionUnavailable, InboxMessage
from universal_userio.delivery_receipt import (
    DELIVERED, SMTP_ACCEPTED, DeliveryReceiptError, assert_truthful, delivery_fields,
    read_receipt,
)
from universal_userio.mcp_surface import UserIOMcpSurface
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def propose(self, *_args, **_kwargs):
        return ""


class Outbox:
    def __init__(self):
        self.calls = []

    def send(self, *_args, **_kwargs):
        return "noop"

    def send_reply(self, **kwargs):
        self.calls.append(dict(kwargs))
        return "receipt"


class FakeTelegramOutbox:
    """A connector whose live session state is known up front."""

    def __init__(self, *, resolvable=True, reason=""):
        self._verdict = {"resolvable": resolvable, "reason": reason}
        self.resolved = []
        self.sent = []

    def contact_resolution_status(self, account_ref):
        return dict(self._verdict)

    def resolve_contact(self, identifier, *, account_ref):
        if self._verdict["resolvable"] is False:
            raise ContactResolutionUnavailable(self._verdict["reason"] or "connector_unreachable")
        self.resolved.append((identifier, account_ref))
        return {"peer_id": "5045123456", "name": "Новый контакт", "username": identifier}

    def send_reply(self, **kwargs):
        self.sent.append(dict(kwargs))
        return "telegram:receipt"


class FakeHimalayaRunner:
    """Record every Himalaya call and let the Sent append fail on demand."""

    def __init__(self, *, sent_copy_ok=True):
        self.calls = []
        self._sent_copy_ok = sent_copy_ok

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs.get("input")))
        if "add" in argv:
            return SimpleNamespace(returncode=0 if self._sent_copy_ok else 1, stdout="", stderr="rejected")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def build(tmp_path, **kwargs):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    outbox = Outbox()
    service = UserIOService(store, Generator(), outbox, **kwargs)
    return store, service, outbox, store.default_user_id


def capabilities_of(store, service, account_ref):
    """Read the capabilities exactly as a consumer reads them."""
    surface = UserIOMcpSurface(store, service)
    result = surface.dispatch("userio.messenger.capabilities", {"account_ref": account_ref})
    assert result["ok"] is True
    return result


def register_telegram(store, user, *, can_reply=True):
    store.register_account(account_id="telegram:5045123456", provider="telegram", display_name="Telegram",
                           enabled=True, can_read=True, can_reply=can_reply,
                           credential_ref="telegram-qr:account-5", user_id=user)


def register_gmail(store, user, *, can_reply=True):
    store.register_account(account_id="gmail-test", provider="gmail", display_name="owner@example.com",
                           enabled=True, can_read=True, can_reply=can_reply, credential_ref="himalaya:test",
                           user_id=user)


def register_matrix(store, user):
    """The reply-capable Matrix account the current compose contract requires."""
    store.register_account(account_id="matrix:1", provider="matrix", display_name="Matrix",
                           enabled=True, can_read=True, can_reply=True,
                           credential_ref="connector:matrix", user_id=user)


# -- defect 1: a first contact is impossible while the connector is down ------------


def test_capabilities_report_that_the_connector_is_unreachable(tmp_path):
    telegram = FakeTelegramOutbox(resolvable=False, reason="connector_unreachable")
    store, service, outbox, user = build(tmp_path, telegram_outbox=telegram)
    register_telegram(store, user)

    capabilities = capabilities_of(store, service, "telegram:5045123456")

    first_contact = capabilities["first_contact"]
    assert first_contact["supported"] is False
    assert first_contact["availability"] == "unavailable"
    assert first_contact["reason"] == "connector_unreachable"
    assert first_contact["operations"]["resolve"]["reason"] == "connector_unreachable"
    assert first_contact["operations"]["prepare"]["reason"] == "connector_unreachable"


def test_capabilities_report_an_account_without_a_live_session(tmp_path):
    telegram = FakeTelegramOutbox(resolvable=False, reason="account_not_connected")
    store, service, outbox, user = build(tmp_path, telegram_outbox=telegram)
    register_telegram(store, user)

    first_contact = capabilities_of(store, service, "telegram:5045123456")["first_contact"]

    assert first_contact["supported"] is False
    assert first_contact["reason"] == "account_not_connected"


def test_capabilities_report_a_working_first_contact(tmp_path):
    telegram = FakeTelegramOutbox(resolvable=True)
    store, service, outbox, user = build(tmp_path, telegram_outbox=telegram)
    register_telegram(store, user)

    first_contact = capabilities_of(store, service, "telegram:5045123456")["first_contact"]

    assert first_contact["supported"] is True
    assert first_contact["allowed"] is True
    assert first_contact["availability"] == "available"
    assert first_contact["reason"] == ""


def test_a_first_contact_without_reply_right_is_not_allowed(tmp_path):
    telegram = FakeTelegramOutbox(resolvable=True)
    store, service, outbox, user = build(tmp_path, telegram_outbox=telegram)
    register_telegram(store, user, can_reply=False)

    first_contact = capabilities_of(store, service, "telegram:5045123456")["first_contact"]

    assert first_contact["operations"]["prepare"]["allowed"] is False
    assert first_contact["operations"]["prepare"]["reason"] == "account_capability_disabled"
    assert first_contact["operations"]["resolve"]["allowed"] is True


def test_the_unreachable_connector_is_named_instead_of_leaking_a_transport_string():
    def unreachable(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    outbox = TelegramQrHttpOutbox("http://127.0.0.1:18095", "token", runner=unreachable)

    with pytest.raises(ContactResolutionUnavailable) as error:
        outbox.resolve_contact("someone", account_ref="telegram:1")

    assert error.value.code == "connector_unreachable"
    assert "telegram-qr connector is unreachable" not in str(error.value)


def test_the_resolve_answer_carries_the_machine_readable_code(tmp_path):
    telegram = FakeTelegramOutbox(resolvable=False, reason="connector_unreachable")
    store, service, outbox, user = build(tmp_path, telegram_outbox=telegram)
    register_telegram(store, user)
    surface = UserIOMcpSurface(store, service)

    result = surface.dispatch("userio.contacts.resolve", {
        "account_ref": "telegram:5045123456", "identifier": "@someone"})

    assert result["ok"] is False
    assert result["code"] == "connector_unreachable"
    assert result["error"] == "connector_unreachable"


def test_the_connector_state_probe_names_a_down_connector():
    def refused(*_args, **_kwargs):
        raise ConnectionRefusedError("connection refused")

    outbox = TelegramQrHttpOutbox("http://127.0.0.1:18095", "token", probe=refused)

    assert outbox.contact_resolution_status("telegram:1") == {
        "resolvable": False, "reason": "connector_unreachable"}


# -- defect 2: a receipt must not claim a delivery nothing confirmed ---------------


def test_the_mail_receipt_carries_the_provider_message_id_and_the_sent_copy():
    runner = FakeHimalayaRunner()
    outbox = HimalayaGmailOutbox(runner=runner)

    receipt = outbox.send_message(account="airlock", sender="owner@example.com",
                                  recipient="person@example.com", message_id="",
                                  body="Привет", draft_id="draft_web_1", subject="Привет")

    message_id = HimalayaGmailOutbox.outgoing_message_id("airlock", "draft_web_1")
    assert receipt == f"himalaya:airlock:draft_web_1:{message_id}:saved"
    fields = delivery_fields(receipt)
    assert fields["delivery_status"] == SMTP_ACCEPTED
    assert fields["sent"] is False
    assert fields["delivery_unconfirmed"] is True
    assert fields["message_id"] == message_id
    assert fields["idempotency_key"] == message_id
    assert fields["sent_copy"]["saved"] == "true"
    # One SMTP send plus one IMAP APPEND of the very same letter.
    assert runner.calls[0][0][-2:] == ["message", "send"]
    assert runner.calls[1][0][-4:] == ["message", "add", "--mailbox", "sent"]
    assert runner.calls[1][0][-4:] == ["message", "add", "--mailbox", "sent"]
    assert runner.calls[0][1] == runner.calls[1][1]


def test_a_missing_sent_copy_is_named_and_never_faked():
    runner = FakeHimalayaRunner(sent_copy_ok=False)
    outbox = HimalayaGmailOutbox(runner=runner)

    receipt = outbox.send_message(account="airlock", sender="owner@example.com",
                                  recipient="person@example.com", message_id="",
                                  body="Привет", draft_id="draft_web_2", subject="Привет")

    assert receipt.endswith(":not_saved_rejected")
    fields = delivery_fields(receipt)
    assert fields["sent_copy"]["saved"] == "false"
    assert fields["sent_copy"]["reason"] == "sent_copy_rejected"
    assert fields["sent"] is False


def test_a_delivery_cannot_be_claimed_without_a_confirmation():
    with pytest.raises(DeliveryReceiptError):
        assert_truthful(DELIVERED, False, "id-1")
    with pytest.raises(DeliveryReceiptError):
        assert_truthful(DELIVERED, True, "")
    assert_truthful(SMTP_ACCEPTED, False, "id-1") is None


def test_a_legacy_provider_receipt_keeps_proving_delivery():
    fields = delivery_fields("telegram-qr:account-5:12345:draft_web_3")

    assert fields["delivery_status"] == DELIVERED
    assert fields["sent"] is True
    assert fields["delivery_confirmed"] is True


def test_an_empty_receipt_proves_nothing():
    fields = delivery_fields("")

    assert fields["sent"] is False
    assert fields["delivery_confirmed"] is False
    assert fields["delivery_status"] == "unknown"


def test_the_approved_mail_answers_with_the_honest_delivery_state(tmp_path):
    store, service, outbox, user = build(tmp_path)
    register_gmail(store, user)
    conversation, _ = service.receive(
        InboxMessage("gmail:test", "gmail-1", "person@example.com", "вопрос", 10.0),
        route_id="gmail", account_ref="gmail-test")
    surface = UserIOMcpSurface(store, service)
    draft = surface.dispatch("userio.messages.prepare", {
        "conversation_id": conversation, "text": "ответ",
        "request_id": "first-contact-mail-0001"})["draft_id"]
    store.approve(draft, "himalaya:airlock:draft_web_4:abc@userio.local:saved", user_id=user)

    result = surface.dispatch("userio.messages.send", {
        "draft_id": draft, "confirm": True, "expected_text": "ответ",
        "expected_chat_id": conversation, "expected_attachments": []})

    assert result["ok"] is True
    assert result["sent"] is False
    assert result["delivery_status"] == SMTP_ACCEPTED
    assert result["delivery_unconfirmed"] is True
    assert result["message_id"] == "abc@userio.local"
    assert result["retry_allowed"] is False
    assert "no provider delivery confirmation" in result["delivery_note"]


# -- defect 3: approving the same draft twice must not send twice ------------------


def test_a_second_approval_replays_the_outcome_instead_of_sending_again(tmp_path):
    store, service, outbox, user = build(tmp_path)
    register_matrix(store, user)
    conversation, _ = service.receive(
        InboxMessage("matrix", "1", "anna", "hello", 1.0), route_id="reply", account_ref="matrix:1")
    surface = UserIOMcpSurface(store, service)
    draft = surface.dispatch("userio.messages.prepare", {
        "conversation_id": conversation, "text": "exact reply",
        "request_id": "second-approval-replay-0001"})["draft_id"]
    args = {"draft_id": draft, "confirm": True, "expected_text": "exact reply",
            "expected_chat_id": conversation, "expected_attachments": []}

    first = surface.dispatch("userio.messages.send", args)
    second = surface.dispatch("userio.messages.send", args)

    assert first["ok"] is True and second["ok"] is True
    assert first["receipt"] == second["receipt"]
    assert first["replay"] is False
    assert second["replay"] is True
    assert len(outbox.calls) == 1


def test_a_replayed_approval_keeps_the_same_receipt_and_message_id(tmp_path):
    store, service, outbox, user = build(tmp_path)
    register_matrix(store, user)
    conversation, _ = service.receive(
        InboxMessage("matrix", "1", "anna", "hello", 1.0), route_id="reply", account_ref="matrix:1")
    surface = UserIOMcpSurface(store, service)
    draft = surface.dispatch("userio.messages.prepare", {
        "conversation_id": conversation, "text": "exact reply",
        "request_id": "replayed-approval-0001"})["draft_id"]
    args = {"draft_id": draft, "confirm": True, "expected_text": "exact reply",
            "expected_chat_id": conversation, "expected_attachments": []}

    first = surface.dispatch("userio.messages.send", args)
    second = surface.dispatch("userio.messages.send", args)

    assert first["idempotency_key"] == second["idempotency_key"]
    assert second["retry_allowed"] is False


# -- defect 4: a sent draft must not read as "still to send" ------------------------


def test_a_sent_draft_says_so_and_leaves_the_pending_list(tmp_path):
    store, service, outbox, user = build(tmp_path)
    register_matrix(store, user)
    conversation, _ = service.receive(
        InboxMessage("matrix", "1", "anna", "hello", 1.0), route_id="reply", account_ref="matrix:1")
    surface = UserIOMcpSurface(store, service)
    draft = surface.dispatch("userio.messages.prepare", {
        "conversation_id": conversation, "text": "exact reply",
        "request_id": "sent-draft-leaves-pending-0001"})["draft_id"]
    args = {"draft_id": draft, "confirm": True, "expected_text": "exact reply",
            "expected_chat_id": conversation, "expected_attachments": []}
    surface.dispatch("userio.messages.send", args)

    kept = store.draft(draft)
    assert kept.status == "approved"
    assert kept.receipt == "receipt"

    listed = store.conversation(conversation, user_id=user)["drafts"]
    assert [item["id"] for item in listed] == [draft]
    assert listed[0]["sent"] is True
    assert listed[0]["status"] == "approved"

    assert store.pending_drafts(user_id=user) == []


def test_a_draft_that_is_not_sent_still_reads_as_pending(tmp_path):
    store, service, outbox, user = build(tmp_path)
    register_matrix(store, user)
    conversation, _ = service.receive(
        InboxMessage("matrix", "1", "anna", "hello", 1.0), route_id="reply", account_ref="matrix:1")
    surface = UserIOMcpSurface(store, service)
    draft = surface.dispatch("userio.messages.prepare", {
        "conversation_id": conversation, "text": "exact reply",
        "request_id": "unsent-draft-stays-pending-0001"})["draft_id"]

    listed = store.conversation(conversation, user_id=user)["drafts"]
    assert listed[0]["sent"] is False
    assert listed[0]["status"] == "proposed"
    assert [item["id"] for item in store.pending_drafts(user_id=user)] == [draft]


def test_the_receipt_of_a_sent_draft_is_never_empty(tmp_path):
    store, service, outbox, user = build(tmp_path)
    register_matrix(store, user)
    conversation, _ = service.receive(
        InboxMessage("matrix", "1", "anna", "hello", 1.0), route_id="reply", account_ref="matrix:1")
    surface = UserIOMcpSurface(store, service)
    draft = surface.dispatch("userio.messages.prepare", {
        "conversation_id": conversation, "text": "exact reply",
        "request_id": "empty-receipt-0001"})["draft_id"]

    assert read_receipt(store.draft(draft).receipt)["delivery_status"] == "unknown"


def test_a_sent_mail_letter_shows_up_in_its_own_chat(tmp_path):
    """The letter the transport accepted must be visible in the conversation."""
    runner = FakeHimalayaRunner()
    gmail = HimalayaGmailOutbox(runner=runner)
    store, service, outbox, user = build(tmp_path, gmail_outbox=gmail)
    register_gmail(store, user)

    # The current first-contact route: resolve the peer, prepare one draft and
    # deliver it through the reviewed approval.
    prepared = service.contact_starter.prepare(
        user_id=user, account_ref="gmail-test", identifier="person@example.com",
        text="Привет", request_id="contact-start-mirror")
    surface = UserIOMcpSurface(store, service)
    sent = surface.dispatch("userio.messages.send", {
        "draft_id": prepared["draft_id"], "confirm": True,
        "expected_text": prepared["body"], "expected_chat_id": prepared["conversation_id"],
        "expected_attachments": []})
    assert sent["ok"] is True
    # The mail transport accepts the letter but cannot confirm delivery, so
    # the honest state is an accepted, unconfirmed send.
    assert sent["delivery_status"] == SMTP_ACCEPTED
    assert sent["delivery_unconfirmed"] is True

    conversation = store.conversation(prepared["conversation_id"], user_id=user)
    outgoing = [item for item in conversation["messages"] if item.get("direction") == "outgoing"]
    assert len(outgoing) == 1
    assert outgoing[0]["body"] == "Привет"
    assert outgoing[0]["message_id"] == HimalayaGmailOutbox.outgoing_message_id(
        "test", prepared["draft_id"])


def test_the_first_contact_answer_reports_what_the_transport_proved(tmp_path):
    runner = FakeHimalayaRunner()
    gmail = HimalayaGmailOutbox(runner=runner)
    store, service, outbox, user = build(tmp_path, gmail_outbox=gmail)
    register_gmail(store, user)

    prepared = service.contact_starter.prepare(
        user_id=user, account_ref="gmail-test", identifier="person@example.com",
        text="Привет", request_id="contact-start-answer")
    surface = UserIOMcpSurface(store, service)
    sent = surface.dispatch("userio.messages.send", {
        "draft_id": prepared["draft_id"], "confirm": True,
        "expected_text": prepared["body"], "expected_chat_id": prepared["conversation_id"],
        "expected_attachments": []})

    assert sent["ok"] is True
    assert sent["sent"] is False
    assert sent["delivery_status"] == SMTP_ACCEPTED
    assert sent["delivery_unconfirmed"] is True
    assert sent["message_id"] == HimalayaGmailOutbox.outgoing_message_id(
        "test", prepared["draft_id"])
    assert sent["retry_allowed"] is False
