"""Unified text and semantic search over the whole retained history.

Covers the 16.10.2026 product contract: per-message hits (no conversation
grouping that hides a match), the server-side conversation allow-list, live
re-read before emitting, opt-in semantic search with an honest text-only
fallback, and a bounded resumable index.
"""
from __future__ import annotations

import io
import json

import pytest

from universal_userio.contracts import InboxMessage
from universal_userio.search import search_coverage, search_messages
from universal_userio.semantic_index import (
    SemanticSettings, SemanticUnavailable, backfill_step, chunk_text, initialize,
    point_id, resolve_settings, status,
)
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def suggest(self, *, conversation_id, latest_message):
        return "ответ"


def _service(tmp_path, **kwargs):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    store.register_account(account_id="telegram:540308572", provider="telegram",
                           display_name="Личный Telegram", can_read=True, can_reply=True,
                           credential_ref="telegram-qr:account-1")
    service = UserIOService(store, Generator(), object(), **kwargs)
    return service, store


def _receive(service, sender, body, *, peer="8810909089"):
    message = InboxMessage("telegram", f"{peer}:m-{abs(hash(body)) % 100000}", sender, body,
                           1000.0 + (abs(hash(body)) % 1000), peer_id=peer)
    cid, _ = service.receive(message, route_id="telegram", account_ref="telegram:540308572")
    return cid


def test_message_hits_are_not_collapsed_per_conversation(tmp_path):
    service, store = _service(tmp_path)
    _receive(service, "Менеджер", "первое сообщение про индексацию")
    _receive(service, "Менеджер", "второе сообщение про индексацию")
    _receive(service, "Менеджер", "третье сообщение про индексацию")
    found = search_messages(store, "индексацию", scope_holder=None) if False else search_messages(
        store, "индексацию", limit=50)
    assert len(found["results"]) == 3
    assert {row["match_type"] for row in found["results"]} == {"exact"}
    assert all(row["deep_link"].startswith("https://msg.bezrabotnyi.com/#/chat/") for row in found["results"])
    assert all(row["message_id"] for row in found["results"])


def test_conversation_allow_list_is_a_server_side_prefilter(tmp_path):
    service, store = _service(tmp_path)
    wanted = _receive(service, "Менеджер", "совпадение по смыслу и тексту тут")
    other = _receive(service, "Другой чат", "совпадение по смыслу и тексту там", peer="700000001")
    assert wanted != other
    scoped = search_messages(store, "совпадению", conversation_ids=[wanted], limit=20)
    assert [row["conversation_id"] for row in scoped["results"]] == [wanted]
    unscoped = search_messages(store, "совпадению", conversation_ids=[], limit=20)
    assert {wanted, other} <= {row["conversation_id"] for row in unscoped["results"]}
    # A chat id that does not belong to this user simply matches nothing.
    assert search_messages(store, "совпадению", conversation_ids=["conv_does_not_exist"],
                           limit=20)["results"] == []


def test_deleted_and_edited_messages_never_come_from_the_index(tmp_path):
    service, store = _service(tmp_path)
    conversation = _receive(service, "Менеджер", "секретная фраза про отпуск")
    message_rowid = store._connection.execute(
        "SELECT rowid FROM messages WHERE conversation_id=?", (conversation,)).fetchone()[0]
    assert search_messages(store, "секретную", limit=10)["results"]
    store._connection.execute("UPDATE messages SET deleted_at=1 WHERE rowid=?", (message_rowid,))
    store._connection.commit()
    assert search_messages(store, "секретную", limit=10)["results"] == []


def test_text_search_works_without_any_vector_configuration(tmp_path):
    service, store = _service(tmp_path)
    _receive(service, "Менеджер", "без вектора всё равно ищется")
    found = search_messages(store, "вектора", mode="hybrid", limit=10)
    assert found["mode_used"] == "text"
    assert found["semantic"]["available"] is False
    assert found["results"]
    coverage = search_coverage(store)
    assert coverage["text_only"] is True
    assert coverage["text"]["messages"] >= 1


def test_search_arguments_reject_unbounded_input():
    from universal_userio.search_surface import SearchArgumentsInvalid, normalize_search_arguments
    assert normalize_search_arguments({"scope": "message"})["mode"] == "hybrid"
    assert normalize_search_arguments({"scope": "conversation"})["mode"] == "text"
    with pytest.raises(SearchArgumentsInvalid):
        normalize_search_arguments({"scope": "everything"})
    with pytest.raises(SearchArgumentsInvalid):
        normalize_search_arguments({"conversation_ids": [f"conv-{index}" for index in range(101)]})
    with pytest.raises(SearchArgumentsInvalid):
        normalize_search_arguments({"conversation_ids": ["conv'; DROP TABLE messages;--"]})
    # An oversized page is clamped to the bounded contract, never honoured.
    assert normalize_search_arguments({"scope": "message", "mode": "semantic", "limit": 5000})["limit"] == 200
    assert normalize_search_arguments({"limit": 5000})["limit"] == 100


def test_chunking_is_bounded_and_stable():
    chunks = chunk_text("слово " * 5000)
    assert len(chunks) > 1
    assert all(len(chunk) <= 1200 for chunk in chunks)
    assert " ".join(chunks) == " ".join(("слово " * 5000).split())
    assert chunk_text("   ") == []
    assert point_id("u", 5, 0) == point_id("u", 5, 0)
    assert point_id("u", 5, 0) != point_id("u", 6, 0)


def test_semantic_settings_never_expose_keys(tmp_path):
    service, store = _service(tmp_path)
    store.set_user_search_settings({
        "enabled": True, "qdrant_url": "https://qdrant.example", "qdrant_collection": "userio_history",
        "qdrant_token": "super-secret-qdrant", "embedding_url": "https://embed.example",
        "embedding_model": "qwen3-embedding:4b", "embedding_token": "super-secret-embedding",
    })
    public = store.user_search_settings_public()
    assert public["has_qdrant_key"] is True and public["has_embedding_key"] is True
    assert "super-secret" not in json.dumps(public)
    settings = resolve_settings(store, environment={})
    assert settings.enabled and settings.configured()
    assert "super-secret" not in json.dumps(settings.public())
    assert status(store)["indexed_messages"] == 0


def test_incomplete_configuration_is_treated_as_disabled(tmp_path):
    service, store = _service(tmp_path)
    store.set_user_search_settings({"enabled": True, "qdrant_url": "https://qdrant.example"})
    assert resolve_settings(store, environment={}).enabled is False
    assert search_coverage(store)["text_only"] is True


class _FakeVectorService:
    """A local stand-in for the owner's Qdrant and embeddings endpoints."""

    def __init__(self) -> None:
        self.points: dict[int, tuple[list[float], dict]] = {}
        self.embedded: list[str] = []
        self.down = False

    # --- embeddings -----------------------------------------------------
    def embed(self, texts, _dimensions=16):
        if self.down:
            raise SemanticUnavailable("embeddings unreachable")
        self.embedded.extend(texts)
        return [[1.0] + [0.0] * (_dimensions - 1) for _ in texts]

    def upsert(self, points):
        if self.down:
            raise SemanticUnavailable("qdrant unreachable")
        for point in points:
            self.points[int(point["id"])] = (list(point["vector"]), point["payload"])

    def delete(self, ids):
        for value in ids:
            self.points.pop(int(value), None)

    def search(self, vector, limit, filt=None):
        """Cosine over the stand-in vectors, honouring the payload filter."""
        allowed = []
        for condition in ((filt or {}).get("must") or []):
            if condition.get("key") == "conversation_id":
                allowed += condition["match"]["any"]
        scored = []
        for stored_vector, payload in self.points.values():
            if allowed and payload.get("conversation_id") not in allowed:
                continue
            score = sum(float(a) * float(b) for a, b in zip(vector, stored_vector))
            scored.append((score, payload))
        scored.sort(key=lambda item: -item[0])
        return [{"payload": payload, "score": float(score)} for score, payload in scored[:limit]]

    def count(self):
        return len(self.points)


def _wire(monkeypatch, service, fake, **settings_overrides):
    """Point the index at the local stand-in instead of the network."""
    values = {"enabled": True, "qdrant_url": "https://qdrant.local", "qdrant_collection": "userio_history",
              "embedding_url": "https://embed.local", "embedding_model": "qwen3-embedding:4b",
              "dimensions": 16}
    values.update(settings_overrides)
    service._store.set_user_search_settings(values)
    dimensions = int(values["dimensions"])
    monkeypatch.setattr(
        "universal_userio.semantic_index.qdrant_request",
        lambda settings, method, path, body=None, timeout=30: _fake_qdrant(
            fake, method, path, body, dimensions))
    monkeypatch.setattr(
        "universal_userio.semantic_index.embed",
        lambda settings, texts, timeout=60: fake.embed(texts, settings.dimensions))
    return resolve_settings(service._store, environment={})


def _fake_qdrant(fake: _FakeVectorService, method: str, path: str, body, dimensions: int = 16):
    """Stand-in for the Qdrant REST API, including the status tuple."""
    if "/points/search" in path:
        return 200, {"result": fake.search(body["vector"], body["limit"], body.get("filter"))}
    if "/points/delete" in path:
        fake.delete(body["points"])
        return 200, {"status": "ok"}
    if method == "PUT" and "/points" in path:
        fake.upsert(body["points"])
        return 200, {"status": "completed"}
    if path.rstrip("/").endswith("/points") and method == "PUT":
        return 200, {"result": True}
    if "/collections/" in path and not body:
        return 200, {"result": {"config": {"params": {"vectors": {"size": dimensions}}},
                                "points_count": fake.count()}}
    return 200, {"result": True}


def test_semantic_search_returns_real_message_hits(tmp_path, monkeypatch):
    service, store = _service(tmp_path)
    wanted = _receive(service, "Менеджер", "проценты по годовому плану оказались выше ожидания")
    _receive(service, "Другой чат", "совсем другой разговор про погоду", peer="700000001")
    fake = _FakeVectorService()
    _wire(monkeypatch, service, fake)
    step = backfill_step(store, resolve_settings(store, environment={}), limit=50)
    assert step["indexed"] == 2
    assert step["chunks"] == 2
    assert status(store)["coverage_ratio"] == 1.0
    # A paraphrase-only question: no word of the stored text appears in it, so a
    # text-only engine can answer nothing here.
    assert search_messages(store, "насколько выросла годовая выручка", mode="text", limit=10)["results"] == []
    found = search_messages(store, "насколько выросла годовая выручка", mode="semantic", limit=10)
    assert found["mode_used"] == "semantic"
    assert found["semantic"]["available"] is True
    assert wanted in {row["conversation_id"] for row in found["results"]}
    for row in found["results"]:
        assert row["match_type"] == "semantic"
        assert row["account_ref"] == "telegram:540308572"
        assert row["peer_id"] in {"8810909089", "700000001"}
        assert row["deep_link"].startswith("https://msg.bezrabotnyi.com/#/chat/")
        assert row["snippet"]


def test_semantic_hits_are_reread_and_dropped_when_the_source_moves_on(tmp_path, monkeypatch):
    service, store = _service(tmp_path)
    conversation = _receive(service, "Менеджер", "проценты по годовому плану выросли")
    fake = _FakeVectorService()
    _wire(monkeypatch, service, fake)
    backfill_step(store, resolve_settings(store, environment={}), limit=50)
    query = "насколько выросли показатели"
    assert search_messages(store, query, mode="semantic", limit=10)["results"]
    # Edited after indexing: the stored vector no longer describes the text.
    store._connection.execute("UPDATE messages SET body='совсем другой текст'")
    store._connection.commit()
    assert search_messages(store, query, mode="semantic", limit=10)["results"] == []
    assert store._connection.execute(
        "SELECT COUNT(*) FROM search_vector_queue WHERE operation='upsert'").fetchone()[0] == 1
    # Moved to another conversation: the allow-list no longer matches it.
    store._connection.execute("UPDATE messages SET body='проценты по годовому плану выросли'")
    store._connection.execute("UPDATE messages SET conversation_id='conv_moved'")
    store._connection.commit()
    assert search_messages(store, query, mode="semantic", conversation_ids=[conversation],
                           limit=10)["results"] == []


def test_semantic_hits_stop_when_the_account_is_revoked(tmp_path, monkeypatch):
    service, store = _service(tmp_path)
    _receive(service, "Менеджер", "проценты по годовому плану выросли")
    fake = _FakeVectorService()
    _wire(monkeypatch, service, fake)
    backfill_step(store, resolve_settings(store, environment={}), limit=50)
    query = "насколько выросли показатели"
    assert search_messages(store, query, mode="semantic", limit=10)["results"]
    store._connection.execute("UPDATE provider_accounts SET enabled=0 WHERE id='telegram:540308572'")
    store._connection.commit()
    assert search_messages(store, query, mode="semantic", limit=10)["results"] == []


def test_semantic_pagination_does_not_repeat_a_page(tmp_path, monkeypatch):
    service, store = _service(tmp_path)
    for index in range(5):
        _receive(service, "Менеджер", f"строка отчёта {index} про выручку", peer=f"70000000{index}")
    fake = _FakeVectorService()
    _wire(monkeypatch, service, fake)
    backfill_step(store, resolve_settings(store, environment={}), limit=50)
    first = search_messages(store, "выручку", mode="semantic", limit=2, offset=0)
    second = search_messages(store, "выручку", mode="semantic", limit=2, offset=2)
    third = search_messages(store, "выручку", mode="semantic", limit=2, offset=4)
    ids = [[row["message_id"] for row in page["results"]] for page in (first, second, third)]
    flat = [value for page in ids for value in page]
    assert len(flat) == len(set(flat)) == 5
    assert ids[0] and ids[1] and ids[2]


def test_semantic_outage_reports_text_only(tmp_path, monkeypatch):
    service, store = _service(tmp_path)
    _receive(service, "Менеджер", "точная фраза про отчётность")
    fake = _FakeVectorService()
    _wire(monkeypatch, service, fake)
    backfill_step(store, resolve_settings(store, environment={}), limit=50)
    fake.down = True
    found = search_messages(store, "отчётность", mode="hybrid", limit=10)
    assert found["mode_used"] == "text"
    assert found["semantic"]["available"] is False
    assert found["semantic"]["reason"]
    assert [row["match_type"] for row in found["results"]] == ["exact"]
    assert search_coverage(store, environment={})["text_only"] is True


def test_semantic_respects_the_conversation_allow_list(tmp_path, monkeypatch):
    service, store = _service(tmp_path)
    wanted = _receive(service, "Менеджер", "разрешённый чат про бюджет")
    forbidden = _receive(service, "Другой чат", "запрещённый чат про бюджет", peer="700000001")
    fake = _FakeVectorService()
    _wire(monkeypatch, service, fake)
    backfill_step(store, resolve_settings(store, environment={}), limit=50)
    found = search_messages(store, "бюджет", mode="semantic", conversation_ids=[wanted], limit=10)
    assert [row["conversation_id"] for row in found["results"]] == [wanted]
    unscoped = search_messages(store, "бюджет", mode="semantic", limit=10)
    assert {row["conversation_id"] for row in unscoped["results"]} == {wanted, forbidden}


def test_index_resumes_from_its_checkpoint(tmp_path, monkeypatch):
    service, store = _service(tmp_path)
    for index in range(6):
        _receive(service, "Менеджер", f"сообщение номер {index} про отчётность")
    fake = _FakeVectorService()
    _wire(monkeypatch, service, fake)
    settings = resolve_settings(store, environment={})
    first = backfill_step(store, settings, limit=2)
    assert first["indexed"] == 2
    assert status(store)["indexed_messages"] == 2
    total = first["indexed"]
    while status(store)["checkpoint"] < status(store)["max_rowid"] and total < 6:
        total += backfill_step(store, settings, limit=2)["indexed"]
    assert status(store)["indexed_messages"] == 6
    assert status(store)["complete"] is True
    # Re-running a finished slice must not duplicate vectors.
    points_before = fake.count()
    backfill_step(store, settings, limit=2)
    assert fake.count() == points_before


def test_queue_drains_edits_and_deletions(tmp_path, monkeypatch):
    from universal_userio.semantic_index import drain_queue
    service, store = _service(tmp_path)
    _receive(service, "Менеджер", "первая версия сообщения")
    fake = _FakeVectorService()
    _wire(monkeypatch, service, fake)
    settings = resolve_settings(store, environment={})
    backfill_step(store, settings, limit=50)
    assert fake.count() == 1
    store._connection.execute("UPDATE messages SET body='вторая версия сообщения'")
    store._connection.commit()
    drained = drain_queue(store, settings, limit=50)
    assert drained["indexed"] == 1
    assert status(store)["indexed_messages"] == 1
    store._connection.execute("DELETE FROM messages")
    store._connection.commit()
    drained = drain_queue(store, settings, limit=50)
    assert drained["deleted"] == 1
    assert fake.count() == 0
    assert status(store)["indexed_messages"] == 0


def test_index_step_is_bounded_and_resumable(tmp_path, monkeypatch):
    service, store = _service(tmp_path)
    for index in range(4):
        _receive(service, "Менеджер", f"сообщение {index}")
    fake = _FakeVectorService()
    _wire(monkeypatch, service, fake)
    from universal_userio.search import run_search_index
    result = run_search_index(service, {"messages": 2}, user_id=None)
    assert result["ran"] is True
    assert result["step"]["indexed"] == 2
    assert result["budget_seconds"] <= 1800
    assert search_coverage(store)["text"]["messages"] >= 4

def test_outage_keeps_the_work_and_resumes_without_losing_it(tmp_path, monkeypatch):
    """An embedder outage must not advance the checkpoint or consume the queue."""
    service, store = _service(tmp_path)
    for index in range(4):
        _receive(service, "Менеджер", f"строка {index} про выручку")
    fake = _FakeVectorService()
    _wire(monkeypatch, service, fake)
    settings = resolve_settings(store, environment={})
    assert backfill_step(store, settings, limit=4)["indexed"] == 4
    # Force a full re-index through the queue, then fail halfway.
    store._connection.execute("UPDATE messages SET body=body")  # queue a refresh hint
    store._connection.commit()
    checkpoint = status(store)["checkpoint"]
    assert checkpoint > 0
    fake.down = True
    from universal_userio.semantic_index import drain_queue
    failed = drain_queue(store, settings, limit=50)
    assert failed["indexed"] == 0
    assert failed["failed"] >= 1
    assert failed["deferred"] >= 1
    assert status(store)["checkpoint"] == checkpoint
    queued = store._connection.execute("SELECT COUNT(*) FROM search_vector_queue").fetchone()[0]
    assert queued == 4
    # The service comes back: the same slice completes and the queue empties.
    fake.down = False
    recovered = drain_queue(store, settings, limit=50)
    assert recovered["indexed"] == 4
    assert store._connection.execute("SELECT COUNT(*) FROM search_vector_queue").fetchone()[0] == 0
    assert status(store)["indexed_messages"] == 4


def test_chunked_message_never_sends_an_unbounded_request(tmp_path, monkeypatch):
    """One very large message is written in bounded slices, not one huge call."""
    service, store = _service(tmp_path)
    _receive(service, "Менеджер", "проценты " * 6000)
    fake = _FakeVectorService()
    _wire(monkeypatch, service, fake)
    seen_batches: list[int] = []
    original = fake.embed

    def counting_embed(texts, _dimensions=16):
        seen_batches.append(len(texts))
        return original(texts, _dimensions)

    import universal_userio.semantic_index as si
    si.embed = lambda settings, texts, timeout=60: counting_embed(texts, settings.dimensions)
    from universal_userio.search import run_search_index
    result = run_search_index(service, {"messages": 10}, user_id=None)
    assert result["step"]["chunks"] > 8
    assert max(seen_batches) <= 8
    state = store._connection.execute(
        "SELECT state,chunks FROM search_vector_state").fetchall()
    assert {str(row["state"]) for row in state} <= {"indexed", "partial"}
    # A later step finishes the remaining chunks of the same message.
    total = status(store)["indexed_chunks"]
    for _ in range(200):
        step = run_search_index(service, {"messages": 10}, user_id=None)
        total += step["step"]["chunks"]
        if status(store)["indexed_messages"] == 1:
            break
    assert status(store)["indexed_messages"] == 1


def test_outgoing_message_reports_the_owner_as_its_author(tmp_path):
    """The stored sender is the chat counterpart, not the author of an outgoing message."""
    service, store = _service(tmp_path)
    incoming = InboxMessage("telegram", "287262367:m-1", "Dmitry Karpunichev tealband",
                            "а какие там финансовые ограничения", 10.0, peer_id="287262367")
    conversation, _ = service.receive(incoming, route_id="telegram", account_ref="telegram:540308572")
    outgoing = InboxMessage("telegram", "287262367:m-2", "Dmitry Karpunichev tealband",
                            "выжимка для первого лица с финансовыми ограничениями", 11.0,
                            peer_id="287262367", direction="outgoing")
    service.receive(outgoing, route_id="telegram", account_ref="telegram:540308572")
    rows = search_messages(store, "финансовые ограничения", limit=10)["results"]
    assert len(rows) == 2
    by_direction = {row["direction"]: row for row in rows}
    owner_hit = by_direction["outgoing"]
    assert owner_hit["sender"] == "Dmitry Karpunichev tealband"
    assert owner_hit["author"]["id"] == "540308572"
    assert owner_hit["author"]["role"] == "owner"
    assert owner_hit["provider_message_id"] == "m-2"
    peer_hit = by_direction["incoming"]
    assert peer_hit["author"]["id"] == "287262367"
    assert peer_hit["author"]["role"] == "interlocutor"
    for row in rows:
        assert row["account_ref"] == "telegram:540308572"
        assert row["peer_id"] == "287262367"
        assert row["channel"] == "telegram"


def test_canonical_author_matches_the_messenger_projection(tmp_path):
    from universal_userio.source_projection import canonical_author
    owner = canonical_author(channel="telegram", account_ref="telegram:540308572",
                            conversation_kind="direct", peer_id="287262367",
                            direction="outgoing", sender="Dmitry Karpunichev tealband")
    assert owner["id"] == "540308572" and owner["role"] == "owner"
    peer = canonical_author(channel="telegram", account_ref="telegram:540308572",
                           conversation_kind="direct", peer_id="287262367",
                           direction="incoming", sender="Dmitry Karpunichev tealband")
    assert peer["id"] == "287262367" and peer["role"] == "interlocutor"
    mail = canonical_author(channel="gmail", account_ref="gmail-care", conversation_kind="direct",
                            peer_id="owner@example.test", direction="outgoing",
                            sender="owner@example.test", gmail_sender_id="care@example.test")
    assert mail["id"] == "care@example.test"
    routed = canonical_author(channel="telegram", account_ref="telegram:540308572",
                             conversation_kind="direct", peer_id="287262367",
                             direction="incoming", sender="Dmitry Karpunichev tealband",
                             routing={"author_id": "999", "author_name": "Dmitry K"})
    assert routed["id"] == "999" and routed["name"] == "Dmitry K"


def test_group_message_author_comes_from_provider_routing(tmp_path):
    """A group chat stores the group title in the sender column; routing holds the author."""
    import json as json_module
    service, store = _service(tmp_path)
    store.register_account(account_id="telegram:540308572", provider="telegram",
                           display_name="Личный Telegram", can_read=True, can_reply=True,
                           credential_ref="telegram-qr:account-1")
    message = InboxMessage("telegram", "-1001234567890:m-77", "Рабочая группа",
                           "Дмитрий, вот выжимка по ограничениям", 12.0,
                           peer_id="-1001234567890", conversation_kind="group")
    conversation, _ = service.receive(message, route_id="telegram", account_ref="telegram:540308572")
    store._connection.execute(
        "INSERT INTO message_attachments(user_id,source,message_id,idx,kind,content_type,filename,provider_ref)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (store.default_user_id, "telegram", "telegram:540308572|-1001234567890:m-77", 0,
         "telegram_routing", "application/json", "routing.json",
         json_module.dumps({"author_id": "287262367", "author_name": "Дмитрий Карпуничев",
                            "reply_to_message_id": "1985700", "is_reply_to_account": True})))
    store._connection.execute(
        "UPDATE messages SET provider_thread_id='1985700', provider_reply_to='1985700'"
        " WHERE message_id=?", ("telegram:540308572|-1001234567890:m-77",))
    store._connection.commit()
    row = store._connection.execute(
        "SELECT conversation_id FROM messages WHERE message_id=?",
        ("telegram:540308572|-1001234567890:m-77",)).fetchone()
    assert str(row["conversation_id"]) == conversation
    hits = search_messages(store, "выжимка по ограничениям", conversation_ids=[conversation], limit=5)
    assert len(hits["results"]) == 1
    hit = hits["results"][0]
    assert hit["sender"] == "Рабочая группа"
    assert hit["author"]["id"] == "287262367"
    assert hit["author"]["name"] == "Дмитрий Карпуничев"
    assert hit["author"]["role"] == "interlocutor"
    assert hit["thread_id"] == "1985700"
    assert hit["reply_to_provider_message_id"] == "1985700"


def test_second_owner_indexes_own_history_without_inheriting_the_owner(tmp_path, monkeypatch):
    """Every user who opts in gets their own index; nothing crosses users."""
    store = SQLiteUserIOStore(tmp_path / "multi.sqlite3")
    second_principal = store.register_user("second-owner", "second-owner-test-password")
    second_user = second_principal.user_id
    store.register_account(account_id="telegram:900000001", provider="telegram",
                           display_name="Второй аккаунт", can_read=True, can_reply=True,
                           credential_ref="telegram-qr:account-9", user_id=second_user)
    second = UserIOService(store, Generator(), object())
    message = InboxMessage("telegram", "peer-2:m-1", "Коллега", "во втором аккаунте про годовые планы",
                           5.0, peer_id="peer-2")
    store.bind_channel_route(user_id=second_user, source="telegram", route_id="telegram")
    second.receive(message, route_id="telegram", account_ref="telegram:900000001",
                   user_id=second_user)
    owner = UserIOService(store, Generator(), object())
    owner.receive(InboxMessage("telegram", "peer-1:m-1", "Менеджер", "у владельца про годовые планы",
                               6.0, peer_id="peer-1"),
                  route_id="telegram", account_ref="telegram:540308572")
    store.set_user_search_settings({
        "enabled": True, "qdrant_url": "https://qdrant.local", "qdrant_collection": "second_collection",
        "embedding_url": "https://embed.local", "embedding_model": "qwen3-embedding:4b",
        "dimensions": 16}, user_id=second_user)
    fake = _FakeVectorService()
    _wire(monkeypatch, second, fake)
    store.set_user_search_settings({
        "enabled": True, "qdrant_url": "https://qdrant.local", "qdrant_collection": "second_collection",
        "embedding_url": "https://embed.local", "embedding_model": "qwen3-embedding:4b",
        "dimensions": 16})
    from universal_userio.search_index import IndexWorker
    worker = IndexWorker(store, sleep_seconds=1, messages=10)
    assert sorted(worker.opted_in_users()) == sorted(["user_owner", second_user])
    seen: list[str] = []
    for _ in range(2):
        summary = worker.run_once()
        seen.append(summary["user_id"])
    assert sorted(seen) == sorted(["user_owner", second_user])
    assert store.user_search_settings_public(user_id=second_user)["qdrant_collection"] == "second_collection"
    # Each owner only ever indexed their own messages.
    rows = store._connection.execute(
        "SELECT user_id, COUNT(*) FROM search_vector_state GROUP BY user_id").fetchall()
    assert {str(row["user_id"]) for row in rows} <= {"user_owner", second_user}
    assert semantic_index_status(store, second_user)["indexed_messages"] == 1
    assert semantic_index_status(store, second_user)["live_messages"] == 1
    assert semantic_index_status(store, second_user)["live_messages"] == 1


def semantic_index_status(store, user_id):
    from universal_userio.semantic_index import status
    return status(store, user_id=user_id)


def test_switching_the_index_configuration_resets_only_own_derived_state(tmp_path, monkeypatch):
    service, store = _service(tmp_path)
    _receive(service, "Менеджер", "проценты по годовому плану выросли")
    fake = _FakeVectorService()
    _wire(monkeypatch, service, fake)
    from universal_userio.search import run_search_index
    first = run_search_index(service, {"messages": 10}, user_id=None)
    assert first["ran"] is True and first["indexed_messages"] == 1
    store.set_user_search_settings({"qdrant_collection": "userio_history_v2"})
    second = run_search_index(service, {"messages": 10}, user_id=None)
    assert second["switched"]["changed"] is True
    assert second["indexed_messages"] == 1
    assert second["coverage_ratio"] == 1.0
    # A key change alone is not a configuration change.
    store.set_user_search_settings({"qdrant_token": "another-key"})
    third = run_search_index(service, {"messages": 10}, user_id=None)
    assert third["switched"]["changed"] is False
    # The source history itself is never touched by a switch.
    assert store._connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] >= 1


def test_missing_collection_is_created_and_wrong_dimensions_are_reported(tmp_path, monkeypatch):
    from universal_userio import semantic_index as si
    settings = SemanticSettings(enabled=True, qdrant_url="https://qdrant.local",
                                qdrant_collection="fresh_collection", embedding_url="https://embed.local",
                                embedding_model="m", dimensions=16)
    created: list[str] = []

    def fake_request(settings, method, path, body=None, timeout=30):
        if path.endswith("/index"):
            return 200, {"result": True}
        if method == "GET":
            if path.rstrip("/") not in created:
                return 404, {}
            return 200, {"result": {"config": {"params": {"vectors": {"size": 16}}},
                                 "points_count": 0}}
        if method == "PUT":
            created.append(path)
            return 200, {"result": True}
        return 200, {"result": True}

    monkeypatch.setattr(si, "qdrant_request", fake_request)
    assert si.ensure_collection(settings)["collection"] == "fresh_collection"
    assert any(path == "/collections/fresh_collection" for path in created)
    # A collection with a different contract is reported, never rewritten.
    def wrong(settings, method, path, body=None, timeout=30):
        if path.endswith("/index"):
            return 200, {"result": True}
        return 200, {"result": {"config": {"params": {"vectors": {"size": 1536}}}, "points_count": 0}}
    monkeypatch.setattr(si, "qdrant_request", wrong)
    try:
        si.ensure_collection(settings)
    except SemanticUnavailable as error:
        assert "1536" in error.reason and "matching collection" in error.reason
    else:
        raise AssertionError("a wrong-dimension collection must be reported, not changed")


def test_a_key_without_create_rights_gets_an_actionable_reason(tmp_path, monkeypatch):
    from universal_userio import semantic_index as si
    settings = SemanticSettings(enabled=True, qdrant_url="https://qdrant.local",
                                qdrant_collection="absent_collection", embedding_url="https://embed.local",
                                embedding_model="m", dimensions=16)

    def refuse_create(settings, method, path, body=None, timeout=30):
        if method == "GET":
            return 404, {}
        return 403, {}

    monkeypatch.setattr(si, "qdrant_request", refuse_create)
    try:
        si.ensure_collection(settings)
    except SemanticUnavailable as error:
        assert "grant create" in error.reason and "absent_collection" in error.reason
    else:
        raise AssertionError("a refused collection creation must be actionable")


def test_embeddings_base_url_is_normalised_once():
    """Owners configure an origin, the standard /v1 base, or the full path."""
    from universal_userio.semantic_index import embeddings_endpoint
    assert embeddings_endpoint("http://192.168.2.75:11440/v1") == "http://192.168.2.75:11440/v1/embeddings"
    assert embeddings_endpoint("http://192.168.2.75:11440") == "http://192.168.2.75:11440/v1/embeddings"
    assert embeddings_endpoint("https://api.example.com/v1/embeddings") == "https://api.example.com/v1/embeddings"
    assert embeddings_endpoint("https://api.example.com/v1/") == "https://api.example.com/v1/embeddings"
    assert embeddings_endpoint("https://api.example.com/openai/v1") == "https://api.example.com/openai/v1/embeddings"
    assert "/v1/v1/" not in embeddings_endpoint("http://192.168.2.75:11440/v1")
    for base in ("http://192.168.2.75:11440/v1", "http://192.168.2.75:11440",
                 "https://api.example.com/v1/embeddings"):
        assert embeddings_endpoint(embeddings_endpoint(base)) == embeddings_endpoint(base)


def test_qwen3_queries_carry_the_instruction_and_other_models_do_not():
    """Qwen3-Embedding is instruction tuned; documents stay raw, queries do not."""
    from universal_userio.semantic_index import instruction_query
    for model in ("qwen3-embedding:4b", "Qwen3-Embedding-4B", "qwen3-embedding"):
        prepared = instruction_query(model, "Сравнение ежемесячных отчётов и контроль бюджетов")
        assert prepared.startswith("Instruct: ")
        assert prepared.endswith("Query: Сравнение ежемесячных отчётов и контроль бюджетов")
        assert instruction_query(model, prepared) == prepared  # never doubled
    for model in ("text-embedding-3-large", "qwen2-embedding", "", "bge-m3"):
        raw = "  Сравнение ежемесячных отчётов\n и контроль бюджетов  "
        assert instruction_query(model, raw) == raw
    # The chunked document text is never prefixed.
    assert chunk_text("Документ про бюджеты") == ["Документ про бюджеты"]


def test_effective_owner_settings_are_visible_without_keys(tmp_path, monkeypatch):
    from universal_userio.semantic_index import public_settings
    service, store = _service(tmp_path)
    for key,value in {'QDRANT_URL':'https://vectors.local','QDRANT_COLLECTION':'own_collection',
                      'QDRANT_TOKEN':'private-test-qdrant','EMBEDDING_URL':'https://embed.local/v1',
                      'EMBEDDING_MODEL':'qwen3-embedding:4b','EMBEDDING_TOKEN':'private-test-embed'}.items():
        monkeypatch.setenv('USERIO_SEARCH_'+key,value)
    store.set_user_search_settings({'enabled': True})
    shown=public_settings(store)
    assert shown['qdrant_url']=='https://vectors.local'
    assert shown['embedding_model']=='qwen3-embedding:4b'
    assert shown['has_qdrant_key'] and shown['has_embedding_key']
    assert 'private-test' not in json.dumps(shown)
    second=store.register_user('other-search-owner','other-search-password')
    store.set_user_search_settings({'enabled':True},user_id=second.user_id)
    assert not resolve_settings(store,user_id=second.user_id).configured()
    assert not public_settings(store,user_id=second.user_id)['has_qdrant_key']
