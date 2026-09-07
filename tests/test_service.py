from __future__ import annotations

from universal_userio.contracts import InboxMessage
from universal_userio.adapters import inbox_message_from_envelope
from universal_userio.service import DeliveryUnavailableError, UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def suggest(self, *, conversation_id: str, latest_message: InboxMessage) -> str:
        return f"Thanks for: {latest_message.body}"


class Outbox:
    def __init__(self) -> None:
        self.calls = []

    def send_reply(self, *, route_id: str, conversation_id: str, draft_id: str, body: str) -> str:
        self.calls.append((route_id, conversation_id, draft_id, body))
        return "delivery-receipt-1"


def test_message_to_draft_to_approved_outbox_reply(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    outbox = Outbox()
    service = UserIOService(store, Generator(), outbox)
    message = InboxMessage("telegram", "chat:1", "chat", "hello", 1.0)

    conversation_id, accepted = service.receive(message, route_id="telegram-reply")
    draft = service.propose(conversation_id, message)
    approved = service.approve(draft.id)

    assert accepted is True
    assert approved.status == "approved"
    assert outbox.calls == [("telegram-reply", conversation_id, draft.id, "Thanks for: hello")]
    assert store.conversation(conversation_id)["drafts"][0]["outbox_receipt"] == "delivery-receipt-1"


def test_duplicate_ingress_is_suppressed_and_rejection_never_sends(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    outbox = Outbox()
    service = UserIOService(store, Generator(), outbox)
    message = InboxMessage("vk", "42", "customer", "question", 1.0)

    conversation_id, accepted = service.receive(message, route_id="vk-reply")
    _, duplicate = service.receive(message, route_id="vk-reply")
    draft = service.propose(conversation_id, message)
    rejected = store.reject(draft.id)

    assert accepted is True
    assert duplicate is False
    assert rejected.status == "rejected"
    assert outbox.calls == []
    try:
        service.approve(draft.id)
    except ValueError as error:
        assert str(error) == "draft is not approvable"
    else:
        raise AssertionError("rejected draft was sent")
    assert outbox.calls == []


def test_gmail_source_approves_through_its_himalaya_outbox(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    store.register_account(
        account_id="gmail-careviolan", provider="gmail", display_name="careviolan@gmail.com",
        can_read=True, can_reply=True, credential_ref="himalaya:careviolan",
    )
    outbox = Outbox()
    class GmailOutbox:
        def __init__(self) -> None:
            self.calls = []
        def send_reply(self, **kwargs) -> str:
            self.calls.append(kwargs)
            return "himalaya:careviolan:draft"
    gmail_outbox = GmailOutbox()
    service = UserIOService(store, Generator(), outbox, gmail_outbox=gmail_outbox)
    conversation_id, _ = service.receive(
        InboxMessage("gmail:careviolan", "m-1", "sender", "hello", 1.0), route_id="gmail-read-only"
    )
    draft = service.create_manual_draft(conversation_id, body="reply")

    approved = service.approve(draft.id)
    assert approved.status == "approved"
    assert gmail_outbox.calls == [{"account": "careviolan", "sender": "careviolan@gmail.com", "recipient": "sender", "message_id": "m-1", "body": "reply", "draft_id": draft.id}]
    assert outbox.calls == []



def test_canonical_inbox_envelope_contract() -> None:
    message = inbox_message_from_envelope(
        {"schema": "universal.inbox.message.v1", "source": "vk", "message_id": "m-1", "sender": "customer", "body": "hello"},
        received_at=1.0,
    )
    assert message.conversation_key == "vk:customer"
    assert message.message_id == "m-1"

def test_identity_rule_enables_autosend_and_new_message_feed(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    store.register_identity(source="vk", external_id="42", identity_id="person_anna", display_name="Anna")
    store.set_rule(identity_id="person_anna", source="vk", route_id="vip-vk", mode="auto_send")
    outbox = Outbox()
    service = UserIOService(store, Generator(), outbox)
    message = InboxMessage("vk", "43", "42", "urgent", 1.0)

    conversation_id, accepted, draft = service.receive_and_plan(message, route_id="ordinary-vk")

    assert accepted is True
    assert draft is not None and draft.status == "approved"
    assert outbox.calls == [("vip-vk", conversation_id, draft.id, "Thanks for: urgent")]
    assert store.conversation(conversation_id)["identity_id"] == "person_anna"
    assert store.new_messages() == [{"source": "vk", "message_id": "43", "sender": "42", "body": "urgent", "received_at": 1.0, "conversation_id": conversation_id, "identity_id": "person_anna"}]
    assert store.mark_seen(source="vk", message_id="43") is True
    assert store.new_messages() == []


def test_ai_variants_are_independent_approvable_drafts(tmp_path) -> None:
    class VariantGenerator:
        def suggest(self, **_kwargs) -> str:
            return "fallback"

        def suggest_variants(self, **_kwargs):
            return ["formal reply", "friendly reply", ""]

    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, VariantGenerator(), Outbox())
    message = InboxMessage("telegram", "1", "chat", "hello", 1.0)
    conversation_id, _ = service.receive(message, route_id="telegram")

    drafts = service.propose_variants(conversation_id, message)

    assert [draft.body for draft in drafts] == ["formal reply", "friendly reply"]
    assert [draft["status"] for draft in store.conversation(conversation_id)["drafts"]] == ["proposed", "proposed"]


def test_context_aware_ai_receives_prior_messages(tmp_path) -> None:
    class ContextGenerator:
        def suggest(self, **_kwargs) -> str:
            return "unused"

        def suggest_with_context(self, **kwargs):
            self.history = kwargs["history"]
            return ["contextual reply"]

    generator = ContextGenerator()
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, generator, Outbox())
    first = InboxMessage("telegram", "1", "chat", "first", 1.0)
    second = InboxMessage("telegram", "2", "chat", "second", 2.0)
    conversation_id, _ = service.receive(first, route_id="telegram")
    service.receive(second, route_id="telegram")

    service.propose(conversation_id, second)

    assert [entry["body"] for entry in generator.history] == ["first", "second"]


def test_account_registry_exposes_capabilities_not_browser_session(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    store.register_account(
        account_id="vk-sales", provider="vk", display_name="Sales VK", can_read=True, can_reply=True,
        credential_ref="secret://userio/vk-sales", enabled=True,
    )

    assert store.accounts() == [{
        "id": "vk-sales", "provider": "vk", "display_name": "Sales VK", "capabilities": ["read", "reply"],
        "credential_ref": "secret://userio/vk-sales", "enabled": True,
    }]


def test_send_policy_blocks_approval_but_keeps_drafts(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    store.set_user_preference("send_enabled", "0")
    message = InboxMessage("telegram", "m-policy", "alice", "hello", 1.0)
    conversation_id, _ = service.receive(message, route_id="telegram")
    draft = service.create_manual_draft(conversation_id, body="reply")
    assert draft.status == "proposed"
    import pytest
    with pytest.raises(DeliveryUnavailableError):
        service.approve(draft.id)


def test_audio_transcript_is_promoted_to_canonical_body_and_persisted(tmp_path) -> None:
    payload = {
        "schema": "universal.inbox.message.v1",
        "source": "telegram",
        "message_id": "chat:77",
        "sender": "chat",
        "body": "[Telegram voice]",
        "attachments": [{
            "kind": "voice",
            "content_type": "audio/ogg",
            "filename": "voice-77.ogg",
            "provider_ref": "77",
            "transcript": "Это автоматическая транскрипция.",
            "transcription_status": "completed",
            "transcription_model": "whisper-1",
        }],
    }
    message = inbox_message_from_envelope(payload, received_at=2.0)
    assert message.body == "Это автоматическая транскрипция."
    assert message.attachments[0]["transcript"] == "Это автоматическая транскрипция."

    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    conversation_id, accepted = service.receive(message, route_id="telegram")
    assert accepted is True
    record = store.conversation(conversation_id)
    assert record["messages"][0]["body"] == "Это автоматическая транскрипция."
    attachment = record["messages"][0]["attachments"][0]
    assert attachment["transcript"] == "Это автоматическая транскрипция."
    assert attachment["transcription_status"] == "completed"
    assert attachment["transcription_model"] == "whisper-1"


def test_replayed_audio_enriches_old_placeholder_without_duplicate(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    placeholder = InboxMessage("telegram", "chat:88", "chat", "[Telegram voice]", 1.0)
    conversation_id, accepted = service.receive(placeholder, route_id="telegram")
    assert accepted is True

    enriched = inbox_message_from_envelope({
        "schema": "universal.inbox.message.v1",
        "source": "telegram",
        "message_id": "chat:88",
        "sender": "chat",
        "body": "[Telegram voice]",
        "attachments": [{
            "kind": "voice", "content_type": "audio/ogg", "filename": "voice-88.ogg",
            "transcript": "Старое голосовое теперь распознано.",
            "transcription_status": "completed", "transcription_model": "whisper-1",
        }],
    }, received_at=1.0)
    same_id, duplicate = service.receive(enriched, route_id="telegram")
    assert same_id == conversation_id
    assert duplicate is False
    record = store.conversation(conversation_id)
    assert len(record["messages"]) == 1
    assert record["messages"][0]["body"] == "Старое голосовое теперь распознано."
    assert record["messages"][0]["attachments"][0]["transcript"] == "Старое голосовое теперь распознано."


def test_long_audio_transcript_does_not_duplicate_ingress_prefix() -> None:
    transcript = "слово " * 3000
    prefix = transcript[:8000]
    message = inbox_message_from_envelope({
        "schema": "universal.inbox.message.v1",
        "source": "telegram",
        "message_id": "chat:long",
        "sender": "chat",
        "body": prefix,
        "attachments": [{
            "kind": "voice", "content_type": "audio/ogg", "filename": "long.ogg",
            "transcript": transcript, "transcription_status": "completed",
            "transcription_model": "whisper-1",
        }],
    }, received_at=3.0)
    assert message.body == transcript.strip()[:65_536]
    assert message.body.count(prefix[:100]) >= 1
    assert "[Транскрипция]" not in message.body


class _DraftNotifier:
    def __init__(self):
        self.calls = []

    def notify(self, **kwargs):
        self.calls.append(kwargs)
        return "notice-receipt"


def test_manual_draft_notifies_when_approval_is_required(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    notifier = _DraftNotifier()
    service = UserIOService(
        store, Generator(), Outbox(), draft_notifier=notifier, draft_notification_delay_seconds=0
    )
    message = InboxMessage("telegram", "m-notify", "alice", "hello", 1.0)
    conversation_id, _ = service.receive(message, route_id="telegram")

    draft = service.create_manual_draft(conversation_id, body="reply")

    assert draft.status == "proposed"
    assert len(notifier.calls) == 1
    assert notifier.calls[0]["drafts"][0].id == draft.id


def test_receive_and_plan_does_not_notify_before_auto_send(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    store.register_identity(source="vk", external_id="42", identity_id="person_auto", display_name="Auto")
    store.set_rule(identity_id="person_auto", source="vk", route_id="vip-vk", mode="auto_send")
    notifier = _DraftNotifier()
    service = UserIOService(
        store, Generator(), Outbox(), draft_notifier=notifier, draft_notification_delay_seconds=0
    )

    _, accepted, draft = service.receive_and_plan(
        InboxMessage("vk", "auto-1", "42", "urgent", 1.0), route_id="ordinary-vk"
    )

    assert accepted is True
    assert draft is not None and draft.status == "approved"
    assert notifier.calls == []

def test_explicit_ai_variants_notify_once_as_one_approval_event(tmp_path) -> None:
    class VariantGenerator:
        def suggest(self, **_kwargs): return "fallback"
        def suggest_variants(self, **_kwargs): return ["one", "two"]
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    notifier = _DraftNotifier()
    service = UserIOService(
        store, VariantGenerator(), Outbox(), draft_notifier=notifier,
        draft_notification_delay_seconds=0,
    )
    message = InboxMessage("telegram", "m-variants", "alice", "hello", 1.0)
    conversation_id, _ = service.receive(message, route_id="telegram")

    drafts = service.propose_for_approval(conversation_id, message, limit=3)

    assert [d.body for d in drafts] == ["one", "two"]
    assert len(notifier.calls) == 1
    assert [d.id for d in notifier.calls[0]["drafts"]] == [d.id for d in drafts]


def test_delayed_notification_skips_draft_already_approved(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    notifier = _DraftNotifier()
    service = UserIOService(store, Generator(), Outbox(), draft_notifier=notifier)
    message = InboxMessage("telegram", "m-fast-approve", "alice", "hello", 1.0)
    conversation_id, _ = service.receive(message, route_id="telegram")
    draft = service.propose(conversation_id, message)
    service.approve(draft.id)

    service._notify_drafts_if_still_proposed([draft.id])

    assert notifier.calls == []

def test_delayed_notification_skips_deleted_draft(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    notifier = _DraftNotifier()
    service = UserIOService(store, Generator(), Outbox(), draft_notifier=notifier)
    message = InboxMessage("telegram", "m-fast-delete", "alice", "hello", 1.0)
    conversation_id, _ = service.receive(message, route_id="telegram")
    draft = service.propose(conversation_id, message)
    store.delete_draft(draft.id)

    service._notify_drafts_if_still_proposed([draft.id])

    assert notifier.calls == []


def test_telegram_fallback_skips_draft_already_shown_by_browser(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    notifier = _DraftNotifier()
    service = UserIOService(store, Generator(), Outbox(), draft_notifier=notifier)
    message = InboxMessage("telegram", "browser-ack", "alice", "hello", 1.0)
    conversation_id, _ = service.receive(message, route_id="telegram")
    draft = service.propose(conversation_id, message)
    assert store.mark_drafts_browser_notified([draft.id]) == 1

    service._notify_drafts_if_still_proposed([draft.id])

    assert notifier.calls == []
