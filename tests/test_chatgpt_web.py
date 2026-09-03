"""Tests for the headless cookie-based ChatGPT web channel adapter."""

from __future__ import annotations

import json

import pytest

from universal_userio.adapters import AdapterNotSupported, ChatGPTWebChannelAdapter
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | str) -> None:
        self.status_code = status_code
        self.text = payload if isinstance(payload, str) else json.dumps(payload)


class FakeClient:
    """Scripted curl_cffi stand-in: maps URL fragments to responses."""

    def __init__(self, routes: list[tuple[str, int, dict | str]]) -> None:
        self.routes = list(routes)
        self.calls: list[str] = []
        self.cookie_values: dict[str, str] = {}
        self.cookies = self

    def set(self, name: str, value: str, *, domain: str = "") -> None:
        self.cookie_values[name] = value

    def get(self, url: str, headers: dict | None = None):
        self.calls.append(url)
        for index, (fragment, status, payload) in enumerate(self.routes):
            if fragment in url:
                if index == 0 and "auth/session" not in url:
                    continue  # first route is always the session mint
                return FakeResponse(status, payload)
        return FakeResponse(404, {"error": "no route"})

    def __call__(self):  # client_factory hook
        return self


def session_file(tmp_path, token: str = "st123") -> str:
    path = tmp_path / "chatgpt-session.json"
    path.write_text(json.dumps({"session_token": token}), encoding="utf-8")
    return str(path)


SESSION_PAYLOAD = {"accessToken": "eyJ.a.fresh", "expires": "2026-12-02T00:55:48.470Z"}
CONVERSATION_PAYLOAD = {
    "title": "Что такое SFU",
    "mapping": {
        "a": {"message": {"author": {"role": "system"}, "content": {"parts": ["hidden"]}, "create_time": 1}},
        "b": {"message": {"author": {"role": "user"}, "content": {"parts": ["Что такое sfu?"], "create_time": 2}},
              "create_time": 2},
        "c": {"message": {"author": {"role": "assistant"},
                          "content": {"parts": ["SFU — Selective Forwarding Unit."]}, "create_time": 3}},
    },
}


def make_adapter(tmp_path, monkeypatch, routes):
    session_path = session_file(tmp_path)
    monkeypatch.setenv(ChatGPTWebChannelAdapter.SESSION_ENV, session_path)
    client = FakeClient(routes)
    adapter = ChatGPTWebChannelAdapter(
        SQLiteUserIOStore(tmp_path / "userio.sqlite3"), DummyService(), "user-1",
        client_factory=client,
    )
    return adapter, client


class DummyService:
    def create_manual_draft(self, conversation_id: str, *, body: str, user_id: str):
        return type("D", (), {"id": "d1", "conversation_id": conversation_id, "body": body, "status": "proposed"})()

    def receive(self, message, *, route_id: str, user_id: str):
        return ("conv_1", True)


def test_list_mints_token_and_parses_items(tmp_path, monkeypatch) -> None:
    routes = [
        ("auth/session", 200, SESSION_PAYLOAD),
        ("backend-api/conversations", 200, {"items": [
            {"id": "chat-1", "title": "Что такое SFU", "update_time": 1},
            {"id": "chat-2", "title": "Время поездки", "update_time": 2},
        ]}),
    ]
    adapter, client = make_adapter(tmp_path, monkeypatch, routes)
    chats = adapter.list(limit=5)
    assert [c["id"] for c in chats] == ["chat-1", "chat-2"]
    assert all(c["channel"] == "chatgpt" for c in chats)
    assert any("auth/session" in u for u in client.calls)
    assert any("backend-api/conversations" in u for u in client.calls)


def test_read_maps_conversation_tree(tmp_path, monkeypatch) -> None:
    routes = [
        ("auth/session", 200, SESSION_PAYLOAD),
        ("backend-api/conversation/chat-1", 200, CONVERSATION_PAYLOAD),
    ]
    adapter, client = make_adapter(tmp_path, monkeypatch, routes)
    chat = adapter.read(chat_id="chat-1")["chat"]
    assert chat["title"] == "Что такое SFU"
    assert [m["role"] for m in chat["messages"]] == ["user", "assistant"]
    assert chat["messages"][0]["text"] == "Что такое sfu?"


def test_unconfigured_channel_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(ChatGPTWebChannelAdapter.SESSION_ENV, raising=False)
    adapter = ChatGPTWebChannelAdapter(SQLiteUserIOStore(tmp_path / "s.db"), DummyService(), "user-1")
    with pytest.raises(AdapterNotSupported, match="not configured"):
        adapter.list()


def test_missing_session_file_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(ChatGPTWebChannelAdapter.SESSION_ENV, str(tmp_path / "absent.json"))
    adapter = ChatGPTWebChannelAdapter(SQLiteUserIOStore(tmp_path / "s.db"), DummyService(), "user-1")
    with pytest.raises(AdapterNotSupported, match="missing"):
        adapter.list()


def test_rejected_session_cookie_raises(tmp_path, monkeypatch) -> None:
    routes = [("auth/session", 401, {"error": "unauthorized"})]
    adapter, _ = make_adapter(tmp_path, monkeypatch, routes)
    with pytest.raises(RuntimeError, match="HTTP 401"):
        adapter.list()


def test_send_anchors_draft_in_store(tmp_path, monkeypatch) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    routes = [
        ("auth/session", 200, SESSION_PAYLOAD),
        ("backend-api/conversation/chat-1", 200, CONVERSATION_PAYLOAD),
    ]
    session_path = session_file(tmp_path)
    monkeypatch.setenv(ChatGPTWebChannelAdapter.SESSION_ENV, session_path)
    client = FakeClient(routes)

    class Service(DummyService):
        received: list = []

        def receive(self, message, *, route_id: str, user_id: str):
            Service.received.append({
                "message_id": message.message_id, "sender": message.sender,
                "body": message.body, "route_id": route_id, "user_id": user_id,
            })
            return ("conv_chat1", True)

        def create_manual_draft(self, conversation_id: str, *, body: str, user_id: str):
            return type("D", (), {"id": "d2", "conversation_id": conversation_id, "body": body, "status": "proposed"})()

    adapter = ChatGPTWebChannelAdapter(store, Service(), "user-1", client_factory=client)
    draft = adapter.send(chat_id="chat-1", text="draft answer")
    assert draft.status == "proposed"
    assert Service.received[0]["body"].startswith("SFU")
    assert Service.received[0]["route_id"] == "chatgpt"
