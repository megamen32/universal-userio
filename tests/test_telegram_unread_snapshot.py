"""Native provider unread snapshots require exact ownership and do not mark seen."""
import math

import pytest

from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


@pytest.fixture
def fixture(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "counts.sqlite3")
    service = UserIOService(store, object(), object())
    account = "telegram:12345"
    def receive(peer, native, direction="incoming"):
        return service.receive(
            InboxMessage("telegram", f"{account}|{peer}:{native}", peer,
                "payload", float(native), peer_id=peer,
                direction=direction, conversation_kind="group", reconciliation=True),
            route_id="telegram", account_ref=account)[0]
    a = receive("-100111", 10)
    b = receive("-100222", 20)
    yield store, account, a, b
    store.close()


def test_batch_counts_match_native_even_when_local_history_is_partial(fixture):
    store, account, a, b = fixture
    snap = store.telegram_unread_snapshot(
        account_ref=account, user_id=store.default_user_id,
        observed_at=1791710000.,
        dialogs=[
            {"peer_id": "-100111", "unread_count": 477,
             "read_inbox_max_id": 3},
            {"peer_id": "-100222", "unread_count": 376,
             "read_inbox_max_id": 4},
        ])
    assert snap["count"] == 2
    rows = {r["id"]: r for r in store.conversations(source="telegram")}
    assert [rows[x]["unread_count"] for x in (a,b)] == [477,376]
    assert all(rows[x]["unread_source"] == "telegram" for x in (a,b))
    assert all(m["seen_at"] is None for cid in (a,b)
               for m in store.conversation(cid)["messages"])


def test_snapshot_prevalidates_full_batch(fixture):
    store, account, a, b = fixture
    with pytest.raises(ValueError):
        store.telegram_unread_snapshot(
            account_ref=account, user_id=store.default_user_id,
            observed_at=1791710000., dialogs=[
                {"peer_id":"-100111","unread_count":42},
                {"peer_id":"-100222","unread_count":False},
            ])
    assert {r["id"]:r["unread_source"] for r in store.conversations(source="telegram")} == {
        a:"local_mirror", b:"local_mirror"}
    with pytest.raises(ValueError):
        store.telegram_unread_snapshot(
            account_ref=account, user_id=store.default_user_id,
            observed_at=math.nan, dialogs=[])
