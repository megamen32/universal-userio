"""WhatsApp outbound delivery: approved drafts go through the bridge /send API."""

from __future__ import annotations

import io
import json
import urllib.request

from universal_userio.adapters import UnifiedChannels
from universal_userio.channels.whatsapp import WhatsAppBridgeClient
from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def suggest(self, **_kwargs: object) -> str:
        return "draft"


class Outbox:
    def send_reply(self, **_kwargs: object) -> str:
        return "unexpected"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


class RecordingRunner:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout=None):
        self.requests.append(request)
        return FakeResponse(json.dumps(self.payload).encode())


def test_whatsapp_approved_draft_sends_through_bridge(tmp_path) -> None:
    runner = RecordingRunner({"ok": True, "messageId": "3EB0-W-1"})
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite")
    service = UserIOService(
        store, Generator(), Outbox(),
        whatsapp_outbox=WhatsAppBridgeClient(
            "http://127.0.0.1:18096", runner=runner, token="secret-token"
        ),
    )
    message = InboxMessage(
        "whatsapp", "account-3:W-1", "79990001111@s.whatsapp.net", "hello", 1_700_000_000.0
    )
    service.receive(message, route_id="whatsapp")

    channels = UnifiedChannels(store, service, store.default_user_id)
    chats = channels.adapter("whatsapp").list()
    assert [chat["title"] for chat in chats] == ["79990001111@s.whatsapp.net"]

    draft = channels.adapter("whatsapp").send(chat_id=chats[0]["id"], text="We can help.")
    approved = service.approve(draft.id, user_id=store.default_user_id)
    assert approved.status == "approved"

    assert len(runner.requests) == 1
    sent = runner.requests[0]
    assert sent.full_url.endswith("/send")
    assert sent.get_header("Authorization") == "Bearer secret-token"
    assert json.loads(sent.data.decode()) == {
        "chatId": "79990001111@s.whatsapp.net",
        "message": "We can help.",
    }


def test_client_token_defaults_from_env(monkeypatch) -> None:
    monkeypatch.setenv("USERIO_API_TOKEN", "env-token")
    runner = RecordingRunner({"ok": True, "messageId": "3EB0-W-2"})
    client = WhatsAppBridgeClient("http://127.0.0.1:30100", runner=runner)

    assert client.send(chat_id="79990001111@s.whatsapp.net", text="ping") == "3EB0-W-2"
    assert runner.requests[0].get_header("Authorization") == "Bearer env-token"
