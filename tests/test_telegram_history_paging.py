"""Bounded Telegram history pagination and honest completeness status."""
import io
import json

import pytest

from universal_userio.adapters import TelegramChannelAdapter
from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


ACCOUNT = "telegram:12345"
PEER = "67890"


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("USERIO_API_TOKEN", "fixture-only")
    store = SQLiteUserIOStore(tmp_path / "history.sqlite3")
    service = UserIOService(store, object(), object())
    store.register_account(account_id=ACCOUNT, provider="telegram", display_name="Owner",
                           can_read=True, can_reply=True, credential_ref="fixture", enabled=True)

    def ingest(mid, direction="incoming"):
        return service.receive(InboxMessage(
            "telegram", f"{ACCOUNT}|{PEER}:{mid}", "Peer", f"body-{mid}",
            float(mid), peer_id=PEER, conversation_kind="direct",
            direction=direction, reconciliation=True,
        ), route_id="telegram", account_ref=ACCOUNT)[0]
    yield store, service, ingest
    store.close()


def test_scan_only_claims_complete_after_sequential_last_page(fixture):
    store, _, ingest = fixture
    cid = ingest(10)
    for mid in (15, 20, 30, 40):
        ingest(mid)
    user = store.default_user_id
    assert store.conversation(cid)["history"]["status"] == "unverified"
    kwargs = dict(account_ref=ACCOUNT, peer_id=PEER, user_id=user)
    first = store.telegram_mirror_state(
        **kwargs, scan_before_id=0, scan_oldest_id=30,
        scan_has_more=True, scan_fetched=2)
    assert first["history"]["scan_advanced"] is True
    assert store.conversation(cid)["history"]["status"] == "partial"
    ignored = store.telegram_mirror_state(
        **kwargs, scan_before_id=999, scan_oldest_id=20,
        scan_has_more=False, scan_fetched=2)
    assert ignored["history"]["scan_advanced"] is False
    assert store.conversation(cid)["history"]["status"] == "partial"
    complete = store.telegram_mirror_state(
        **kwargs, scan_before_id=30, scan_oldest_id=10,
        scan_has_more=False, scan_fetched=3)
    assert complete["history"]["complete"] is True
    assert store.conversation(cid)["history"] == {
        "status": "complete", "oldest_id": 10, "fetched_count": 5}
    again = store.telegram_mirror_state(
        **kwargs, scan_before_id=0, scan_oldest_id=20,
        scan_has_more=True, scan_fetched=2)
    assert again["history"]["scan_advanced"] is False
    assert store.conversation(cid)["history"]["status"] == "complete"


@pytest.mark.parametrize("data", [
    {"scan_before_id": -1, "scan_oldest_id": 0, "scan_has_more": False, "scan_fetched": 0},
    {"scan_before_id": True, "scan_oldest_id": 0, "scan_has_more": False, "scan_fetched": 0},
    {"scan_before_id": 20, "scan_oldest_id": 21, "scan_has_more": True, "scan_fetched": 1},
    {"scan_before_id": 20, "scan_oldest_id": 0, "scan_has_more": True, "scan_fetched": 1},
    {"scan_before_id": 0, "scan_oldest_id": 0, "scan_has_more": True, "scan_fetched": 0},
])
def test_bad_scan_metadata_is_rejected(fixture, data):
    store, _, _ = fixture
    with pytest.raises(ValueError, match="history scan"):
        store.telegram_mirror_state(
            account_ref=ACCOUNT, peer_id=PEER, user_id=store.default_user_id, **data)


def test_history_page_returns_only_older_owned_messages(fixture):
    store, _, ingest = fixture
    cid = ingest(10)
    for mid in (15, 20, 30, 40):
        ingest(mid)
    before = store.telegram_history_page(cid, before_id=40, limit=2)
    assert [m["message_id"].rsplit(":", 1)[-1] for m in before] == ["20", "30"]
    assert all(m["seen_at"] is None for m in before)
    assert store._connection.execute(
        "SELECT COUNT(*) FROM workspace_events WHERE eligible=1").fetchone()[0] == 0
    with pytest.raises(ValueError):
        store.telegram_history_page(cid, before_id=True)


def test_mcp_history_read_fetches_exact_older_page_without_marking_seen(fixture):
    store, service, ingest = fixture
    cid = ingest(10)
    requests = []

    def runner(request, timeout):
        assert timeout == 25
        payload = json.loads(request.data)
        requests.append(payload)
        ingest(5)
        ingest(6)
        return io.BytesIO(json.dumps({
            "account_id": ACCOUNT, "peer_id": PEER, "mirrored": 2,
            "oldest_id": 5, "has_more": False,
        }).encode())

    adapter = TelegramChannelAdapter(
        store, service, store.default_user_id,
        bridge_url="http://127.0.0.1:1234", runner=runner)
    result = adapter.read(chat_id=cid, before_id=10, history_limit=2)
    assert requests == [{"account_id":ACCOUNT,"peer_id":PEER,"limit":2,"scan":True,"before_id":10}]
    assert [m["body"] for m in result["chat"]["messages"]] == ["body-5", "body-6"]
    assert result["sync"]["oldest_id"] == 5
    assert result["sync"]["has_more"] is False
    assert not any(m["seen_at"] is not None for m in result["chat"]["messages"])
