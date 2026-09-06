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


def test_mcp2_negotiates_modern_protocol_and_exposes_resources(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    conversation_id, _ = service.receive(
        InboxMessage("telegram", "mcp2-1", "anna", "modern hello", 1.0), route_id="telegram"
    )
    surface = UserIOMcpSurface(store, service)

    from universal_userio.mcp_transport import json_rpc_response
    initialized = json_rpc_response(surface, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2026-07-28", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
    })
    assert initialized["result"]["protocolVersion"] == "2026-07-28"
    assert "resources" in initialized["result"]["capabilities"]

    resources = json_rpc_response(surface, {"jsonrpc": "2.0", "id": 2, "method": "resources/list", "params": {}})
    assert "userio://inbox/unread" in [item["uri"] for item in resources["result"]["resources"]]
    templates = json_rpc_response(surface, {"jsonrpc": "2.0", "id": 3, "method": "resources/templates/list", "params": {}})
    assert templates["result"]["resourceTemplates"][0]["uriTemplate"] == "userio://conversations/{conversationId}"

    read = json_rpc_response(surface, {
        "jsonrpc": "2.0", "id": 4, "method": "resources/read",
        "params": {"uri": f"userio://conversations/{conversation_id}"},
    })
    payload = __import__("json").loads(read["result"]["contents"][0]["text"] )
    assert payload["conversation"]["messages"][0]["body"] == "modern hello"


def test_mcp2_resource_subscription_receives_update(tmp_path) -> None:
    from universal_userio.mcp_transport import ResourceSubscriptionHub, json_rpc_response
    from universal_userio.contracts import UserPrincipal
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    surface = UserIOMcpSurface(store, service)
    hub = ResourceSubscriptionHub()
    principal = UserPrincipal(store.default_user_id, "owner", "owner")
    subscribed = json_rpc_response(surface, {"jsonrpc":"2.0","id":1,"method":"resources/subscribe","params":{"uri":"userio://inbox/unread"}}, principal=principal, subscription_hub=hub)
    assert subscribed["result"] == {}
    service.add_inbound_listener(lambda user_id, _cid, _msg: hub.publish(user_id, "userio://inbox/unread"))
    service.receive(InboxMessage("telegram","sub-1","anna","wake me",1.0), route_id="telegram")
    event = hub.wait(store.default_user_id, timeout=0.1)
    assert event["method"] == "notifications/resources/updated"
    assert event["params"]["uri"] == "userio://inbox/unread"


def test_send_tool_visibility_is_per_user(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    surface = UserIOMcpSurface(store, service)
    user, _ = store.create_user("reader_user", "reader-password")
    store.set_user_preference("send_enabled", "0", user_id=user.user_id)
    owner = store.owner()
    owner_tools = {t["name"] for t in surface.dispatch("tools/list", {}, principal=owner)["tools"]}
    reader_tools = {t["name"] for t in surface.dispatch("tools/list", {}, principal=user)["tools"]}
    assert "userio.draft.approve_send" in owner_tools
    assert "userio.draft.approve_send" not in reader_tools


def test_mcp2_resource_unsubscribe_stops_updates(tmp_path) -> None:
    from universal_userio.mcp_transport import ResourceSubscriptionHub, json_rpc_response
    from universal_userio.contracts import UserPrincipal
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    surface = UserIOMcpSurface(store, UserIOService(store, Generator(), Outbox()))
    hub = ResourceSubscriptionHub()
    principal = UserPrincipal(store.default_user_id, "owner", "owner")
    for method in ("resources/subscribe", "resources/unsubscribe"):
        response = json_rpc_response(surface, {"jsonrpc":"2.0","id":1,"method":method,"params":{"uri":"userio://inbox/unread"}}, principal=principal, subscription_hub=hub)
        assert response["result"] == {}
    hub.publish(store.default_user_id, "userio://inbox/unread")
    assert hub.wait(store.default_user_id, timeout=0.01) is None


def test_mcp_user_capabilities_filter_tools_resources_and_subscribe(tmp_path) -> None:
    from universal_userio.mcp_transport import ResourceSubscriptionHub, json_rpc_response
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    user, _ = store.create_user("limited_user", "limited-password")
    surface = UserIOMcpSurface(store, service)
    hub = ResourceSubscriptionHub()

    store.set_user_capability("download", False, user_id=user.user_id)
    store.set_user_capability("send", False, user_id=user.user_id)
    tools = {item["name"] for item in surface.dispatch("tools/list", {}, principal=user)["tools"]}
    assert "userio.channels.read" in tools
    assert "userio.channels.download" not in tools
    assert "userio.draft.approve_send" not in tools
    assert "userio.draft.create" not in tools

    store.set_user_capability("subscribe", False, user_id=user.user_id)
    init = json_rpc_response(surface, {
        "jsonrpc":"2.0", "id":1, "method":"initialize",
        "params":{"protocolVersion":"2026-07-28"},
    }, principal=user, subscription_hub=hub)
    assert init["result"]["capabilities"]["resources"]["subscribe"] is False
    denied = json_rpc_response(surface, {
        "jsonrpc":"2.0", "id":2, "method":"resources/subscribe",
        "params":{"uri":"userio://inbox/unread"},
    }, principal=user, subscription_hub=hub)
    assert denied["error"]["message"] == "subscribe capability disabled"

    store.set_user_capability("read", False, user_id=user.user_id)
    assert surface.resource_manifest(principal=user)["resources"] == []
    tools = {item["name"] for item in surface.dispatch("tools/list", {}, principal=user)["tools"]}
    assert "userio.channels.read" not in tools
    assert "userio.accounts.list" not in tools


def test_matrix_is_a_first_class_userio_channel(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    conversation_id, _ = service.receive(InboxMessage("matrix", "mx-1", "@anna:example.org", "matrix hello", 1.0), route_id="matrix")
    surface = UserIOMcpSurface(store, service)
    listed = surface.dispatch("userio.channels.list", {"channel":"matrix"})
    assert listed["ok"] is True
    assert listed["chats"][0]["id"] == conversation_id
    read = surface.dispatch("userio.channels.read", {"channel":"matrix", "chat_id":conversation_id})
    assert read["chat"]["messages"][0]["body"] == "matrix hello"
