"""Airlock's callback-only MCP path must preserve UserIO safety contracts."""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

from universal_userio.contracts import InboxMessage
from universal_userio.http_api import handler
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def suggest(self, **_kwargs):
        return "AI draft"


class Outbox:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send_reply(self, **kwargs):
        self.calls.append(kwargs)
        return "airlock-receipt"


def test_airlock_can_poll_then_draft_and_exactly_approve_over_mcp(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    outbox = Outbox()
    service = UserIOService(store, Generator(), outbox)
    conversation_id, _ = service.receive(
        InboxMessage(
            "telegram", "airlock-http-1", "anna", "Need a reply", 1.0
        ),
        route_id="telegram",
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), handler(service, token="airlock-token")
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    request_id = 0

    def rpc(method: str, params: dict) -> dict:
        nonlocal request_id
        request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        request = Request(
            f"http://127.0.0.1:{server.server_port}/mcp",
            data=json.dumps(payload).encode(),
            method="POST",
            headers={
                "Authorization": "Bearer airlock-token",
                "Content-Type": "application/json",
            },
        )
        with urlopen(request) as response:
            return json.loads(response.read())["result"]

    def call_tool(name: str, arguments: dict) -> dict:
        result = rpc("tools/call", {"name": name, "arguments": arguments})
        return result["structuredContent"]

    try:
        initialized = rpc(
            "initialize",
            {
                "protocolVersion": "2026-07-28",
                "clientInfo": {"name": "airlock-agent", "version": "0.7"},
                "capabilities": {},
            },
        )
        manifest = rpc("tools/list", {})
        polled = call_tool(
            "userio.workspace.poll", {"after": 0, "limit": 10}
        )
        caught_up = call_tool(
            "userio.workspace.poll",
            {"after": polled["cursor"], "limit": 10},
        )
        drafted = call_tool(
            "userio.channels.send_draft",
            {"chat_id": conversation_id, "text": "Exact reply"},
        )

        assert initialized["protocolVersion"] == "2026-07-28"
        assert "userio.workspace.poll" in {
            tool["name"] for tool in manifest["tools"]
        }
        assert polled["events"][0]["message_id"] == "airlock-http-1"
        assert caught_up["events"] == []
        assert outbox.calls == []
        assert drafted["sent"] is False
        assert drafted["approval_required"] is True

        draft = drafted["draft"]
        rejected = call_tool(
            "userio.draft.approve_send",
            {
                "draft_id": draft["id"],
                "confirm": True,
                "expected_text": "stale reply",
                "expected_chat_id": conversation_id,
                "expected_attachments": [],
            },
        )
        approved = call_tool(
            "userio.draft.approve_send",
            {
                "draft_id": draft["id"],
                "confirm": True,
                "expected_text": "Exact reply",
                "expected_chat_id": conversation_id,
                "expected_attachments": [],
            },
        )

        assert rejected == {"ok": False, "error": "draft_snapshot_conflict"}
        assert outbox.calls == [{
            "route_id": "telegram",
            "conversation_id": conversation_id,
            "draft_id": draft["id"],
            "body": "Exact reply",
        }]
        assert approved["sent"] is True
        assert approved["receipt"] == "airlock-receipt"
    finally:
        server.shutdown()
        server.server_close()
