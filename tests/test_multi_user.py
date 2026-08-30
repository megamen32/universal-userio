from __future__ import annotations

import json
import sqlite3
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

import pytest

from universal_userio.adapters import (
    AdapterNotSupported,
    ChatGPTCDPChannelAdapter,
    MailChannelAdapter,
    TelegramChannelAdapter,
    VKChannelAdapter,
    WhatsAppChannelAdapter,
)
from universal_userio.contracts import InboxMessage
from universal_userio.http_api import handler
from universal_userio.mcp_surface import UserIOMcpSurface
from universal_userio.runtime import seed_owner_from_file
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def suggest(self, **_kwargs):
        return "AI draft"


class Outbox:
    def __init__(self):
        self.calls = []

    def send_reply(self, **kwargs):
        self.calls.append(kwargs)
        return "receipt"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args):
        return None


def test_users_with_same_provider_ids_are_isolated_and_approval_is_scoped(tmp_path) -> None:
    store, outbox = SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Outbox()
    service = UserIOService(store, Generator(), outbox)
    user_a, _ = store.create_user("alice", "alice-password")
    user_b, _ = store.create_user("bob", "bob-password")
    with pytest.raises(ValueError, match="route is not assigned to user"):
        service.receive(
            InboxMessage("telegram", "blocked", "chat", "blocked", 0.0),
            route_id="owner-route", user_id=user_a.user_id,
        )
    store.bind_channel_route(user_id=user_a.user_id, source="telegram", route_id="telegram")
    store.bind_channel_route(user_id=user_b.user_id, source="telegram", route_id="telegram")

    chat_a, accepted_a = service.receive(
        InboxMessage("telegram", "same-id", "same-chat", "Alice text", 1.0),
        route_id="telegram", user_id=user_a.user_id,
    )
    chat_b, accepted_b = service.receive(
        InboxMessage("telegram", "same-id", "same-chat", "Bob text", 1.0),
        route_id="telegram", user_id=user_b.user_id,
    )
    surface = UserIOMcpSurface(store, service)

    assert accepted_a is accepted_b is True
    assert chat_a != chat_b
    assert surface.dispatch("userio.channels.list", {}, principal=user_a)["chats"][0]["last_message_snippet"] == "Alice text"
    assert surface.dispatch("userio.channels.read", {"chat_id": chat_b}, principal=user_a)["error"] == "chat not found"

    draft = surface.dispatch(
        "userio.channels.send_draft", {"chat_id": chat_a, "text": "answer"}, principal=user_a
    )["draft"]
    denied = surface.dispatch(
        "userio.draft.approve_send", {"draft_id": draft["id"], "confirm": True}, principal=user_b
    )
    sent = surface.dispatch(
        "userio.draft.approve_send", {"draft_id": draft["id"], "confirm": True}, principal=user_a
    )

    assert denied == {"ok": False, "error": "draft not found"}
    assert sent["draft"]["status"] == "approved"
    assert len(outbox.calls) == 1


@pytest.mark.parametrize(
    ("adapter_type", "source"),
    [
        (MailChannelAdapter, "gmail"),
        (TelegramChannelAdapter, "telegram"),
        (WhatsAppChannelAdapter, "whatsapp"),
        (VKChannelAdapter, "vk"),
    ],
)
def test_each_provider_wrapper_implements_unified_contract(adapter_type, source, tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / f"{source}.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    chat_id, _ = service.receive(
        InboxMessage(source, "1", "chat", "hello", 1.0), route_id=source
    )
    adapter = adapter_type(store, service, store.default_user_id)

    assert adapter.list()[0]["id"] == chat_id
    assert adapter.read(chat_id=chat_id)["chat"]["messages"][0]["body"] == "hello"
    assert adapter.send(chat_id=chat_id, text="reply").status == "proposed"
    with pytest.raises(AdapterNotSupported, match="not supported by adapter"):
        adapter.download(file_ref="missing")


def test_chatgpt_cdp_adapter_reads_page_visible_chats_without_provider_credentials(tmp_path) -> None:
    class CdpMcp:
        def call(self, name, arguments):
            assert name == "list_chats" or arguments["chatRef"] == "chat_ref_1"
            if name == "list_chats":
                return {"chats": [{"chatRef": "chat_ref_1", "title": "Roadmap", "updatedAt": "2026-08-30T12:00:00Z", "unread": True}]}
            assert name == "export_chat"
            return {"content": json.dumps({"chatRef": "chat_ref_1", "title": "Roadmap", "messages": [{"messageRef": "msg_ref_1", "role": "user", "text": "status?"}]})}

    store = SQLiteUserIOStore(tmp_path / "chatgpt.sqlite3")
    adapter = ChatGPTCDPChannelAdapter(store, UserIOService(store, Generator(), Outbox()), store.default_user_id, client=CdpMcp())

    assert adapter.list() == [{"id": "chat_ref_1", "channel": "chatgpt", "title": "Roadmap", "last_message_snippet": "", "unread": True}]
    assert adapter.read(chat_id="chat_ref_1")["chat"]["messages"][0]["text"] == "status?"
    draft = adapter.send(chat_id="chat_ref_1", text="reply")

    class ChatGPTOutbox:
        def __init__(self):
            self.calls = []

        def send_reply(self, **_kwargs):
            raise AssertionError("ChatGPT must not use the NoticePlace route")

        def send_chatgpt_reply(self, **kwargs):
            self.calls.append(kwargs)
            return "message_ref_sent"

    outbox = ChatGPTOutbox()
    service = UserIOService(store, Generator(), outbox)
    approved = service.approve(draft.id)

    assert approved.status == "approved"
    assert outbox.calls == [{"chat_ref": "chat_ref_1", "draft_id": draft.id, "body": "reply"}]


def test_private_seed_creates_owner_login_without_exposing_password(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    seed = tmp_path / ".env.owner-seed"
    seed.write_text(
        "USERIO_SEED_USERNAME=seed-owner\nUSERIO_SEED_PASSWORD=seed-password\n",
        encoding="utf-8",
    )

    assert seed_owner_from_file(store, seed) is True
    principal, token = store.login("seed-owner", "seed-password") or (None, None)

    assert principal is not None and principal.role == "owner"
    assert store.authenticate_token(token).user_id == principal.user_id
    assert "seed-password" not in repr(principal)


def test_legacy_conversation_keeps_receiving_after_user_scoped_migration(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY,conversation_key TEXT UNIQUE NOT NULL,route_id TEXT NOT NULL,
            source TEXT NOT NULL,sender TEXT NOT NULL,identity_id TEXT,
            response_mode TEXT NOT NULL,updated_at REAL NOT NULL
        );
        CREATE TABLE messages (
            source TEXT NOT NULL,message_id TEXT NOT NULL,conversation_id TEXT NOT NULL,
            sender TEXT NOT NULL,body TEXT NOT NULL,received_at REAL NOT NULL,seen_at REAL,
            PRIMARY KEY(source,message_id)
        );
        CREATE TABLE drafts (
            id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,body TEXT NOT NULL,status TEXT NOT NULL,
            created_at REAL NOT NULL,approved_at REAL,outbox_receipt TEXT
        );
        CREATE TABLE identities (
            source TEXT NOT NULL,external_id TEXT NOT NULL,identity_id TEXT NOT NULL,
            display_name TEXT NOT NULL,PRIMARY KEY(source,external_id)
        );
        CREATE TABLE reply_rules (
            identity_id TEXT NOT NULL,source TEXT NOT NULL,route_id TEXT NOT NULL,
            response_mode TEXT NOT NULL,PRIMARY KEY(identity_id,source)
        );
        CREATE TABLE provider_accounts (
            id TEXT PRIMARY KEY,provider TEXT NOT NULL,display_name TEXT NOT NULL,
            can_read INTEGER NOT NULL,can_reply INTEGER NOT NULL,credential_ref TEXT NOT NULL,
            enabled INTEGER NOT NULL
        );
        INSERT INTO conversations VALUES
            ('legacy-chat','telegram:chat','telegram','telegram','chat','person','auto_send',1);
        INSERT INTO messages VALUES
            ('telegram','1','legacy-chat','chat','old',1,1);
        """
    )
    connection.close()
    store = SQLiteUserIOStore(path)
    service = UserIOService(store, Generator(), Outbox())

    chat_id, accepted = service.receive(
        InboxMessage("telegram", "2", "chat", "new", 2.0), route_id="telegram"
    )

    assert accepted is True
    assert chat_id == "legacy-chat"
    record = store.conversation(chat_id)
    assert [message["body"] for message in record["messages"]] == ["old", "new"]
    assert record["identity_id"] == "person"
    assert record["response_mode"] == "auto_send"
    assert record["messages"][0]["seen_at"] == 1


def test_ambiguous_or_unknown_explicit_connector_account_fails_closed(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    first, _ = store.create_user("first-user", "first-password")
    second, _ = store.create_user("second-user", "second-password")
    for user in (first, second):
        store.register_account(
            account_id="shared", provider="telegram", display_name="Shared",
            can_read=True, can_reply=True, credential_ref=f"connector:{user.user_id}",
            user_id=user.user_id,
        )

    with pytest.raises(ValueError, match="ambiguous"):
        store.ingress_user(source="telegram", account_id="shared")
    with pytest.raises(ValueError, match="not registered"):
        store.ingress_user(source="telegram", account_id="missing")


def test_http_user_creation_login_and_sse_mcp(tmp_path) -> None:
    service = UserIOService(SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Generator(), Outbox())
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="service-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        create = Request(
            base + "/v1/users", data=b'{"username":"connector","password":"connector-password"}',
            method="POST", headers={
                "Authorization": "Bearer service-token", "Content-Type": "application/json",
            },
        )
        with urlopen(create) as response:
            created = json.loads(response.read())
        login = Request(
            base + "/auth/login", data=b'{"username":"connector","password":"connector-password"}',
            method="POST", headers={"Content-Type": "application/json"},
        )
        with urlopen(login) as response:
            user_token = json.loads(response.read())["token"]
        mcp = Request(
            base + "/mcp",
            data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}',
            method="POST", headers={
                "Authorization": f"Bearer {user_token}",
                "Content-Type": "application/json", "Accept": "text/event-stream",
            },
        )
        with urlopen(mcp) as response:
            body = response.read()

        assert created["token_returned_once"] is True
        assert response.headers["Content-Type"].startswith("text/event-stream")
        assert body.startswith(b"event: message\ndata: ")
        payload = json.loads(body.split(b"data: ", 1)[1])
        assert payload["result"]["tools"][0]["name"] == "userio.channels.list"
    finally:
        server.shutdown()
        server.server_close()


def test_dashboard_requires_login_and_uses_user_scoped_session(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    roomhacker, _ = store.create_user("roomhacker", "dashboard-password")
    other, _ = store.create_user("other-user", "other-password")
    store.register_account(
        account_id="gmail-roomhacker", provider="gmail", display_name="roomhacker@gmail.com",
        can_read=True, can_reply=False, credential_ref="himalaya:roomhacker",
        user_id=roomhacker.user_id,
    )
    store.register_account(
        account_id="gmail-other", provider="gmail", display_name="other@gmail.com",
        can_read=True, can_reply=False, credential_ref="himalaya:other", user_id=other.user_id,
    )
    service = UserIOService(store, Generator(), Outbox())
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="service-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    opener = build_opener(NoRedirect())
    try:
        with pytest.raises(HTTPError) as anonymous:
            opener.open(base + "/")
        assert anonymous.value.code == 302
        assert anonymous.value.headers["Location"] == "/login"

        with urlopen(base + "/login") as response:
            page = response.read().decode()
        assert 'name="username"' in page
        assert 'name="password"' in page

        with pytest.raises(HTTPError) as login:
            opener.open(Request(
                base + "/auth/session",
                data=urlencode({"username": "roomhacker", "password": "dashboard-password"}).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ))
        assert login.value.code == 302
        assert login.value.headers["Location"] == "/"
        cookie = login.value.headers["Set-Cookie"].split(";", 1)[0]

        with urlopen(Request(base + "/v1/accounts", headers={"Cookie": cookie})) as response:
            accounts = json.loads(response.read())["accounts"]
        assert [account["display_name"] for account in accounts] == ["roomhacker@gmail.com"]
    finally:
        server.shutdown()
        server.server_close()


def test_dashboard_signup_creates_an_isolated_user_session(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    store.register_account(
        account_id="owner-mail", provider="gmail", display_name="owner@gmail.com",
        can_read=True, can_reply=False, credential_ref="himalaya:owner",
    )
    service = UserIOService(store, Generator(), Outbox())
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="service-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    opener = build_opener(NoRedirect())
    try:
        with urlopen(base + "/login") as response:
            assert b'href="/signup"' in response.read()
        with urlopen(base + "/signup") as response:
            page = response.read()
        assert b'action="/auth/signup"' in page
        assert b'name="password_confirm"' in page

        with pytest.raises(HTTPError) as signup:
            opener.open(Request(
                base + "/auth/signup",
                data=urlencode({
                    "username": "new-person", "password": "new-password",
                    "password_confirm": "new-password",
                }).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ))
        assert signup.value.code == 302
        assert signup.value.headers["Location"] == "/"
        cookie = signup.value.headers["Set-Cookie"].split(";", 1)[0]

        with urlopen(Request(base + "/v1/accounts", headers={"Cookie": cookie})) as response:
            assert json.loads(response.read()) == {"accounts": []}
        principal = store.authenticate_credentials("new-person", "new-password")
        assert principal is not None and principal.role == "user"
    finally:
        server.shutdown()
        server.server_close()
