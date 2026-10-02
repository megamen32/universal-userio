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
