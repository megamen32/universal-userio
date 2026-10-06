"""Provider-side message edits must rewrite the mirror in place.

Covers the reported Telegram bot status flow: the bot posts "Начинаю
проверку..." and later edits the same message to "Найдено в топе: N". The
mirror must show the edited text, carry the edit timestamp, and must not
create a second message or a second workspace (agent/triage) event.
"""
from __future__ import annotations

import pytest

from universal_userio.adapters import inbox_message_from_envelope
from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def suggest(self, *, conversation_id: str, latest_message: InboxMessage) -> str:
        return "draft"


class Outbox:
    def send_reply(self, *, route_id: str, conversation_id: str, draft_id: str, body: str) -> str:
        return "delivery-receipt-1"


def _service(tmp_path) -> UserIOService:
    return UserIOService(SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Generator(), Outbox())


def test_edited_message_rewrites_mirror_body_in_place(tmp_path) -> None:
    service = _service(tmp_path)
    original = InboxMessage("telegram", "8810909089:1981200", "bot", "Начинаю проверку...", 1.0)
    conversation_id, accepted = service.receive(original, route_id="telegram")
    assert accepted is True

    edited = InboxMessage(
        "telegram", "8810909089:1981200", "bot", "Найдено в топе: 3", 2.0, edited_at=1_759_000_000.0
    )
    same_id, duplicate = service.receive(edited, route_id="telegram")
    assert same_id == conversation_id
    assert duplicate is False

    record = service._store.conversation(conversation_id)
    assert len(record["messages"]) == 1
    message = record["messages"][0]
    assert message["body"] == "Найдено в топе: 3"
    assert message["edited_at"] == pytest.approx(1_759_000_000.0)


def test_edited_message_does_not_create_second_workspace_event(tmp_path) -> None:
    service = _service(tmp_path)
    service.receive(
        InboxMessage("matrix", "hist-1", "owner", "Начинаю проверку...", 1.0,
                     conversation_kind="direct"),
        route_id="matrix",
    )
    service.receive(
        InboxMessage("matrix", "hist-1", "owner", "Найдено в топе: 1", 2.0,
                     conversation_kind="direct"),
        route_id="matrix",
    )
    events = service._store.workspace_events(user_id=service._store.default_user_id)
    assert [event["message_id"] for event in events["events"]] == ["hist-1"]
    assert events["events"][0]["body"] == "Найдено в топе: 1"


def test_unchanged_replay_does_not_mark_message_edited(tmp_path) -> None:
    service = _service(tmp_path)
    message = InboxMessage("telegram", "chat:2", "bot", "Тот же текст", 1.0)
    conversation_id, _ = service.receive(message, route_id="telegram")
    _, duplicate = service.receive(
        InboxMessage("telegram", "chat:2", "bot", "Тот же текст", 2.0), route_id="telegram"
    )
    assert duplicate is False
    record = service._store.conversation(conversation_id)
    assert record["messages"][0]["edited_at"] is None


def test_placeholder_enrichment_is_not_reported_as_edit(tmp_path) -> None:
    service = _service(tmp_path)
    conversation_id, _ = service.receive(
        InboxMessage("telegram", "chat:3", "chat", "[Telegram voice]", 1.0), route_id="telegram"
    )
    service.receive(
        InboxMessage("telegram", "chat:3", "chat", "Распознанный текст", 2.0), route_id="telegram"
    )
    record = service._store.conversation(conversation_id)
    assert record["messages"][0]["body"] == "Распознанный текст"
    assert record["messages"][0]["edited_at"] is None


def test_edit_never_replaces_real_text_with_placeholder(tmp_path) -> None:
    service = _service(tmp_path)
    conversation_id, _ = service.receive(
        InboxMessage("telegram", "chat:4", "chat", "Настоящий текст", 1.0), route_id="telegram"
    )
    service.receive(
        InboxMessage("telegram", "chat:4", "chat", "[image]", 2.0), route_id="telegram"
    )
    record = service._store.conversation(conversation_id)
    assert record["messages"][0]["body"] == "Настоящий текст"
    assert record["messages"][0]["edited_at"] is None


def test_envelope_carries_provider_edit_timestamp() -> None:
    message = inbox_message_from_envelope(
        {
            "schema": "universal.inbox.message.v1",
            "source": "telegram",
            "message_id": "8810909089:1981200",
            "sender": "bot",
            "body": "Найдено в топе: 7",
            "edited_at": 1_759_000_500,
        },
        received_at=1_759_000_501.0,
    )
    assert message.edited_at == 1_759_000_500.0


def test_envelope_without_edit_timestamp_defaults_to_zero() -> None:
    message = inbox_message_from_envelope(
        {
            "schema": "universal.inbox.message.v1",
            "source": "telegram",
            "message_id": "chat:9",
            "sender": "bot",
            "body": "обычное сообщение",
        },
        received_at=1.0,
    )
    assert message.edited_at == 0.0


@pytest.mark.parametrize("bad", ["abc", True, -1.0])
def test_envelope_rejects_invalid_edit_timestamp(bad) -> None:
    with pytest.raises(ValueError):
        inbox_message_from_envelope(
            {
                "schema": "universal.inbox.message.v1",
                "source": "telegram",
                "message_id": "chat:10",
                "sender": "bot",
                "body": "текст",
                "edited_at": bad,
            },
            received_at=1.0,
        )
