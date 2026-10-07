"""UserIO owns per-user bounded conversation context."""

from __future__ import annotations

import json
import time

import pytest

from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore, _estimated_tokens


def _seed_cache(store, conversation_id):
    work = store.conversation_summary_work(conversation_id)
    last = work["new_messages"][-1]
    assert store.save_conversation_summary(
        conversation_id, summary="old facts " * 100,
        expected_through_message_rowid=work["expected_through_message_rowid"],
        through_message_rowid=last["message_rowid"], through_message_id=last["message_id"],
        summarized_message_count=work["summarized_message_count"] + len(work["new_messages"]),
    )
    return work


def test_summary_pages_cover_oldest_rows_without_gaps(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "cache.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    for index in range(250):
        cid, _ = service.receive(InboxMessage("telegram", str(index), "contact", str(index),
                                             float(index), conversation_kind="direct"), route_id="telegram")
    pages = [_seed_cache(store, cid) for _ in range(3)]
    assert [len(page["new_messages"]) for page in pages] == [100, 100, 50]
    assert [row["message_id"] for page in pages for row in page["new_messages"]] == list(map(str, range(250)))
    assert store.conversation_summary_work(cid)["new_messages"] == []


def test_edits_and_changed_attachments_invalidate_but_replays_do_not(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "cache.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    cid = conversation(store, service)
    _seed_cache(store, cid)
    service.receive(InboxMessage("telegram", "1", "contact", "changed fact", 1.,
                                 conversation_kind="direct"), route_id="telegram")
    assert store.conversation_summary(cid) is None
    assert store.conversation_summary_work(cid)["new_messages"][0]["body"] == "changed fact"
    _seed_cache(store, cid)
    attachment = {"idx": 0, "kind": "voice", "transcript": "first transcription"}
    store.upsert_attachment(source="telegram", message_id="1", attachment=attachment)
    assert store.conversation_summary(cid) is None
    _seed_cache(store, cid)
    store.upsert_attachment(source="telegram", message_id="1", attachment=attachment)
    assert store.conversation_summary(cid) is not None
    store.upsert_attachment(source="telegram", message_id="1", attachment=attachment | {"transcript": "corrected"})
    assert store.conversation_summary(cid) is None


def test_merged_gmail_exact_anchors_and_full_configured_window(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "cache.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    store.set_context_settings(cache_channels={"gmail": True}, max_message_count=500)
    for index in range(301):
        source = "gmail:a" if index < 300 else "gmail:b"
        mid = "shared" if index in (0, 300) else str(index)
        cid, _ = service.receive(InboxMessage(source, mid, "sender@example.org", str(index),
                                             float(index), conversation_kind="direct"), route_id=source)
    assert store.conversation_summary_work(cid, through_message_id="shared") is None
    first = store.conversation_summary_work(cid, through_message_id="shared", through_message_source="gmail:a")
    assert len(first["new_messages"]) == 1
    history = store.bounded_conversation_context(cid, current_message_id="shared",
        current_message_source="gmail:b", message_limit=500, token_limit=48_000)
    assert len(history) == 300
    assert history[0]["source"] == "gmail:a"
    assert store.bounded_conversation_context(cid, before_message_id=history[100]["anchor_id"],
        message_limit=500, token_limit=48_000) == history[:100]


def test_budget_reduction_maintenance_and_startup_retention(tmp_path):
    database = tmp_path / "cache.sqlite3"
    store = SQLiteUserIOStore(database)
    service = UserIOService(store, Generator(), Outbox())
    cid = conversation(store, service)
    _seed_cache(store, cid)
    store.set_context_settings(summary_token_budget=64, summary_retention_days=1)
    cached = store._connection.execute("SELECT summary FROM conversation_summaries").fetchone()[0]
    assert _estimated_tokens(cached) <= 64
    with store._connection:
        store._connection.execute("UPDATE conversation_summaries SET updated_at=?", (time.time() - 172800,))
    reopened = SQLiteUserIOStore(database)
    assert reopened._connection.execute("SELECT COUNT(*) FROM conversation_summaries").fetchone()[0] == 0
    assert reopened.maintain_conversation_summaries() == 0


def test_first_page_edit_during_generation_cannot_persist_stale_summary(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "cache.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    cid = conversation(store, service)
    work = store.conversation_summary_work(cid)
    service.receive(InboxMessage("telegram", "1", "contact", "edited during generation", 1.,
                                 conversation_kind="direct"), route_id="telegram")
    last = work["new_messages"][-1]
    assert not store.save_conversation_summary(
        cid, summary="stale generated facts", expected_through_message_rowid=0,
        through_message_rowid=last["message_rowid"], through_message_id=last["message_id"],
        summarized_message_count=5, expected_messages=work["new_messages"],
    )
    assert store.conversation_summary(cid) is None


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


def assert_default_cache_policy(settings: dict[str, object]) -> None:
    assert settings["message_count"] == 3
    assert settings["max_message_count"] == 150
    assert settings["token_budget"] == 1000
    assert settings["max_token_budget"] == 48_000
    assert settings["summary_token_budget"] == 1200
    assert settings["summary_retention_days"] == 90
    assert settings["cache_channels"]["telegram"] is True
    assert settings["cache_channels"]["gmail"] is False
    assert settings["cache_conversation_kinds"] == {
        "direct": True, "group": False, "channel": False, "unknown": False,
    }


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

    assert_default_cache_policy(store.context_settings(user_id=owner.user_id))
    updated = store.set_context_settings(
        message_count=5, token_budget=2200, user_id=other.user_id,
    )
    assert updated["message_count"] == 5
    assert updated["token_budget"] == 2200
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
    reopened_settings = reopened.context_settings(user_id=other.user_id)
    assert reopened_settings["message_count"] == 5
    assert reopened_settings["token_budget"] == 2200


def test_context_maxima_and_cache_maps_are_validated(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    settings = store.set_context_settings(
        message_count=4, max_message_count=40,
        token_budget=1500, max_token_budget=12_000,
        summary_token_budget=900, summary_retention_days=30,
        cache_channels={"telegram": True, "gmail:test": True, "email": False},
        cache_conversation_kinds={"direct": True, "group": True},
    )
    assert settings["max_message_count"] == 40
    assert settings["max_token_budget"] == 12_000
    assert settings["summary_token_budget"] == 900
    assert settings["summary_retention_days"] == 30
    assert settings["cache_channels"]["gmail"] is False
    assert settings["cache_conversation_kinds"]["group"] is True

    for values in (
        {"message_count": 5, "max_message_count": 4},
        {"token_budget": 2000, "max_token_budget": 1999},
        {"summary_token_budget": 63},
        {"max_token_budget": 63},
        {"max_token_budget": 100_001},
        {"cache_channels": {"telegram": 1}},
    ):
        with pytest.raises(ValueError):
            store.set_context_settings(**values)

    store.set_user_preference("context_token_budget", "0")
    store.set_user_preference("context_max_token_budget", "1")
    assert store.context_settings()["max_token_budget"] == 64


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
    assert claim["event"]["context_policy"]["message_count"] == 2
    assert claim["event"]["context_policy"]["token_budget"] == 1000
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


class CacheGenerator(Generator):
    def __init__(self) -> None:
        self.summary_calls: list[dict[str, object]] = []
        self.triage_calls: list[dict[str, object]] = []

    def summarize_conversation(self, **kwargs) -> str:
        self.summary_calls.append(kwargs)
        ids = ",".join(str(item["message_id"]) for item in kwargs["new_messages"])
        previous = str(kwargs["previous_summary"])
        return f"{previous}|{ids}" if previous else f"summary:{ids}"

    def triage_with_context(self, **kwargs):
        self.triage_calls.append(kwargs)
        return super().triage_with_context(**kwargs)


def test_telegram_direct_summary_is_incremental_and_injected_immediately(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    generator = CacheGenerator()
    service = UserIOService(store, generator, Outbox())

    conversation_id, _ = service.receive(
        InboxMessage(
            "telegram", "1", "anna", "first", 1.0,
            conversation_kind="direct",
        ),
        route_id="telegram",
    )
    first_event = store.workspace_events()["events"][-1]
    service.triage_workspace_event(
        event_seq=int(first_event["seq"]), request_id="summary-first",
    )
    first_cache = store.conversation_summary(conversation_id)
    assert first_cache is not None
    assert first_cache["summary"] == "summary:1"
    assert first_cache["summarized_message_count"] == 1
    assert generator.triage_calls[-1]["cached_summary"] == "summary:1"

    service.receive(
        InboxMessage(
            "telegram", "2", "anna", "second", 2.0,
            conversation_kind="direct",
        ),
        route_id="telegram",
    )
    second_event = store.workspace_events()["events"][-1]
    service.triage_workspace_event(
        event_seq=int(second_event["seq"]), request_id="summary-second",
    )

    assert [
        [item["message_id"] for item in call["new_messages"]]
        for call in generator.summary_calls
    ] == [["1"], ["2"]]
    assert generator.summary_calls[-1]["previous_summary"] == "summary:1"
    assert generator.triage_calls[-1]["cached_summary"] == "summary:1|2"
    assert generator.triage_calls[-1]["initial_context_messages"] == 3
    assert generator.triage_calls[-1]["max_context_messages"] == 150
    assert generator.triage_calls[-1]["max_context_token_budget"] == 48_000
    second_cache = store.conversation_summary(conversation_id)
    assert second_cache is not None
    assert second_cache["summary"] == "summary:1|2"
    assert second_cache["summarized_message_count"] == 2


def test_triage_and_summary_receive_ephemeral_bounded_image_bytes(tmp_path) -> None:
    class ImageFile:
        content_type = "image/png"
        data = b"\x89PNG\r\n\x1a\n" + b"pixels"

    calls: list[dict[str, object]] = []

    def load_image(**kwargs):
        calls.append(kwargs)
        return ImageFile()

    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    generator = CacheGenerator()
    service = UserIOService(
        store, generator, Outbox(), ai_image_loader=load_image,
    )
    _conversation_id, _ = service.receive(
        InboxMessage(
            "telegram", "photo-1", "anna", "Смотри", 1.0,
            conversation_kind="direct",
            attachments=({
                "kind": "image", "content_type": "image/png",
                "size": len(ImageFile.data),
                "src": "file:///private/secret.png?token=sentinel",
            },),
        ),
        route_id="telegram",
    )
    event = store.workspace_events()["events"][-1]

    service.triage_workspace_event(
        event_seq=int(event["seq"]), request_id="image-context",
    )

    summary_attachment = generator.summary_calls[-1]["new_messages"][0]["attachments"][0]
    latest_attachment = generator.triage_calls[-1]["latest_message"].attachments[0]
    assert summary_attachment["image_bytes"] == ImageFile.data
    assert latest_attachment["image_bytes"] == ImageFile.data
    assert "src" not in summary_attachment and "src" not in latest_attachment
    assert calls and calls[0]["source"] == "telegram"


def test_shared_vision_attempt_budget_spans_multiple_context_pages(tmp_path) -> None:
    class ImageFile:
        content_type = "image/png"
        data = b"\x89PNG\r\n\x1a\n" + b"pixels"

    calls: list[dict[str, object]] = []

    def load_image(**kwargs):
        calls.append(kwargs)
        return ImageFile()

    service = UserIOService(
        SQLiteUserIOStore(tmp_path / "userio.sqlite3"), CacheGenerator(), Outbox(),
        ai_image_loader=load_image,
    )
    entry = {
        "source": "telegram", "message_id": "photo",
        "attachments": [{"kind": "image", "content_type": "image/png"}] * 3,
    }
    budget = [4]
    service._hydrate_ai_images([entry], user_id="owner", image_budget=budget)
    service._hydrate_ai_images([entry], user_id="owner", image_budget=budget)

    assert len(calls) == 4
    assert budget == [0]


def test_triage_hydrates_latest_image_before_history(tmp_path) -> None:
    class ImageFile:
        content_type = "image/png"
        data = b"\x89PNG\r\n\x1a\n" + b"pixels"

    calls: list[str] = []

    def load_image(**kwargs):
        calls.append(str(kwargs["message_id"]))
        return ImageFile()

    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    store.set_context_settings(cache_channels={"telegram": False})
    service = UserIOService(
        store, CacheGenerator(), Outbox(), ai_image_loader=load_image,
    )
    for index in range(1, 6):
        service.receive(
            InboxMessage(
                "telegram", f"42:{index}", "anna", f"photo {index}", float(index),
                conversation_kind="direct", peer_id="42",
                attachments=({"kind": "image", "content_type": "image/png"},),
            ),
            route_id="telegram",
        )
    event = store.workspace_events()["events"][-1]

    service.triage_workspace_event(
        event_seq=int(event["seq"]), request_id="latest-image-first",
    )

    assert calls[0] == "42:5"
    assert len(calls) == 4


def test_email_summary_is_off_by_default_and_disabling_policy_purges_cache(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    generator = CacheGenerator()
    service = UserIOService(store, generator, Outbox())

    gmail_id, _ = service.receive(
        InboxMessage("gmail", "g1", "mail@example.test", "mail", 1.0),
        route_id="gmail",
    )
    gmail_event = store.workspace_events()["events"][-1]
    service.triage_workspace_event(
        event_seq=int(gmail_event["seq"]), request_id="gmail-summary-off",
    )
    assert store.conversation_summary(gmail_id) is None
    assert generator.summary_calls == []

    telegram_id, _ = service.receive(
        InboxMessage(
            "telegram", "t1", "anna", "telegram", 2.0,
            conversation_kind="direct",
        ),
        route_id="telegram",
    )
    telegram_event = store.workspace_events()["events"][-1]
    service.triage_workspace_event(
        event_seq=int(telegram_event["seq"]), request_id="telegram-summary-on",
    )
    assert store.conversation_summary(telegram_id) is not None

    store.set_context_settings(cache_channels={"telegram": False, "gmail": False})
    assert store.conversation_summary(telegram_id) is None


def test_summary_retention_is_enforced_on_read(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    generator = CacheGenerator()
    service = UserIOService(store, generator, Outbox())
    conversation_id, _ = service.receive(
        InboxMessage(
            "telegram", "old-1", "anna", "old", 1.0,
            conversation_kind="direct",
        ),
        route_id="telegram",
    )
    event = store.workspace_events()["events"][-1]
    service.triage_workspace_event(
        event_seq=int(event["seq"]), request_id="summary-retention",
    )
    assert store.conversation_summary(conversation_id) is not None
    store.set_context_settings(summary_retention_days=1)
    with store._lock, store._connection:
        store._connection.execute(
            "UPDATE conversation_summaries SET updated_at=0 WHERE conversation_id=?",
            (conversation_id,),
        )
    assert store.conversation_summary(conversation_id) is None
