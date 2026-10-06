from __future__ import annotations

import asyncio
import json

import pytest

from universal_userio.adapters import UnifiedChannels
from universal_userio.channels.capabilities import channel_capabilities
from universal_userio.channels.max import MaxChannel
from universal_userio.channels.max_client import MaxClient, dispatch_to_message
from universal_userio.contracts import InboxMessage
from universal_userio.max_ingress import backfill
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def chats(self, *, count: int = 50):
        return [
            {"id": 9001, "type": "DIALOG", "title": "Безопасность", "newMessages": 2},
            {"id": 9002, "type": "GROUP", "title": "Команда", "newMessages": 0},
        ]

    def history(self, chat_id, *, backward: int = 30):
        return [
            {"id": 111, "sender": 777, "text": "Начать общаться просто", "time": 1781704393993},
            {"id": 112, "sender": 777, "text": "Код подтверждения", "time": 1781704493993},
        ]

    def send(self, *, chat_id, text: str):
        self.sent.append((str(chat_id), text))
        return {"chat_id": str(chat_id), "message_id": "113", "receipt": "max-ws-accepted"}


def test_max_channel_lists_chats_and_reads_history():
    channel = MaxChannel(FakeClient())
    chats = asyncio.run(channel.list_chats())
    assert [chat.id for chat in chats] == ["9001", "9002"]
    assert chats[0].title == "Безопасность"
    assert chats[0].kind == "dm" and chats[0].unread_count == 2
    assert chats[1].kind == "group"

    messages = asyncio.run(channel.read_chat("9001"))
    assert [m.text for m in messages] == ["Начать общаться просто", "Код подтверждения"]
    assert messages[0].sender_id == "777"
    assert messages[0].chat_id == "9001"


def test_max_channel_send_delivers_through_client():
    client = FakeClient()
    channel = MaxChannel(client)
    receipt = asyncio.run(channel.send_message("9001", "Ответ поддержки"))
    assert receipt.out is True
    assert client.sent == [("9001", "Ответ поддержки")]


def test_dispatch_to_message_extracts_known_shapes():
    message = dispatch_to_message(
        {"chatId": 9001, "message": {"id": 114, "sender": 777, "text": "Новое сообщение"}}
    )
    assert message == {
        "chat_id": "9001",
        "message_id": "114",
        "sender": "777",
        "text": "Новое сообщение",
        "time": None,
        "out": False,
    }


def test_dispatch_to_message_ignores_non_message_events():
    assert dispatch_to_message({"event": "typing", "chatId": 9001}) is None
    assert dispatch_to_message({"chatId": 9001, "message": {"id": 5, "text": ""}}) is None
    assert dispatch_to_message("not-a-dict") is None


def _frame(opcode: int, cmd: int = 1, payload: dict | None = None) -> str:
    return json.dumps({"ver": 11, "cmd": cmd, "seq": 1, "opcode": opcode, "payload": payload or {}})


class ScriptedTransport:
    """Feeds canned frames per opcode and records outgoing envelopes."""

    def __init__(self, replies: dict[int, str]) -> None:
        self.replies = replies
        self.sent: list[dict] = []

    def connect(self, timeout: float):
        class _FakeWs:
            connected = True

        return _FakeWs()

    def send(self, ws, text: str) -> None:
        self.sent.append(json.loads(text))

    def recv(self, ws, timeout: float):
        opcode = self.sent[-1]["opcode"]
        return json.loads(self.replies[opcode])

    def close(self, ws) -> None:
        return None


LOGIN_PAYLOAD = {
    "profile": {"contact": {"id": 9001, "names": [{"name": "Никита"}]}},
    "chats": [],
}


def test_max_client_roundtrips_protocol_frames():
    transport = ScriptedTransport(
        {
            6: _frame(6, payload={"lang": "ru", "phone-auth-enabled": True}),
            19: _frame(19, payload=LOGIN_PAYLOAD),
            53: _frame(53, payload={"chats": [{"id": 9001, "type": "DIALOG"}]}),
            49: _frame(49, payload={"messages": [{"id": 111, "text": "Привет", "sender": 777}]}),
            64: _frame(64, payload={"chatId": 9001, "message": {"id": 115, "text": "Ответ"}}),
        }
    )
    client = MaxClient("token-1", "device-1", transport=transport)
    login = client.connect()
    assert login["profile"]["contact"]["id"] == 9001

    assert [chat["id"] for chat in client.chats(count=5)] == [9001]
    assert transport.sent[-1]["opcode"] == 53
    assert transport.sent[-1]["payload"]["count"] == 5

    assert [row["id"] for row in client.history(9001, backward=10)] == [111]

    receipt = client.send(chat_id=9001, text="Ответ")
    assert receipt == {"chat_id": "9001", "message_id": "115", "receipt": "max-ws-accepted"}
    envelope = transport.sent[-1]
    assert envelope["ver"] == 11 and envelope["cmd"] == 0 and envelope["opcode"] == 64
    assert envelope["payload"]["chatId"] == 9001
    assert envelope["payload"]["message"]["text"] == "Ответ"


def test_max_client_error_frame_raises():
    transport = ScriptedTransport(
        {
            6: _frame(6, payload={"lang": "ru"}),
            19: _frame(19, cmd=3, payload={"error": "login.token"}),
        }
    )
    client = MaxClient("token-1", "device-1", transport=transport)
    with pytest.raises(RuntimeError, match="login.token"):
        client.connect()


def test_max_channel_from_env_requires_token(monkeypatch):
    monkeypatch.delenv("USERIO_MAX_TOKEN", raising=False)
    with pytest.raises(ValueError, match="USERIO_MAX_TOKEN"):
        MaxChannel.from_env({})


def test_max_capabilities_registered():
    assert channel_capabilities("max") == frozenset({"read", "send"})


class _Generator:
    def suggest(self, **_kwargs: object) -> str:
        return "draft"


class _Outbox:
    def send_reply(self, **_kwargs: object) -> str:
        return "unexpected"


class ServiceClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.profile = LOGIN_PAYLOAD


def _service(tmp_path, *, manual: bool = False):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite")
    client = ServiceClient()
    service = UserIOService(
        store, _Generator(), _Outbox(), max_client=client,
        max_user_id=store.default_user_id, max_route_id="max",
        max_manual_approve=manual,
    )
    service.receive_and_plan(
        InboxMessage("max", "max-9001-111", "9001", "Нужна помощь", 1_791_300_000.0),
        route_id="max",
    )
    return service, client


def test_max_message_flows_to_draft_and_approved_send(tmp_path):
    service, client = _service(tmp_path)
    channels = UnifiedChannels(service._store, service, service._store.default_user_id)

    chats = channels.adapter("max").list()
    assert len(chats) == 1
    assert chats[0]["channel"] == "max"
    assert chats[0]["last_message_snippet"] == "Нужна помощь"

    draft = channels.adapter("max").send(chat_id=chats[0]["id"], text="Помогаем.")
    approved = service.approve(draft.id, user_id=service._store.default_user_id)
    assert approved.status == "approved"
    # delivery goes to the MAX peer id, not the internal conversation id
    assert client.sent == [("9001", "Помогаем.")]


def test_max_manual_approve_keeps_auto_draft_locked(tmp_path):
    service, client = _service(tmp_path, manual=True)
    _, _accepted, draft = service.receive_and_plan(
        InboxMessage("max", "max-9001-222", "9001", "Ещё вопрос", 1_791_300_100.0),
        route_id="max",
    )
    assert draft is not None and draft.status == "proposed"
    assert client.sent == []

    approved = service.approve(draft.id, user_id=service._store.default_user_id)
    assert approved.status == "approved"
    assert client.sent == [("9001", "draft")]


class FakeSink:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def send_message(self, **kwargs) -> None:
        self.messages.append(kwargs)


def test_max_ingress_backfill_skips_own_and_empty_messages():
    class BackfillClient:
        profile = LOGIN_PAYLOAD  # own id 9001

        def chats(self, *, count: int = 50):
            return [{"id": 9001, "type": "DIALOG", "title": "Безопасность"}]

        def history(self, chat_id, *, backward: int = 30):
            return [
                {"id": 1, "text": "входящее", "sender": 777, "time": 1},
                {"id": 2, "text": "моё", "sender": 9001, "time": 2},
                {"id": 3, "text": "", "sender": 777, "time": 3},
            ]

    sink = FakeSink()
    delivered = backfill(sink, BackfillClient(), depth=10)  # type: ignore[arg-type]
    assert delivered == 1
    assert sink.messages[0]["source"] == "max"
    assert sink.messages[0]["sender"] == "777"
    assert sink.messages[0]["body"] == "входящее"
    assert sink.messages[0]["message_id"] == "max-9001-1"
