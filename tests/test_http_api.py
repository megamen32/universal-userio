from __future__ import annotations

import json
import re
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
        assert accepted["draft"] is None
        assert outbox.calls == []

        propose = Request(base + f"/v1/conversations/{accepted['conversation_id']}/ai-drafts", data=b"{}", method="POST", headers={"Authorization": "Bearer test-token"})
        with urlopen(propose) as response:
            draft = json.loads(response.read())["drafts"][0]

        manual = Request(base + f"/v1/conversations/{accepted['conversation_id']}/drafts", data=b'{"body":"manual answer"}', method="POST", headers={"Authorization": "Bearer test-token"})
        with urlopen(manual) as response:
            assert json.loads(response.read())["body"] == "manual answer"

        approve = Request(base + f"/v1/drafts/{draft['id']}/approve", data=b"{}", method="POST", headers={"Authorization": "Bearer test-token"})
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
        conversations = Request(base + "/v1/conversations?source=vk", headers={"Authorization": "Bearer test-token"})
        with urlopen(conversations) as response:
            assert json.loads(response.read())["conversations"][0]["unread_count"] == 1
    finally:
        server.shutdown()
        server.server_close()


def test_email_messages_share_one_case_insensitive_conversation(tmp_path) -> None:
    service = UserIOService(SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Generator(), Outbox())
    first = InboxMessage("email", "1", "Anna@Example.com", "first", 1.0)
    second = InboxMessage("gmail", "2", "anna@example.com", "second", 2.0)
    first_id, _ = service.receive(first, route_id="email-reply")
    second_id, _ = service.receive(second, route_id="email-reply")
    assert first_id == second_id


def test_gmail_account_lane_is_accepted_and_groups_by_sender(tmp_path) -> None:
    service = UserIOService(SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Generator(), Outbox())
    first = InboxMessage("gmail", "1", "Anna@Example.com", "first", 1.0)
    second = InboxMessage("gmail:careviolan", "2", "anna@example.com", "second", 2.0)
    first_id, _ = service.receive(first, route_id="gmail-read-only")
    second_id, _ = service.receive(second, route_id="gmail-read-only")
    assert first_id == second_id


def test_dashboard_is_a_public_shell_but_message_data_remains_token_protected(tmp_path) -> None:
    service = UserIOService(SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Generator(), Outbox())
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="test-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/") as response:
            page = response.read()
        assert b"Universal UserIO" in page
        asset = re.search(rb'/assets/[^" ]+\.js', page)
        assert asset is not None
        with urlopen(f"http://127.0.0.1:{server.server_port}" + asset.group().decode()) as response:
            assert response.headers["Content-Type"] == "text/javascript"
        try:
            urlopen(f"http://127.0.0.1:{server.server_port}/v1/inbox")
        except HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("dashboard API data leaked without token")
    finally:
        server.shutdown()
        server.server_close()


def test_http_mcp_surface_is_bearer_protected_and_advertises_userio_tools(tmp_path) -> None:
    service = UserIOService(SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Generator(), Outbox())
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="test-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(f"http://127.0.0.1:{server.server_port}/mcp", data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}', method="POST", headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"})
        with urlopen(request) as response:
            result = json.loads(response.read())["result"]
        assert "userio.draft.approve_send" in [tool["name"] for tool in result["tools"]]
    finally:
        server.shutdown()
        server.server_close()


def test_trusted_loopback_proxy_can_use_dashboard_api_but_not_mcp(tmp_path) -> None:
    service = UserIOService(SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Generator(), Outbox())
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        handler(service, token="test-token", trusted_proxy_token="proxy-secret"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        proxy_headers = {
            "X-UserIO-Authenticated": "1", "X-UserIO-Proxy-Token": "proxy-secret",
        }
        inbox = Request(
            f"http://127.0.0.1:{server.server_port}/v1/inbox", headers=proxy_headers
        )
        with urlopen(inbox) as response:
            assert json.loads(response.read()) == {"messages": []}
        mcp = Request(
            f"http://127.0.0.1:{server.server_port}/mcp",
            data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
            method="POST", headers=proxy_headers,
        )
        try:
            urlopen(mcp)
        except HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("proxy header bypassed MCP bearer authentication")
    finally:
        server.shutdown()
        server.server_close()


def test_vk_identity_connect_does_not_claim_message_capabilities(tmp_path) -> None:
    service = UserIOService(SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Generator(), Outbox())
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        handler(
            service, token="test-token", vkid_app_id="54729441",
            trusted_proxy_token="proxy-secret",
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        page = Request(base + "/vk/connect/new", headers={"X-UserIO-Authenticated": "1"})
        with urlopen(page) as response:
            assert response.status == 200
            assert b"54729441" in response.read()
        request = Request(
            base + "/v1/vk/accounts", data=b'{"user_id":"42","display_name":"VK Person"}',
            method="POST", headers={
                "X-UserIO-Authenticated": "1", "X-UserIO-Proxy-Token": "proxy-secret",
                "Content-Type": "application/json",
            },
        )
        with urlopen(request) as response:
            result = json.loads(response.read())
        assert result["mode"] == "vkid_identity_only"
        assert service._store.accounts()[0]["capabilities"] == []
    finally:
        server.shutdown()
        server.server_close()


def test_accounts_can_be_removed_without_deleting_provider_data(tmp_path) -> None:
    service = UserIOService(SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Generator(), Outbox())
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="test-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        request = Request(base + "/v1/accounts", data=b'{"id":"gmail:old","provider":"gmail:old","display_name":"Old","credential_ref":"gmail:old"}', method="POST", headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"})
        with urlopen(request):
            pass
        request = Request(base + "/v1/accounts/gmail%3Aold", method="DELETE", headers={"Authorization": "Bearer test-token"})
        with urlopen(request) as response:
            assert json.loads(response.read())["deleted"] is True
        assert service._store.accounts() == []
    finally:
        server.shutdown()
        server.server_close()
