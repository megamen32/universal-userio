"""Fast unit: arrival window, one model call, and zero-call suppression (20s/max200s)."""
from unittest.mock import patch

import pytest

from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def __init__(self):
        self.calls = []
        self.during_model = lambda: None

    def triage_with_context(self, **kwargs):
        self.calls.append(kwargs)
        self.during_model()
        return {"importance": 0.9, "confidence": 1.0, "urgency": "high",
                "reason_codes": ["request"], "reason_ru": "Нужен ответ", "action_required": True,
                "action_summary": "Ответить", "suggested_replies": [{"body": "Хорошо"}]}


def setup(tmp_path, peer="900000101"):
    store = SQLiteUserIOStore(tmp_path / "state.sqlite3")
    generator = Generator()
    service = UserIOService(store, generator, object())
    with patch("universal_userio.store.time.time", return_value=1000):
        cid, _ = service.receive(InboxMessage("telegram", peer+":1", "Катя", "Счёт на 100 рублей", 1000,
            peer_id=peer, conversation_kind="direct"), route_id="owner")
    return store, generator, service, cid


def claim(store, now):
    with patch("universal_userio.store.time.time", return_value=now):
        return store.claim_workspace_event(worker_id="automatic", automatic=True)


def triage(service, seq):
    with patch("universal_userio.service.time.time", return_value=1500):
        return service.triage_workspace_event(event_seq=seq, request_id=f"test-{seq}", automatic=True)


def test_fixed_window_and_single_model_for_related_messages(tmp_path):
    store, gen, service, cid = setup(tmp_path)
    with patch("universal_userio.store.time.time", return_value=1250):
        service.receive(InboxMessage("telegram", "900000101:2", "Катя", "Исправление: 200 рублей, до пятницы", 1250,
            peer_id="900000101", conversation_kind="direct"), route_id="owner")
    assert claim(store, 1299) is None
    leased = claim(store, 1300)
    assert leased["event"]["message_id"] == "900000101:2"
    assert len(leased["event"]["batch_messages"]) == 2
    result = triage(service, leased["event"]["seq"])
    assert result["triage"]["decision"] == "notify"
    assert len(gen.calls) == 1
    texts = [m["body"] for m in gen.calls[0]["actor_context"]["incoming_batch"]["messages"]]
    assert texts == ["Счёт на 100 рублей", "Исправление: 200 рублей, до пятницы"]
    assert claim(store, 1500) is None  # no second model for older member


@pytest.mark.parametrize("state", ["seen", "provider_read", "answered", "bot", "excode"])
def test_no_llm_for_unneeded_input(tmp_path, state):
    store, gen, service, cid = setup(tmp_path, peer="8810909089" if state == "excode" else "900000101")
    if state == "seen":
        store.mark_read_through(cid, "900000101:1")
    elif state == "provider_read":
        store._connection.execute("UPDATE messages SET provider_read=1")
        store._connection.commit()
    elif state == "answered":
        service.receive(InboxMessage("telegram", "900000101:2", "Катя", "Я уже ответил", 1100,
            direction="outgoing", peer_id="900000101", conversation_kind="direct"), route_id="owner")
    elif state == "bot":
        store._connection.execute("UPDATE messages SET sender_is_bot=1")
        store._connection.commit()
    assert claim(store, 1500) is None
    result = triage(service, 1)
    assert result["triage"]["decision"] == "silent"
    assert gen.calls == []


def test_read_after_claim_prevents_cache_or_model_work(tmp_path):
    store, gen, service, cid = setup(tmp_path)
    assert claim(store, 1300)
    store.mark_read_through(cid, "900000101:1")
    service._cached_summary_and_refresh = lambda **kwargs: pytest.fail("cache LLM called")
    assert triage(service, 1)["triage"]["decision"] == "silent"
    assert gen.calls == []


def test_read_during_generation_makes_result_silent(tmp_path):
    store, gen, service, cid = setup(tmp_path)
    assert claim(store, 1300)
    gen.during_model = lambda: store.mark_read_through(cid, "900000101:1")
    result = triage(service, 1)
    assert result["triage"]["decision"] == "silent"
    assert result["drafts"] == []
    assert len(gen.calls) == 1


def test_direct_worker_post_waits_and_manual_button_remains_immediate(tmp_path):
    store, gen, service, cid = setup(tmp_path)
    with patch("universal_userio.service.time.time", return_value=1100):
        result = service.triage_workspace_event(event_seq=1, request_id="auto", automatic=True)
        assert result["status"] == "pending" and gen.calls == []
        manual = service.triage_workspace_event(event_seq=1, request_id="manual")
        assert manual["triage"]["decision"] == "notify"
    assert len(gen.calls) == 1


def test_pause_preserves_queue_and_resume_filters_read_messages(tmp_path):
    store,gen,service,cid=setup(tmp_path)
    store.set_automatic_ai_paused(True)
    assert claim(store,1500) is None
    assert store.workspace_events()['events'][0]['seq']==1
    assert gen.calls==[]
    store.set_automatic_ai_paused(False)
    assert claim(store,1500)['event']['seq']==1
    store.set_automatic_ai_paused(True)
    assert triage(service,1)['triage']['decision']=='silent'
    assert gen.calls==[]


def test_pause_persists_and_is_user_scoped(tmp_path):
    store,gen,service,cid=setup(tmp_path)
    user,_=store.create_user('another-reader','correct horse battery staple')
    store.set_automatic_ai_paused(True)
    assert not store.automatic_ai_paused(user_id=user.user_id)
    assert SQLiteUserIOStore(tmp_path/'state.sqlite3').automatic_ai_paused()
