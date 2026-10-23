"""Bounded Telegram history freshness and per-window request coalescing."""
from __future__ import annotations

import io
import json
import threading
import time

import pytest

import universal_userio.adapters as adapters_module
from universal_userio.adapters import TelegramChannelAdapter
from universal_userio.contracts import InboxMessage
from universal_userio.mcp_surface import UserIOMcpSurface
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore
from universal_userio.telegram_history_refresh import (
    DEFAULT_HISTORY_REFRESH_WAIT_SECONDS,
    HistoryRefreshCoordinator,
)


ACCOUNT = "telegram:12345"
PEER = "67890"


@pytest.fixture
def history_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("USERIO_API_TOKEN", "fixture-only")
    store = SQLiteUserIOStore(tmp_path / "history.sqlite3")
    service = UserIOService(store, object(), object())
    store.register_account(
        account_id=ACCOUNT, provider="telegram", display_name="Owner",
        can_read=True, can_reply=True, credential_ref="fixture", enabled=True,
    )

    def ingest(message_id: int) -> str:
        return service.receive(InboxMessage(
            "telegram", f"{ACCOUNT}|{PEER}:{message_id}", "Peer", f"body-{message_id}",
            float(message_id), peer_id=PEER, conversation_kind="direct",
            direction="incoming", reconciliation=True,
        ), route_id="telegram", account_ref=ACCOUNT)[0]

    coordinator = HistoryRefreshCoordinator(wait_seconds=0.04)
    monkeypatch.setattr(adapters_module, "TELEGRAM_HISTORY_REFRESH", coordinator)
    yield store, service, ingest, coordinator
    coordinator.shutdown()
    store.close()


def test_default_refresh_returns_truthful_cached_status_at_two_second_budget():
    assert DEFAULT_HISTORY_REFRESH_WAIT_SECONDS == 2.0
    coordinator = HistoryRefreshCoordinator()
    release = threading.Event()
    started = threading.Event()

    def blocked_refresh():
        started.set()
        release.wait(timeout=3)
        return {"status": "refreshed", "fresh": True}

    try:
        began = time.monotonic()
        result = coordinator.refresh(("user", "account", "peer", None, 50), blocked_refresh)
        elapsed = time.monotonic() - began
        assert started.is_set()
        assert 1.8 <= elapsed < 2.5
        assert result == {
            "status": "refreshing", "fresh": False, "stale": True, "refreshing": True,
        }
    finally:
        release.set()
        coordinator.shutdown()


def test_completed_refresh_exception_returns_failed_state_and_releases_singleflight():
    coordinator = HistoryRefreshCoordinator(wait_seconds=0.2)
    calls = []

    def fail():
        calls.append("failed")
        raise OSError("connector read failed")

    try:
        result = coordinator.refresh(("user", "account", "peer", None, 50), fail)
        assert result == {
            "status": "failed", "fresh": False, "stale": True,
            "refreshing": False, "error": "OSError",
        }

        recovered = coordinator.refresh(
            ("user", "account", "peer", None, 50),
            lambda: calls.append("recovered") or {"status": "refreshed", "fresh": True},
        )
        assert recovered["status"] == "refreshed"
        assert recovered["fresh"] is True
        assert calls == ["failed", "recovered"]
    finally:
        coordinator.shutdown()


def test_reconciled_native_telegram_edit_replaces_body_and_invalidates_summary(history_fixture):
    store, service, _ingest, _coordinator = history_fixture
    other_account = "telegram:54321"
    store.register_account(
        account_id=other_account, provider="telegram", display_name="Other owner",
        can_read=True, can_reply=True, credential_ref="other-fixture", enabled=True,
    )

    def receive(account: str, body: str, *, reconciliation: bool, edited_at: float = 0.0,
                attachments=()):
        return service.receive(InboxMessage(
            "telegram", f"{account}|{PEER}:77", "Peer", body, 77.0,
            peer_id=PEER, conversation_kind="direct", direction="incoming",
            reconciliation=reconciliation, edited_at=edited_at, attachments=tuple(attachments),
        ), route_id="telegram", account_ref=account)[0]

    conversation_id = receive(ACCOUNT, "old native text", reconciliation=False)
    other_conversation_id = receive(other_account, "other account text", reconciliation=False)

    work = store.conversation_summary_work(conversation_id)
    last = work["new_messages"][-1]
    assert store.save_conversation_summary(
        conversation_id, summary="cached facts", expected_through_message_rowid=0,
        through_message_rowid=last["message_rowid"], through_message_id=last["message_id"],
        summarized_message_count=len(work["new_messages"]), expected_messages=work["new_messages"],
    )
    event_before = store._connection.execute(
        "SELECT eligible,reconciled FROM workspace_events WHERE user_id=? AND source='telegram' "
        "AND message_id=? AND conversation_id=?",
        (store.default_user_id, f"{ACCOUNT}|{PEER}:77", conversation_id),
    ).fetchone()
    assert event_before is not None

    duplicate = service.receive(InboxMessage(
        "telegram", f"{ACCOUNT}|{PEER}:77", "Peer", "[edited native text]", 77.0,
        peer_id=PEER, conversation_kind="direct", direction="incoming",
        reconciliation=True, edited_at=88.0,
        attachments=({"kind": "webpage"}, {"kind": "telegram_routing"}),
    ), route_id="telegram", account_ref=ACCOUNT)[1]

    assert duplicate is False
    assert len(store.conversation(conversation_id)["messages"]) == 1
    assert store.conversation(conversation_id)["messages"][0]["body"] == "[edited native text]"
    assert store.conversation(conversation_id)["messages"][0]["edited_at"] == 88.0
    assert store.conversation_summary(conversation_id) is None
    event_after = store._connection.execute(
        "SELECT eligible,reconciled FROM workspace_events WHERE user_id=? AND source='telegram' "
        "AND message_id=? AND conversation_id=?",
        (store.default_user_id, f"{ACCOUNT}|{PEER}:77", conversation_id),
    ).fetchone()
    assert tuple(event_after) == tuple(event_before)
    assert store.conversation(other_conversation_id)["messages"][0]["body"] == "other account text"

    receive(ACCOUNT, "older edit", reconciliation=True, edited_at=87.0)
    message = store.conversation(conversation_id)["messages"][0]
    assert message["body"] == "[edited native text]"
    assert message["edited_at"] == 88.0


def test_reconciled_telegram_media_refresh_preserves_existing_transcript(history_fixture):
    store, service, _ingest, _coordinator = history_fixture
    message_id = f"{ACCOUNT}|{PEER}:88"
    original = InboxMessage(
        "telegram", message_id, "Peer", "recognized words", 88.0,
        peer_id=PEER, conversation_kind="direct", direction="incoming", reconciliation=True,
        attachments=({"kind": "voice", "provider_ref": "88", "transcript": "recognized words",
                      "transcription_status": "completed"},),
    )
    conversation_id, _ = service.receive(original, route_id="telegram", account_ref=ACCOUNT)
    refreshed = InboxMessage(
        "telegram", message_id, "Peer", "[Telegram voice]", 88.0,
        peer_id=PEER, conversation_kind="direct", direction="incoming", reconciliation=True,
        edited_at=90.0,
        attachments=({"kind": "voice", "provider_ref": "88", "transcription_status": "not_requested"},),
    )

    service.receive(refreshed, route_id="telegram", account_ref=ACCOUNT)

    assert store.conversation(conversation_id)["messages"][0]["body"] == "recognized words"


def test_read_returns_fresh_history_when_refresh_finishes_inside_budget(history_fixture):
    store, service, ingest, coordinator = history_fixture
    chat_id = ingest(10)
    requests = []

    def runner(request, timeout):
        assert timeout == 25
        requests.append(json.loads(request.data))
        ingest(11)
        return io.BytesIO(json.dumps({
            "account_id": ACCOUNT, "peer_id": PEER, "mirrored": 1,
            "oldest_id": 10, "has_more": False,
        }).encode())

    adapter = TelegramChannelAdapter(
        store, service, store.default_user_id,
        bridge_url="http://127.0.0.1:1234", runner=runner,
    )
    started = time.monotonic()
    result = adapter.read(chat_id=chat_id)
    elapsed = time.monotonic() - started

    assert elapsed < DEFAULT_HISTORY_REFRESH_WAIT_SECONDS
    assert result["sync"]["status"] == "refreshed"
    assert result["sync"]["fresh"] is True
    assert result["sync"]["stale"] is False
    assert result["sync"]["refreshing"] is False
    assert [message["body"] for message in result["chat"]["messages"]] == ["body-10", "body-11"]
    assert requests == [{"account_id": ACCOUNT, "peer_id": PEER, "limit": 50, "scan": True}]


def test_mcp_returns_cached_history_after_budget_and_joins_background_refresh(history_fixture, monkeypatch):
    store, service, ingest, coordinator = history_fixture
    chat_id = ingest(10)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    requests = []

    def runner(request, timeout):
        assert timeout == 25
        requests.append(json.loads(request.data))
        started.set()
        try:
            assert release.wait(timeout=2)
            ingest(11)
            return io.BytesIO(json.dumps({
                "account_id": ACCOUNT, "peer_id": PEER, "mirrored": 1,
                "oldest_id": 10, "has_more": False,
            }).encode())
        finally:
            finished.set()

    monkeypatch.setattr(
        adapters_module.UnifiedChannels,
        "adapter",
        lambda _channels, _source=None: TelegramChannelAdapter(
            store, service, store.default_user_id,
            bridge_url="http://127.0.0.1:1234", runner=runner,
        ),
    )
    surface = UserIOMcpSurface(store, service)
    try:
        started_at = time.monotonic()
        first = surface.dispatch("userio.channels.read", {
            "channel": "telegram", "chat_id": chat_id,
        })
        first_elapsed = time.monotonic() - started_at
        assert started.wait(timeout=1)
        second = surface.dispatch("userio.channels.read", {
            "channel": "telegram", "chat_id": chat_id,
        })
        assert first_elapsed < 0.25
        assert first["ok"] is True and second["ok"] is True
        assert first["sync"] == second["sync"] == {
            "status": "refreshing", "fresh": False, "stale": True, "refreshing": True,
        }
        assert [message["body"] for message in first["chat"]["messages"]] == ["body-10"]
        assert [message["body"] for message in second["chat"]["messages"]] == ["body-10"]
        assert len(requests) == 1

        release.set()
        assert finished.wait(timeout=1)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            messages = store.conversation(chat_id)["messages"]
            if any(message["body"] == "body-11" for message in messages):
                break
            time.sleep(0.005)
        assert [message["body"] for message in store.conversation(chat_id)["messages"]] == ["body-10", "body-11"]
        assert len(requests) == 1
    finally:
        release.set()
