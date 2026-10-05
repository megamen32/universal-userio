from __future__ import annotations

from universal_userio.adapters import inbox_message_from_envelope
from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def generate(self, *_args, **_kwargs):
        return []


class Outbox:
    def send_reply(self, **_kwargs):
        return "unused"


def test_reconciliation_is_archived_but_never_claimable(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    envelope = {
        "schema": "universal.inbox.message.v1", "source": "telegram",
        "message_id": "telegram:11|51:10", "peer_id": "51",
        "conversation_kind": "direct", "sender": "Alice", "body": "old message",
        "reconciliation": True,
    }
    message = inbox_message_from_envelope(envelope, received_at=1.0)
    assert message.reconciliation is True
    conversation_id, accepted = service.receive(message, route_id="telegram", account_ref="telegram:11")
    assert accepted is True
    assert store.message("telegram:11|51:10", source="telegram") is not None
    assert store.workspace_events()["events"] == []
    assert store.claim_workspace_event(worker_id="worker") is None
    store.set_workspace_chat_rule(conversation_id=conversation_id, action="allow")
    assert store.claim_workspace_event(worker_id="worker") is None
    service.receive(InboxMessage("telegram", "51:11", "Alice", "live", 2.0,
                                 conversation_kind="direct", peer_id="51"),
                    route_id="telegram", account_ref="telegram:11")
    assert store.claim_workspace_event(worker_id="worker")["event"]["message_id"] == "telegram:11|51:11"


def test_legacy_telegram_id_deduplicates_only_within_same_account_and_chat(tmp_path) -> None:
    database = tmp_path / "userio.sqlite3"
    store = SQLiteUserIOStore(database)
    service = UserIOService(store, Generator(), Outbox())
    legacy_id, _ = service.receive(
        InboxMessage("telegram", "51:9", "Alice", "already stored", 1.0,
                     conversation_kind="direct", peer_id="51"), route_id="telegram")
    store.set_conversation_account(legacy_id, "telegram:11")
    with store._lock, store._connection:
        store._connection.execute("DELETE FROM settings WHERE key='workspace_conversation_kind_backfilled_v1'")
    store.close()
    store = SQLiteUserIOStore(database)
    service = UserIOService(store, Generator(), Outbox())
    original_head = store.workspace_events()["head"]
    conversation_id, accepted = service.receive(
        InboxMessage("telegram", "51:9", "Renamed Alice", "already stored", 2.0,
                     conversation_kind="direct", peer_id="51", reconciliation=True),
        route_id="telegram", account_ref="telegram:11")
    assert conversation_id == legacy_id
    assert accepted is False
    assert store.workspace_events()["head"] == original_head
    assert store.message("telegram:11|51:9", source="telegram") is None

    other_id, other_accepted = service.receive(
        InboxMessage("telegram", "51:9", "Alice", "other account", 3.0,
                     conversation_kind="direct", peer_id="51", reconciliation=True),
        route_id="telegram", account_ref="telegram:22")
    assert other_id != legacy_id and other_accepted is True
    assert store.message("telegram:22|51:9", source="telegram")["body"] == "other account"


def test_live_arrival_racing_reconciliation_is_processed_once(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    old = InboxMessage("telegram", "51:1", "Alice", "hello", 1.0,
                       conversation_kind="direct", peer_id="51", reconciliation=True)
    conversation_id, accepted = service.receive(old, route_id="telegram", account_ref="telegram:11")
    assert accepted
    assert store.claim_workspace_event(worker_id="worker") is None
    live = InboxMessage("telegram", "51:1", "Alice", "hello", 2.0,
                        conversation_kind="direct", peer_id="51")
    assert service.receive(live, route_id="telegram", account_ref="telegram:11") == (conversation_id, False)
    claimed = store.claim_workspace_event(worker_id="worker")
    assert claimed is not None
    assert claimed["event"]["message_id"] == "telegram:11|51:1"
    assert store.workspace_events()["head"] == claimed["event"]["seq"]
