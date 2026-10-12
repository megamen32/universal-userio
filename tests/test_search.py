from universal_userio.contracts import InboxMessage
from universal_userio.search import search_conversations
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def propose(self, *_args, **_kwargs):
        return ""


class Outbox:
    def send(self, *_args, **_kwargs):
        return "noop"


def test_global_search_finds_old_message_across_sources(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    service.receive(InboxMessage("telegram", "tg-old", "Alice", "старое редкое слово архипелаг", 1.0), route_id="telegram")
    service.receive(InboxMessage("telegram", "tg-new", "Alice", "новый обычный текст", 2.0), route_id="telegram")
    service.receive(InboxMessage("sms", "sms-1", "+100", "архипелаг в смс", 3.0), route_id="sms")

    results = search_conversations(store, "архипелаг")
    assert {row["source"] for row in results} == {"telegram", "sms"}
    telegram = next(row for row in results if row["source"] == "telegram")
    assert telegram["match_message_id"] == "tg-old"
    assert "архипелаг" in str(telegram["preview"])


def test_search_source_filter(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    service.receive(InboxMessage("telegram", "tg", "Alice", "needle", 1.0), route_id="telegram")
    service.receive(InboxMessage("sms", "sms", "+100", "needle", 2.0), route_id="sms")
    results = search_conversations(store, "needle", source="telegram")
    assert len(results) == 1
    assert results[0]["source"] == "telegram"


def test_search_unread_aggregation_has_linear_sql_work(tmp_path):
    """Fast unit, expected <1s/max5s: prevent CPU/HTTP thread exhaustion."""
    store = SQLiteUserIOStore(tmp_path / "search.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    cid, _ = service.receive(InboxMessage("telegram", "seed", "Group", "needle", 1.0), route_id="telegram")
    row = dict(store._connection.execute("SELECT * FROM messages WHERE message_id='seed'").fetchone())
    columns = list(row)
    rows = []
    for i in range(2000):
        item = dict(row, message_id=f"search-{i}", received_at=i+2., seen_at=None if i % 2 else 1.)
        rows.append(tuple(item[name] for name in columns))
    with store._connection:
        store._connection.executemany(f"INSERT INTO messages ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", rows)
    steps = 0
    def bounded_query():
        nonlocal steps
        steps += 1
        return int(steps > 100)
    store._connection.set_progress_handler(bounded_query, 10000)
    try:
        result = search_conversations(store, "needle", source="telegram")
    finally:
        store._connection.set_progress_handler(None, 0)
    assert len(result) == 1 and result[0]["id"] == cid
    assert result[0]["unread_count"] == 1001
    assert steps <= 100
