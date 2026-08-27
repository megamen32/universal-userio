from __future__ import annotations

from io import StringIO

from universal_userio.contracts import InboxMessage
from universal_userio.mcp_surface import UserIOMcpSurface
from universal_userio.mcp_transport import StdioJsonRpcTransport
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def suggest(self, **_kwargs): return "AI draft"


class Outbox:
    def __init__(self): self.calls = []
    def send_reply(self, **kwargs): self.calls.append(kwargs); return "receipt"


def test_mcp_reads_edits_and_sends_only_after_exact_confirmation(tmp_path) -> None:
    store, outbox = SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Outbox()
    service = UserIOService(store, Generator(), outbox)
    message = InboxMessage("telegram", "1", "anna", "hello", 1.0)
    conversation_id, _ = service.receive(message, route_id="telegram-reply")
    surface = UserIOMcpSurface(store, service)

    listed = surface.dispatch("userio.inbox.list_new", {})
    draft = surface.dispatch("userio.draft.create", {"conversation_id": conversation_id, "body": "manual reply"})["draft"]
    edited = surface.dispatch("userio.draft.update", {"draft_id": draft["id"], "body": "edited reply"})
    denied = surface.dispatch("userio.draft.approve_send", {"draft_id": draft["id"], "confirm": False})
    sent = surface.dispatch("userio.draft.approve_send", {"draft_id": draft["id"], "confirm": True})

    assert listed["messages"][0]["sender"] == "anna"
    assert edited["draft"]["body"] == "edited reply"
    assert denied["error"] == "exact_confirmation_required"
    assert sent["draft"]["status"] == "approved"
    assert outbox.calls[0]["body"] == "edited reply"


def test_mcp_transport_advertises_tools_and_local_delete_is_explicit(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    conversation_id, _ = service.receive(InboxMessage("vk", "1", "anna", "hello", 1.0), route_id="vk")
    input_stream = StringIO('{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n')
    output_stream = StringIO()
    StdioJsonRpcTransport(UserIOMcpSurface(store, service), input_stream, output_stream).serve()

    assert "userio.draft.approve_send" in output_stream.getvalue()
    assert UserIOMcpSurface(store, service).dispatch("userio.conversation.delete_local", {"conversation_id": conversation_id, "confirm": True})["scope"] == "local_userio_only"
    assert store.conversation(conversation_id) is None


def test_mcp_tool_call_uses_standard_content_result(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    input_stream = StringIO(
        '{"jsonrpc":"2.0","id":1,"method":"tools/call",'
        '"params":{"name":"userio.channels.list","arguments":{}}}\n'
    )
    output_stream = StringIO()

    StdioJsonRpcTransport(UserIOMcpSurface(store, service), input_stream, output_stream).serve()

    result = __import__("json").loads(output_stream.getvalue())["result"]
    assert result["content"][0]["type"] == "text"
    assert result["structuredContent"] == {"ok": True, "chats": []}
