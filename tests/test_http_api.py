from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from universal_userio.contracts import InboxMessage
from universal_userio.http_api import handler
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def suggest(self, *, conversation_id: str, latest_message: InboxMessage) -> str:
        return "draft for " + latest_message.body


class Outbox:
    def __init__(self) -> None:
        self.calls = []

    def send_reply(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return "event_1"


def test_http_business_path_requires_auth_and_only_sends_after_approval(tmp_path) -> None:
    outbox = Outbox()
    service = UserIOService(SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Generator(), outbox)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="test-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        payload = {"route_id": "telegram-reply", "message": {"schema": "universal.inbox.message.v1", "source": "telegram", "message_id": "1", "sender": "chat", "body": "hello"}}
        request = Request(base + "/v1/messages", data=json.dumps(payload).encode(), method="POST", headers={"Content-Type": "application/json"})
        try:
            urlopen(request)
        except HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("unauthenticated ingress was accepted")

        request.add_header("Authorization", "Bearer test-token")
        with urlopen(request) as response:
            accepted = json.loads(response.read())
        assert accepted["accepted"] is True
        assert outbox.calls == []

        approve = Request(base + f"/v1/drafts/{accepted['draft']['id']}/approve", data=b"{}", method="POST", headers={"Authorization": "Bearer test-token"})
        with urlopen(approve) as response:
            assert json.loads(response.read())["status"] == "approved"
        assert outbox.calls[0]["route_id"] == "telegram-reply"
    finally:
        server.shutdown()
        server.server_close()


def test_http_control_plane_applies_identity_rule_and_lists_new_messages(tmp_path) -> None:
    outbox = Outbox()
    service = UserIOService(SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Generator(), outbox)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="test-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def post(path: str, body: dict) -> dict:
        request = Request(base + path, data=json.dumps(body).encode(), method="POST", headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"})
        with urlopen(request) as response:
            return json.loads(response.read())

    try:
        assert post("/v1/accounts", {"id": "vk-sales", "provider": "vk", "display_name": "Sales VK", "can_read": True, "can_reply": True, "credential_ref": "secret://userio/vk-sales"})["accepted"] is True
        accounts = Request(base + "/v1/accounts", headers={"Authorization": "Bearer test-token"})
        with urlopen(accounts) as response:
            assert json.loads(response.read())["accounts"][0]["capabilities"] == ["read", "reply"]
        assert post("/v1/identities", {"source": "vk", "external_id": "anna-vk", "identity_id": "person_anna", "display_name": "Anna"})["accepted"] is True
        assert post("/v1/reply-rules", {"identity_id": "person_anna", "source": "vk", "route_id": "vip-vk", "mode": "auto_send"})["accepted"] is True
        received = post("/v1/messages", {"route_id": "ordinary-vk", "message": {"schema": "universal.inbox.message.v1", "source": "vk", "message_id": "1", "sender": "anna-vk", "body": "help"}})

        assert received["draft"]["status"] == "approved"
        assert outbox.calls[0]["route_id"] == "vip-vk"
        inbox = Request(base + "/v1/inbox", headers={"Authorization": "Bearer test-token"})
        with urlopen(inbox) as response:
            assert json.loads(response.read())["messages"][0]["identity_id"] == "person_anna"
    finally:
        server.shutdown()
        server.server_close()


def test_dashboard_is_a_public_shell_but_message_data_remains_token_protected(tmp_path) -> None:
    service = UserIOService(SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Generator(), Outbox())
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="test-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/") as response:
            assert b"Universal UserIO" in response.read()
        try:
            urlopen(f"http://127.0.0.1:{server.server_port}/v1/inbox")
        except HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("dashboard API data leaked without token")
    finally:
        server.shutdown()
        server.server_close()
