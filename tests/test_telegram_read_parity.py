"""Regression coverage for explicit Telegram history reads; no provider I/O."""
import io
import json
from dataclasses import replace

import pytest

from universal_userio.adapters import TelegramChannelAdapter
from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore

ACCOUNT = "telegram:12345"
PEER = "67890"

@pytest.fixture
def mirror(tmp_path, monkeypatch):
    monkeypatch.setenv("USERIO_API_TOKEN", "fixture-only")
    store = SQLiteUserIOStore(tmp_path / "mirror.sqlite3")
    service = UserIOService(store, object(), object())
    store.register_account(account_id=ACCOUNT, provider="telegram", display_name="Owner",
        can_read=True, can_reply=True, credential_ref="fixture", enabled=True)
    def ingest(mid, body, **kw):
        return service.receive(InboxMessage("telegram", f"{ACCOUNT}|{PEER}:{mid}", "Peer",
            body, float(mid), peer_id=PEER, conversation_kind="direct", **kw),
            route_id="telegram", account_ref=ACCOUNT)[0]
    cid = ingest(10, "cached", reconciliation=True)
    yield store, service, ingest, cid
    store.close()


def test_explicit_read_refreshes_owned_history_and_promotes_actual_authors(mirror):
    store, service, ingest, cid = mirror
    calls = []
    arrivals = []
    service.add_inbound_listener(lambda *a: arrivals.append(a))
    def runner(request, timeout):
        calls.append(json.loads(request.data))
        assert request.full_url == "http://127.0.0.1:1234/reconcile"
        assert timeout == 25
        ingest(11, "owner reply", direction="outgoing", reconciliation=True)
        ingest(12, "peer reply", reconciliation=True)
        return io.BytesIO(json.dumps({"account_id":ACCOUNT,"peer_id":PEER,"mirrored":3}).encode())
    adapter = TelegramChannelAdapter(store, service, store.default_user_id,
        bridge_url="http://127.0.0.1:1234", runner=runner)
    result = adapter.read(chat_id=cid)
    assert calls == [{"account_id":ACCOUNT,"peer_id":PEER,"limit":50}]
    assert result["sync"]["fresh"] is True
    assert [(m["body"], m["sender"]) for m in result["chat"]["messages"]] == [
        ("cached", "Peer"), ("owner reply", "Owner"), ("peer reply", "Peer")]
    assert result["chat"]["messages"][1]["author"]["id"] == "12345"
    assert result["chat"]["sender"] == "Peer"  # Routing identity is unchanged.
    assert arrivals == []
    assert result["chat"]["drafts"] == []
    assert store._connection.execute("SELECT COUNT(*) FROM workspace_events WHERE eligible=1").fetchone()[0] == 0
    assert all(m["seen_at"] is None for m in result["chat"]["messages"])


def test_refresh_failure_is_explicit_and_does_not_leak_provider_errors(mirror):
    store, service, _, cid = mirror
    def runner(*args, **kw):
        raise OSError("private transport detail")
    adapter = TelegramChannelAdapter(store, service, store.default_user_id,
        bridge_url="http://127.0.0.1:1234", runner=runner)
    result = adapter.read(chat_id=cid)
    assert result["sync"] == {"status":"failed","fresh":False,"error":"OSError"}
    assert len(result["chat"]["messages"]) == 1
    assert "private transport detail" not in json.dumps(result)


def test_unknown_chat_never_calls_privileged_bridge(mirror):
    store, service, _, _ = mirror
    calls = []
    adapter = TelegramChannelAdapter(store, service, store.default_user_id,
        bridge_url="http://127.0.0.1:1234", runner=lambda *a, **kw: calls.append(a))
    with pytest.raises(KeyError):
        adapter.read(chat_id="foreign-chat")
    assert calls == []


def test_history_replay_preserves_native_text_whitespace(mirror):
    store, service, ingest, cid = mirror
    original = "  native text\n"
    ingest(14, original, reconciliation=True)
    ingest(14, original, reconciliation=True)
    assert store.conversation(cid)["messages"][-1]["body"] == original


def test_single_message_outgoing_author_is_not_peer(mirror):
    store, service, ingest, cid = mirror
    ingest(11, "owner reply", direction="outgoing", reconciliation=True)
    result = TelegramChannelAdapter(store, service, store.default_user_id).read(
        message_id=f"{ACCOUNT}|{PEER}:11")
    assert result["message"]["sender"] == "Owner"


def test_history_replay_preserves_existing_voice_transcript(mirror):
    store, service, ingest, cid = mirror
    rich = {"kind":"voice","content_type":"audio/ogg","filename":"telegram-15.ogg",
        "provider_ref":"15","transcript":"spoken text","transcription_status":"completed",
        "transcription_model":"fixture"}
    ingest(15, "caption\n\nspoken text", attachments=(rich,), reconciliation=True)
    lean = {k:v for k,v in rich.items() if k not in {"transcript","transcription_model"}}
    lean["transcription_status"] = "not_requested"
    ingest(15, "caption", attachments=(lean,), reconciliation=True)
    message = store.conversation(cid)["messages"][-1]
    assert message["body"] == "caption\n\nspoken text"
    assert message["attachments"][0]["transcript"] == "spoken text"
    assert message["attachments"][0]["transcription_status"] == "completed"
