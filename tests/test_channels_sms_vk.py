"""Phase 5 tests: SMS and VK channel wrappers (offline)."""

from __future__ import annotations

import asyncio

import pytest

from universal_userio.channels.core import AdapterNotSupported, stable_ref_id
from universal_userio.channels.sms import AndroidSmsChannel
from universal_userio.channels.vk import VkChannel
from universal_userio.contracts import InboxMessage


class FakeSmsGateway:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def inbound(self) -> list[InboxMessage]:
        return [
            InboxMessage("sms", "gw-1", "+79990002222", "first inbound", 1725312000.0),
            InboxMessage("sms", "gw-2", "+79990003333", "other peer", 1725312100.0),
        ]

    def send(self, *, to: str, body: str) -> str:
        self.sent.append((to, body))
        return "receipt-9"


def test_sms_channel_read_and_send() -> None:
    gateway = FakeSmsGateway()
    channel = AndroidSmsChannel(gateway)

    chats = asyncio.run(channel.list_chats())
    assert sorted(chat.id for chat in chats) == ["+79990002222", "+79990003333"]

    messages = asyncio.run(channel.read_chat("+79990002222"))
    assert [m.text for m in messages] == ["first inbound"]
    assert messages[0].id == stable_ref_id("gw-1")

    single = asyncio.run(channel.read_message("+79990002222", stable_ref_id("gw-1")))
    assert single is not None

    sent = asyncio.run(channel.send_message("+79990002222", "reply text"))
    assert sent.out is True
    assert sent.id == stable_ref_id("receipt-9")
    assert gateway.sent == [("79990002222", "reply text")]


def test_sms_unsupported_operations() -> None:
    channel = AndroidSmsChannel(FakeSmsGateway())
    with pytest.raises(AdapterNotSupported):
        asyncio.run(channel.edit_message("+79990002222", 1, "x"))
    with pytest.raises(AdapterNotSupported):
        channel.typing("+79990002222")


def test_sms_from_env_requires_credentials() -> None:
    with pytest.raises(ValueError, match="USERIO_SMS_GATEWAY_URL"):
        AndroidSmsChannel.from_env({})


VK_ROWS = [
    {"message_id": "vk-1", "sender": "peer-777", "sender_name": "Иван", "body": "вопрос по цене"},
    {"message_id": "vk-2", "sender": "peer-888", "body": "другой диалог"},
]


def test_vk_channel_reads_rows() -> None:
    channel = VkChannel(lambda: list(VK_ROWS))

    chats = asyncio.run(channel.list_chats())
    assert [(chat.id, chat.title) for chat in chats] == [("peer-777", "Иван"), ("peer-888", "peer-888")]

    messages = asyncio.run(channel.read_chat("peer-777"))
    assert [m.text for m in messages] == ["вопрос по цене"]
    assert messages[0].id == stable_ref_id("vk-1")


def test_vk_send_requires_sender_then_uses_it() -> None:
    read_only = VkChannel(lambda: [])
    with pytest.raises(AdapterNotSupported):
        asyncio.run(read_only.send_message("peer-777", "hi"))

    calls: list[tuple[str, str]] = []

    def sender(chat_id: str, text: str) -> str:
        calls.append((chat_id, text))
        return "draft-123"

    channel = VkChannel(lambda: [], sender)
    sent = asyncio.run(channel.send_message("peer-777", "ответ"))
    assert calls == [("peer-777", "ответ")]
    assert sent.out is True
    assert sent.id == stable_ref_id("draft-123")
