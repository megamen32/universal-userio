"""Offline ChatGPT draft-only / scoped approval regression without HTTP or browser traffic."""
import pytest

from universal_userio.adapters import ChatGPTWebChannelAdapter, ChatGPTCDPChannelAdapter
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def suggest(self, **_kwargs):
        raise AssertionError("no AI draft may run on reconciliation")


class Outbox:
    def __init__(self):
        self.calls = []

    def send_chatgpt_reply(self, **kwargs):
        self.calls.append(kwargs)
        return "mock-provider-receipt"

    def send_reply(self, **kwargs):
        raise AssertionError("ChatGPT must not fall back to another provider")


def test_cdp_send_only_stages_and_approve_is_idempotent(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "cdp.sqlite3")
    outbox = Outbox()
    service = UserIOService(store, Generator(), outbox)
    class Cdp:
        def __init__(self):
            self.calls = []
        def call(self, name, arguments):
            self.calls.append(name)
            if name != "export_chat":
                raise AssertionError("no provider send or other CDP call permitted")
            return {"content": '{"chatRef":"ref1","messages":[{"messageRef":"native1","role":"user","text":"hello"}]}'}
    cdp = Cdp()
    adapter = ChatGPTCDPChannelAdapter(store, service, store.default_user_id, client=cdp)
    draft = adapter.send(chat_id="ref1", text="response")
    assert draft.status == "proposed"
    assert cdp.calls == ["export_chat"]
    assert outbox.calls == []
    assert store.conversation(draft.conversation_id, user_id=store.default_user_id)["sender"] == "ref1"
    approved = service.approve(draft.id)
    replay = service.approve(draft.id)
    assert approved.status == replay.status == "approved"
    assert replay.replayed is True
    assert outbox.calls == [{"chat_ref":"ref1", "draft_id":draft.id, "body":"response"}]


def test_web_account_scoped_draft_binds_original_account(tmp_path, monkeypatch):
    store = SQLiteUserIOStore(tmp_path / "web.sqlite3")
    outbox = Outbox()
    service = UserIOService(store, Generator(), outbox)
    from universal_userio import adapters
    monkeypatch.setattr(adapters, "_chatgpt_accounts", lambda *_: {"work": []})
    monkeypatch.setattr(adapters, "_chatgpt_resolve_account", lambda *_: "work")
    monkeypatch.setattr(adapters, "_chatgpt_conversation", lambda *_: {
        "id":"native-ref","messages":[{"node_id":"n1","role":"assistant","text":"model answer"}],
    })
    adapter = ChatGPTWebChannelAdapter(store, service, store.default_user_id)
    draft = adapter.send(chat_id="native-ref", text="owner response")
    assert draft.status == "proposed"
    conversation = store.conversation(draft.conversation_id, user_id=store.default_user_id)
    assert conversation["source"] == "chatgpt:work"
    assert outbox.calls == []
    approved = service.approve(draft.id)
    assert approved.status == "approved"
    assert outbox.calls == [{"chat_ref":"native-ref","draft_id":draft.id,"body":"owner response"}]


def test_draft_rejects_identity_mismatch_and_invalid_text_without_staging(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "invalid.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    class Cdp:
        def call(self, name, args):
            return {"content": '{"chatRef":"other","messages":[{"messageRef":"n1","role":"user","text":"hello"}]}'}
    adapter = ChatGPTCDPChannelAdapter(store, service, store.default_user_id, client=Cdp())
    with pytest.raises(ValueError, match="identity mismatch"):
        adapter.send(chat_id="requested", text="reply")
    with pytest.raises(ValueError, match="must be nonempty"):
        adapter.send(chat_id="requested", text="")
    assert store.conversations(source="chatgpt", user_id=store.default_user_id) == []


def test_unassigned_route_cannot_stage_cross_tenant_draft(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "acl.sqlite3")
    user, _ = store.create_user("unbound-test-user", "test-pass")
    service = UserIOService(store, Generator(), Outbox())
    class Cdp:
        def call(self, name, args):
            return {"content": '{"chatRef":"ref1","messages":[{"messageRef":"n1","role":"user","text":"hello"}]}'}
    adapter = ChatGPTCDPChannelAdapter(store, service, user.user_id, client=Cdp())
    with pytest.raises(ValueError, match="route is not assigned"):
        adapter.send(chat_id="ref1", text="reply")
    assert store.conversations(source="chatgpt", user_id=user.user_id) == []
