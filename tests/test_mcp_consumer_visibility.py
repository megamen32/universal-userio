from __future__ import annotations

import json

from universal_userio.contracts import InboxMessage
from universal_userio.mcp_surface import UserIOMcpSurface
from universal_userio.mcp_transport import json_rpc_response
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def suggest(self, **_kwargs):
        return "draft"


class Outbox:
    def send_reply(self, **_kwargs):
        return "receipt"


def test_consumer_exclusion_covers_all_mcp_read_paths(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    hidden_id, _ = service.receive(
        InboxMessage("telegram", "hermes-1", "Hermes", "own output", 1.0),
        route_id="telegram",
    )
    visible_id, _ = service.receive(
        InboxMessage("telegram", "anna-1", "Anna", "hello", 2.0),
        route_id="telegram",
    )
    surface = UserIOMcpSurface(store, service)
    ignored_chats = [hidden_id]

    chats = surface.dispatch(
        "userio.channels.list", {"channel": "telegram", "ignored_chats": ignored_chats},
    )["chats"]
    unread = surface.dispatch(
        "userio.inbox.list_new", {"ignored_chats": ignored_chats},
    )["messages"]
    hidden = surface.dispatch(
        "userio.conversation.get", {
            "conversation_id": hidden_id, "ignored_chats": ignored_chats,
        },
    )
    visible = surface.dispatch(
        "userio.channels.read", {
            "channel": "telegram", "chat_id": visible_id, "ignored_chats": ignored_chats,
        },
    )
    unread_resource = surface.read_resource("userio://inbox/unread")
    resource_payload = json.loads(unread_resource["contents"][0]["text"])

    assert {chat["id"] for chat in chats} == {visible_id}
    assert {message["conversation_id"] for message in unread} == {visible_id}
    assert hidden == {"ok": True, "conversation": None}
    assert visible["chat"]["id"] == visible_id
    assert {message["conversation_id"] for message in resource_payload["messages"]} == {
        hidden_id, visible_id,
    }

    hidden_read = json_rpc_response(surface, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "name": "userio.channels.read",
            "arguments": {
                "channel": "telegram", "chat_id": hidden_id, "ignored_chats": ignored_chats,
            },
        },
    })
    assert hidden_read["result"]["structuredContent"] == {"ok": False, "error": "chat not found"}

    assert {chat["id"] for chat in surface.dispatch(
        "userio.channels.list", {"channel": "telegram"},
    )["chats"]} == {hidden_id, visible_id}
