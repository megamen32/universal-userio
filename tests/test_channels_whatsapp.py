"""Phase 4 tests: WhatsApp channel over the Baileys bridge HTTP API (offline)."""

from __future__ import annotations

import asyncio
import io
import json
import urllib.request

import pytest

from universal_userio.channels.core import AdapterNotSupported
from universal_userio.channels.whatsapp import (
    WhatsAppBridgeClient,
    WhatsAppChannel,
    _ref_id,
    normalize_chat_id,
)

INCOMING = {
    "key": {"id": "BRIDGE-1", "remoteJid": "79990001111@s.whatsapp.net", "fromMe": False},
    "message": {"conversation": "привет"},
}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


class FakeOpener:
    """Records requests and serves queued JSON responses."""

    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, str, dict | None]] = []

    def __call__(self, request: urllib.request.Request, timeout=None):
        body = request.data.decode() if request.data else None
        self.requests.append((request.method, request.full_url, json.loads(body) if body else None))
        payload = self.responses.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return FakeResponse(json.dumps(payload).encode())


def make_channel(responses: list[object]) -> tuple[WhatsAppChannel, FakeOpener]:
    opener = FakeOpener(responses)
    client = WhatsAppBridgeClient("http://127.0.0.1:30100", runner=opener)
    return WhatsAppChannel(client), opener


def test_send_builds_bridge_payload_and_maps_ids() -> None:
    channel, opener = make_channel([{"success": True, "messageId": "W-42", "messageIds": ["W-42"]}])

    sent = asyncio.run(channel.send_message("79990001111", "hello there"))

    assert sent.out is True
    assert sent.chat_id == "79990001111@s.whatsapp.net"
    assert sent.id == _ref_id("W-42")
    method, url, payload = opener.requests[0]
    assert method == "POST" and url.endswith("/send")
    assert payload == {"chatId": "79990001111@s.whatsapp.net", "message": "hello there"}


def test_inbox_drain_and_read_chat_roundtrip() -> None:
    channel, opener = make_channel([[INCOMING], [], []])

    chats = asyncio.run(channel.list_chats())
    assert [chat.id for chat in chats] == ["79990001111@s.whatsapp.net"]

    messages = asyncio.run(channel.read_chat("79990001111"))
    assert [m.text for m in messages] == ["привет"]
    assert messages[0].out is False

    single = asyncio.run(channel.read_message("79990001111", _ref_id("BRIDGE-1")))
    assert single is not None and single.text == "привет"


def test_edit_uses_remembered_bridge_id() -> None:
    channel, opener = make_channel(
        [
            {"success": True, "messageId": "W-7"},
            {"success": True, "messageIds": ["W-8"]},
        ]
    )
    sent = asyncio.run(channel.send_message("160881623204055@lid", "v1"))
    assert sent.chat_id == "160881623204055@lid"

    edited = asyncio.run(channel.edit_message("160881623204055@lid", sent, "v2"))
    assert edited.text == "v2"
    method, _, payload = opener.requests[-1]
    assert method == "POST" and payload["messageId"] == "W-7"


def test_reply_to_resolves_remembered_bridge_id() -> None:
    channel, opener = make_channel(
        [
            {"success": True, "messageId": "W-1"},
            [INCOMING],
            {"success": True, "messageId": "W-2"},
        ]
    )
    asyncio.run(channel.send_message("79990001111", "first"))
    incoming = asyncio.run(channel.read_chat("79990001111"))
    asyncio.run(channel.send_message("79990001111", "answer", reply_to=incoming[0].id))
    _, _, payload = opener.requests[-1]
    assert payload["replyTo"] == "BRIDGE-1"


def test_typing_context_manager_posts() -> None:
    channel, opener = make_channel([{"success": True}])

    async def scenario():
        async with channel.typing("79990001111"):
            pass

    asyncio.run(scenario())
    method, url, payload = opener.requests[0]
    assert method == "POST" and url.endswith("/typing")
    assert payload == {"chatId": "79990001111@s.whatsapp.net"}


def test_unsupported_operations_and_normalization() -> None:
    assert normalize_chat_id("+79990001111") == "79990001111@s.whatsapp.net"
    assert normalize_chat_id("160881623204055@lid") == "160881623204055@lid"

    channel, _ = make_channel([])
    with pytest.raises(AdapterNotSupported):
        asyncio.run(channel.delete_message("79990001111", 1))
    with pytest.raises(AdapterNotSupported):
        asyncio.run(channel.download_media("79990001111", 1))


def test_from_env_default_url() -> None:
    channel = WhatsAppChannel.from_env({})
    assert channel.platform == "whatsapp"
    assert channel.capabilities == frozenset({"read", "send", "edit", "typing"})
    assert channel._client.base_url == "http://127.0.0.1:30100"
    custom = WhatsAppChannel.from_env({"USERIO_WHATSAPP_BRIDGE_URL": "http://127.0.0.1:30110"})
    assert custom._client.base_url == "http://127.0.0.1:30110"
