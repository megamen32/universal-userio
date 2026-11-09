"""Offline tests for ChatGPT browser fallback safety; no provider sends."""
from __future__ import annotations

import pytest

from universal_userio import adapters
from universal_userio import agent_channel
from universal_userio.adapters import ChatGPTWebOutbox


def setup_account(monkeypatch, user="alice", slug="work", agent_id="vk-browser"):
    monkeypatch.setattr(adapters, "_chatgpt_accounts", lambda _user: {slug: []})
    monkeypatch.setitem(adapters._chatgpt_records, (user, slug), {"agent_id": agent_id})
    return user, slug, agent_id


def test_fallback_without_agent_status_entry_fails_before_enqueue(monkeypatch):
    user, slug, agent_id = setup_account(monkeypatch)
    monkeypatch.setattr(ChatGPTWebOutbox, "_send_web",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("HTTP 403")))
    monkeypatch.setattr(agent_channel, "status", lambda **_: {"agents": []})
    calls = []
    with pytest.raises(RuntimeError, match="browser agent route"):
        ChatGPTWebOutbox().send_reply(chat_ref="c1", draft_id="d1", body="hi",
            account_ref=slug, user=user, agent_fallback=lambda **kw: calls.append(kw))
    assert calls == []


def test_failed_read_of_existing_chat_preserves_original_and_never_posts(monkeypatch):
    user, slug, _ = setup_account(monkeypatch)
    monkeypatch.setattr(adapters, "_chatgpt_conversation",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("ChatGPT returned HTTP 429")))
    posts = []
    monkeypatch.setattr(adapters, "_chatgpt_raw",
        lambda *a, **kw: posts.append((a, kw)) or "data: [DONE]")
    with pytest.raises(RuntimeError, match="original conversation could not be verified") as failure:
        ChatGPTWebOutbox()._send_web(chat_ref="original-chat", draft_id="d1",
            body="reply", account_ref=slug, user=user)
    assert "HTTP 429" in str(failure.value.__cause__)
    assert posts == []


class UnknownPost:
    def __init__(self, behavior):
        self.behavior = behavior
        self.posts = []
    def post(self, url, headers, json):
        self.posts.append(json)
        outcome = self.behavior(len(self.posts), json)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def prepared_post(monkeypatch, client):
    user, slug, _ = setup_account(monkeypatch)
    monkeypatch.setattr(adapters, "_chatgpt_user_agent", lambda *_: "safe-test-agent")
    monkeypatch.setattr(adapters, "_chatgpt_client", lambda *_: client)
    monkeypatch.setitem(adapters._chatgpt_tokens, (user, slug), ("fixture-bearer", 9999999999))
    monkeypatch.setattr(adapters, "_chatgpt_conversation", lambda *_: {
        "mapping": {"parent-node": {"message": {"author": {"role": "user"},
            "create_time": 2}}}})
    return user, slug


def test_unknown_post_outcome_readback_unknown_is_not_retried(monkeypatch):
    client = UnknownPost(lambda _n, _payload: ConnectionError("socket closed"))
    user, slug = prepared_post(monkeypatch, client)
    readbacks = []
    def readback(user_arg, slug_arg, chat_ref, message_id, **_kwargs):
        readbacks.append((user_arg, slug_arg, chat_ref, message_id))
        return {"state": "unknown"}
    monkeypatch.setattr(ChatGPTWebOutbox, "_read_delivery_by_message_id", staticmethod(readback))
    with pytest.raises(RuntimeError, match="delivery outcome unknown"):
        ChatGPTWebOutbox()._send_web(chat_ref="original-chat", draft_id="d1",
            body="reply", account_ref=slug, user=user)
    assert len(client.posts) == 1
    assert len(readbacks) == 1
    assert readbacks[0][:3] == (user, slug, "original-chat")
    assert readbacks[0][3] == client.posts[0]["messages"][0]["id"]


def test_unknown_post_confirmed_by_message_id_history_is_not_retried(monkeypatch):
    client = UnknownPost(lambda _n, _payload: ConnectionError("socket closed"))
    user, slug = prepared_post(monkeypatch, client)
    def readback(_user, _slug, _chat, _message_id, **_kwargs):
        return {"state": "delivered", "assistant_id": "assistant-from-history"}
    monkeypatch.setattr(ChatGPTWebOutbox, "_read_delivery_by_message_id", staticmethod(readback))
    receipt = ChatGPTWebOutbox()._send_web(chat_ref="original-chat", draft_id="d1",
        body="reply", account_ref=slug, user=user)
    assert receipt == "assistant-from-history"
    assert len(client.posts) == 1


def test_real_history_reader_confirms_exact_id_but_absence_stays_unknown(monkeypatch):
    user, slug, _ = setup_account(monkeypatch)
    message_id = "original-user-message-id"
    monkeypatch.setattr(adapters, "_chatgpt_conversation", lambda *_: {"mapping": {
        "node-1": {"message": {"id": message_id, "author": {"role": "user"},
            "content": {"parts": ["reply"]}}},
        "node-2": {"message": {"id": "other-id", "author": {"role": "assistant"},
            "content": {"parts": ["answer"]}}},
    }})
    readback = ChatGPTWebOutbox._read_delivery_by_message_id
    assert readback(user, slug, "original-chat", message_id, body="reply")["state"] == "delivered"
    # No matching history row is not proof of absence: the conversation view
    # may be stale or incomplete, so the safe production adapter stays UNKNOWN.
    assert readback(user, slug, "original-chat", "not-found", body="reply")["state"] == "unknown"


def test_unknown_post_confirmed_absent_allows_one_fresh_id_retry(monkeypatch):
    class ThenAccepted(UnknownPost):
        def post(self, url, headers, json):
            self.posts.append(json)
            if len(self.posts) == 1:
                raise ConnectionError("socket closed")
            return type("Response", (), {"status_code": 200, "headers": {},
                "text": "data: [DONE]"})()
    client = ThenAccepted(None)
    user, slug = prepared_post(monkeypatch, client)
    readbacks = []
    def readback(*_args, **_kwargs):
        readbacks.append(True)
        return {"state": "absent"}
    monkeypatch.setattr(ChatGPTWebOutbox, "_read_delivery_by_message_id", staticmethod(readback))
    ChatGPTWebOutbox()._send_web(chat_ref="original-chat", draft_id="d1",
        body="reply", account_ref=slug, user=user)
    assert len(readbacks) == 1
    assert len(client.posts) == 2
    assert client.posts[1]["messages"][0]["id"] != client.posts[0]["messages"][0]["id"]
    assert client.posts[1]["conversation_id"] == "original-chat"


def test_http_429_is_not_retried_as_unknown_transport(monkeypatch):
    class RateLimited(UnknownPost):
        def post(self, url, headers, json):
            self.posts.append(json)
            return type("Response", (), {"status_code": 429, "headers": {}, "text": ""})()
    client = RateLimited(None)
    user, slug = prepared_post(monkeypatch, client)
    monkeypatch.setattr(ChatGPTWebOutbox, "_read_delivery_by_message_id", staticmethod(
        lambda *_a, **_k: pytest.fail("HTTP 429 is not an unknown transport outcome")))
    with pytest.raises(RuntimeError, match="HTTP 429"):
        ChatGPTWebOutbox()._send_web(chat_ref="original-chat", draft_id="d1",
            body="reply", account_ref=slug, user=user)
    assert len(client.posts) == 1
