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


def test_conversations_preview_falls_back_to_last_text_when_latest_is_attachment(tmp_path) -> None:
    """When the latest message is an attachment placeholder, the chat list and
    search results must surface the most recent real text body instead. This is
    what made "догов" miss `+79103332444` in the Marat review."""
    service = UserIOService(SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Generator(), Outbox())
    route_id = "tg-reply"
    cid, _ = service.receive(
        InboxMessage("telegram", "1", "client", "И приложите договор пожалуйста", 100.0),
        route_id=route_id,
    )
    service.receive(
        InboxMessage("telegram", "2", "client", "[Telegram document]", 200.0),
        route_id=route_id,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="test-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        request = Request(base + "/v1/conversations", headers={"Authorization": "Bearer test-token"})
        with urlopen(request) as response:
            payload = json.loads(response.read())
        conversation = payload["conversations"][0]
        assert conversation["preview"] == "И приложите договор пожалуйста", (
            f"placeholder leaked into preview: {conversation['preview']!r}"
        )
    finally:
        server.shutdown()
        server.server_close()


def test_conversations_are_sorted_by_latest_message_not_conversation_updated_at(tmp_path) -> None:
    """An older conversation that just received a fresh message must rank above
    a conversation that was merely re-tagged without new activity."""
    service = UserIOService(SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Generator(), Outbox())
    fresh, _ = service.receive(
        InboxMessage("telegram", "fresh", "x", "свежее сообщение", 1000.0), route_id="tg-reply",
    )
    stale, _ = service.receive(
        InboxMessage("telegram", "stale", "y", "старое сообщение", 100.0), route_id="tg-reply",
    )
    # Add a draft on the older conversation so its updated_at jumps ahead of
    # the newer conversation in the legacy sort; the new contract must still
    # rank by last_at and keep `fresh` first.
    from universal_userio.contracts import ReplyDraft
    service._store.add_draft(
        ReplyDraft(id="draft-touch", conversation_id=stale, body="touch", status="proposed"),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="test-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        request = Request(base + "/v1/conversations", headers={"Authorization": "Bearer test-token"})
        with urlopen(request) as response:
            payload = json.loads(response.read())
        assert [c["id"] for c in payload["conversations"]] == [fresh, stale], payload
    finally:
        server.shutdown()
        server.server_close()


def test_conversation_media_endpoint_describes_attachment_placeholder(tmp_path) -> None:
    """The /media endpoint pins the contract for attachment bubbles: every
    adapter currently reports `available=false` because StoredChannelAdapter
    does not implement download. The frontend relies on this contract to
    render an honest "not yet wired" modal instead of a silently dead click."""
    service = UserIOService(SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Generator(), Outbox())
    cid, _ = service.receive(
        InboxMessage("telegram", "doc-1", "client", "[Telegram document]", 1.0), route_id="tg-reply",
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="test-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        request = Request(
            base + f"/v1/conversations/{cid}/media/doc-1",
            headers={"Authorization": "Bearer test-token"},
        )
        with urlopen(request) as response:
            payload = json.loads(response.read())
        assert payload["kind"] == "document", payload
        assert payload["available"] is False
        assert payload["reason"], "expected a non-empty reason when media is unavailable"
    finally:
        server.shutdown()
        server.server_close()


def test_conversation_media_endpoint_rejects_unknown_message(tmp_path) -> None:
    service = UserIOService(SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Generator(), Outbox())
    cid, _ = service.receive(
        InboxMessage("telegram", "x", "client", "hi", 1.0), route_id="tg-reply",
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="test-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        request = Request(
            base + f"/v1/conversations/{cid}/media/missing",
            headers={"Authorization": "Bearer test-token"},
        )
        try:
            urlopen(request)
        except HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("expected 404 for unknown message_id")
    finally:
        server.shutdown()
        server.server_close()


def test_conversation_media_endpoint_streams_real_bytes_for_email(tmp_path) -> None:
    """With a fake email channel injected into MailChannelAdapter, the
    /media and /media/raw endpoints must surface the real filename,
    content_type and bytes — proving the contract works end to end."""
    from universal_userio.adapters import MailChannelAdapter
    from universal_userio.channels.core import ChatRef, DownloadedMedia, MessageRef

    class FakeEmail:
        async def download_media(self, *, chat: ChatRef, message: MessageRef) -> DownloadedMedia:
            return DownloadedMedia(
                chat_id=chat, message_id=int(message),
                data=b"%PDF-1.4 demo", mime_type="application/pdf", filename="invoice.pdf",
            )

    service = UserIOService(SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Generator(), Outbox())
    cid, _ = service.receive(
        InboxMessage("gmail", "1042", "billing@example.com", "[Gmail document]", 1.0),
        route_id="gmail",
    )
    service._mail_adapter_factory = lambda: FakeEmail()  # opaque injection point
    # Patch the adapter picker so the test can route through the fake.
    from universal_userio import http_api as api
    original = api._adapter_for_message
    def patched(service, message, user_id):
        adapter = MailChannelAdapter(
            service._store, service, user_id, channel_factory=service._mail_adapter_factory,
        )
        return adapter
    api._adapter_for_message = patched
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="test-token"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            meta_request = Request(
                base + f"/v1/conversations/{cid}/media/1042",
                headers={"Authorization": "Bearer test-token"},
            )
            with urlopen(meta_request) as response:
                meta = json.loads(response.read())
            assert meta["available"] is True, meta
            assert meta["filename"] == "invoice.pdf"
            assert meta["content_type"] == "application/pdf"
            assert meta["size"] == len(b"%PDF-1.4 demo")
            assert meta["download_url"].endswith("/raw")

            raw_request = Request(
                base + f"/v1/conversations/{cid}/media/1042/raw",
                headers={"Authorization": "Bearer test-token"},
            )
            with urlopen(raw_request) as response:
                body = response.read()
                content_type = response.headers.get("Content-Type")
                disposition = response.headers.get("Content-Disposition")
            assert body == b"%PDF-1.4 demo"
            assert content_type == "application/pdf"
            assert "invoice.pdf" in (disposition or "")
        finally:
            server.shutdown()
            server.server_close()
    finally:
        api._adapter_for_message = original


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


def test_dashboard_redirects_to_login_and_message_data_remains_protected(tmp_path) -> None:
    service = UserIOService(SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Generator(), Outbox())
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="test-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/") as response:
            page = response.read()
            assert response.geturl().endswith("/login")
        assert b"Universal UserIO" in page
        assert b'name="username"' in page
        assert b'name="password"' in page
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
        page = Request(base + "/vk/connect/new", headers={
            "X-UserIO-Authenticated": "1", "X-UserIO-Proxy-Token": "proxy-secret",
        })
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
