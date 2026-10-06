"""UserIO owns per-user bounded conversation context."""

from __future__ import annotations

import json

import pytest

from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore, _estimated_tokens


class Generator:
    def suggest(self, **_kwargs) -> str:
        return "draft"

    def triage_with_context(self, **_kwargs):
        return {
            "importance": 1.0,
            "urgency": "high",
            "confidence": 1.0,
            "reason_codes": ["context_test"],
            "reason_ru": "Проверка контекста.",
            "action_required": True,
            "action_summary": "Проверить.",
            "deadline_at": None,
            "suggested_replies": [],
            "safety_override": False,
            "policy_version": "test",
        }


class Outbox:
    def send_reply(self, **_kwargs) -> str:
        return "receipt"


def conversation(store: SQLiteUserIOStore, service: UserIOService, *, user_id=None) -> str:
    conversation_id = ""
    for index in range(1, 6):
        conversation_id, _ = service.receive(
            InboxMessage(
                "telegram", str(index), "contact", f"message-{index}-" + "я" * 300,
                float(index), conversation_kind="direct",
            ),
            route_id="telegram", user_id=user_id,
        )
    return conversation_id


def test_context_settings_are_per_user_durable_and_validated(tmp_path) -> None:
    database = tmp_path / "userio.sqlite3"
    store = SQLiteUserIOStore(database)
    owner = store.owner()
    other, _ = store.create_user("context-user", "password456")

    assert store.context_settings(user_id=owner.user_id) == {
        "message_count": 3, "token_budget": 1000,
    }
    assert store.set_context_settings(
        message_count=5, token_budget=2200, user_id=other.user_id,
    ) == {"message_count": 5, "token_budget": 2200}
    assert store.context_settings(user_id=owner.user_id)["message_count"] == 3

    for values in (
        {"message_count": True, "token_budget": 1000},
        {"message_count": 21, "token_budget": 1000},
        {"message_count": 3, "token_budget": 8001},
        {"message_count": -1, "token_budget": 1000},
    ):
        with pytest.raises(ValueError):
            store.set_context_settings(**values, user_id=owner.user_id)

    store.close()
    reopened = SQLiteUserIOStore(database)
    assert reopened.context_settings(user_id=other.user_id) == {
        "message_count": 5, "token_budget": 2200,
    }


def test_bounded_context_excludes_current_and_future_messages(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    conversation_id = conversation(store, service)
    store.set_context_settings(message_count=2, token_budget=500)

    context = store.bounded_conversation_context(
        conversation_id, current_message_id="4",
    )

    assert [item["message_id"] for item in context] == ["2", "3"]
    assert all(item["body"] for item in context)
    assert all(item["body_truncated"] is True for item in context)
    assert _estimated_tokens(json.dumps(
        context, ensure_ascii=False, separators=(",", ":"),
    )) <= 500


def test_claim_and_deep_triage_receive_the_same_userio_context(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    conversation_id = conversation(store, service)
    store.set_context_settings(message_count=2, token_budget=1000)
    events = store.workspace_events()["events"]
    target = events[-1]

    claim = store.claim_workspace_event(
        worker_id="hermes", after=int(events[-2]["seq"]),
    )
    service.triage_workspace_event(
        event_seq=int(target["seq"]), request_id="context-deep",
    )
    deep = service.deep_workspace_triage(
        event_seq=int(target["seq"]), request_id="context-deep", actor="telegram:42",
    )

    assert claim is not None
    assert claim["event"]["context_policy"] == {
        "message_count": 2, "token_budget": 1000,
    }
    assert [item["message_id"] for item in claim["event"]["recent_context"]] == ["3", "4"]
    assert claim["event"]["recent_context"] == deep["input"]["recent_context"]
    assert claim["event"]["conversation_id"] == conversation_id


def test_zero_context_setting_disables_history(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    conversation_id = conversation(store, service)
    store.set_context_settings(message_count=0, token_budget=1000)
    assert store.bounded_conversation_context(
        conversation_id, current_message_id="5",
    ) == []
