"""User-owned VK communities run in the shared UserIO poller, read only."""
from __future__ import annotations

import json
import stat

import pytest

from universal_userio import account_connections as conn
from universal_userio.channels.vk_community import VKCommunityPermissionDenied, VKCommunityReader
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("USERIO_CONNECTION_SECRETS_DIR", str(tmp_path / "secrets"))
    store = SQLiteUserIOStore(tmp_path / "test.sqlite3")
    service = UserIOService(store, object(), object())
    person, _ = store.create_user("second-vk-owner", "safe-credential-for-testing")
    yield store, service, person.user_id, tmp_path / "secrets"
    store.close()


class FakeVK:
    def __init__(self, token: str, group_id: int):
        self.group_id = group_id
        assert token == "very-long-test-community-token"

    def conversations(self, *, offset=0, count=25):
        peers = [123, 2_000_000_001]
        return {
            "count": len(peers),
            "items": [
                {
                    "conversation": {"peer": {"id": peer, "type": "user" if peer == 123 else "chat"},
                                     "in_read": 11,
                                     "chat_settings": {"title": "VK-бот" if peer==123 else "Беседа"}},
                    "last_message": {"id": 12 if peer == 123 else 31},
                } for peer in peers[offset: offset + count]
            ],
            "profiles": [{"id": 123, "first_name": "Пользователь", "last_name": "VK"}],
        }

    def history(self, peer_id: int, *, count=35, offset=0):
        if peer_id == 123:
            rows = [
                {"id": 11, "date": 1760000000, "from_id": 123, "text": "Проверка", "out": 0},
                {"id": 12, "date": 1760000001, "from_id": -self.group_id,
                 "text": "Ответ бота", "out": 1},
            ]
        else:
            rows = [{"id": 31, "date": 1760000002, "from_id": 789,
                     "text": "Групповое сообщение", "out": 0}]
        return rows[offset:offset+count]


def test_same_vk_community_owned_by_two_userio_users_is_isolated(setup, monkeypatch):
    store, service, other, secret_dir = setup
    records = []
    for owner in (store.default_user_id, other):
        result = conn.connect_vk_community(service, owner, {
            "group_id": "4321", "display_name": "VK Бот",
            "access_token": "very-long-test-community-token",
        }, reader_factory=FakeVK)
        assert result["id"] == "vk:community:4321"
        account = next(x for x in store.accounts(user_id=owner) if x["id"] == result["id"])
        assert "very-long-test-community-token" not in json.dumps(account)
        assert account["credential_ref"].startswith("connector:")
        records.append(account)
    assert records[0]["credential_ref"] != records[1]["credential_ref"]
    assert stat.S_IMODE(secret_dir.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in secret_dir.iterdir())
    with pytest.raises(PermissionError):
        conn.ConnectorSecrets(secret_dir).load(
            records[0]["credential_ref"], user_id=other, provider="vk")
    monkeypatch.setattr("universal_userio.channels.vk_community.VKCommunityReader", FakeVK)
    poller = conn.UserAccountPoller(service)
    for owner, account in zip((store.default_user_id, other), records):
        key = poller.vault.load(account["credential_ref"], user_id=owner, provider="vk")
        poller.poll_vk_community(key)
        rows = store.conversations(source="vk", user_id=owner)
        assert len(rows) == 2
        assert all(row["account_ref"] == "vk:community:4321" for row in rows)
        group = next(row for row in rows if row["preview"] == "Групповое сообщение")
        assert group
        dm = next(row for row in rows if "Ответ бота" in row["preview"])
        history = store.conversation(dm["id"], user_id=owner)["messages"]
        assert {m["direction"] for m in history} == {"incoming", "outgoing"}
        assert store.user_preference(
            "vk-community:message-cursor:vk:community:4321:123", user_id=owner,
        ) == "12"
        poller.poll_vk_community(key)
        assert len(store.conversation(dm["id"], user_id=owner)["messages"]) == 2
    assert {r["id"] for r in store.conversations(source="vk")} != {
        r["id"] for r in store.conversations(source="vk", user_id=other)}


def test_vk_reader_denied_and_never_sends_or_marks_read():
    calls = []
    class Response:
        status = 200
        def __init__(self, body):
            self.body = json.dumps(body).encode()
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self, n=-1): return self.body
    def opener(request, *, timeout):
        calls.append(request.full_url.split("/")[-1])
        if request.full_url.endswith("messages.getConversations"):
            return Response({"error": {"error_code": 15, "error_msg": "permission denied"}})
        return Response({"response": {"items": []}})
    reader = VKCommunityReader("valid-long-test-token", 4321, opener=opener)
    with pytest.raises(VKCommunityPermissionDenied):
        reader.conversations()
    with pytest.raises(ValueError, match="read-only"):
        reader._call("messages.send", peer_id=123, message="never")
    assert calls == ["messages.getConversations"]


def test_an_unusable_token_is_not_registered(setup):
    store, service, other, _ = setup
    class Denied(FakeVK):
        def conversations(self, **kwargs):
            raise VKCommunityPermissionDenied("denied")
    with pytest.raises(VKCommunityPermissionDenied):
        conn.connect_vk_community(service, other, {
            "group_id": "4321", "access_token": "very-long-test-community-token",
        }, reader_factory=Denied)
    assert not store.accounts(user_id=other)


def test_community_connect_page_and_http_user_scope(setup, monkeypatch):
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from universal_userio.http_api import handler
    from universal_userio import account_connections

    store, service, other, _ = setup
    third, other_token = store.create_user("third-community-owner", "private-password-for-testing")
    monkeypatch.setattr(
        "universal_userio.channels.vk_community.VKCommunityReader", FakeVK,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="admin-test-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        page_req = Request(
            base + "/vk/community/connect/new",
            headers={"Authorization": "Bearer " + other_token},
        )
        with urlopen(page_req, timeout=6) as response:
            html = response.read().decode("utf-8")
        assert "VK-сообщество" in html
        assert "/v1/vk/community/accounts" in html
        assert "Личные сообщения" in html or "Личные" in html
        payload = json.dumps({
            "group_id": "4321", "access_token": "very-long-test-community-token",
        }).encode("utf-8")
        request = Request(
            base + "/v1/vk/community/accounts", method="POST", data=payload,
            headers={"Authorization": "Bearer " + other_token,
                     "Content-Type": "application/json"},
        )
        with urlopen(request, timeout=8) as response:
            created = json.load(response)
            assert response.status == 201
        assert created["id"] == "vk:community:4321"
        assert created["mode"] == "vk_community_messages_readonly"
        assert "access_token" not in json.dumps(created)
        assert all(a["id"] != created["id"] for a in store.accounts())
        assert any(a["id"] == created["id"] for a in store.accounts(user_id=third.user_id))
        unauthenticated = Request(
            base + "/v1/vk/community/accounts", method="POST", data=payload,
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as error:
            urlopen(unauthenticated, timeout=6)
        assert error.value.code == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_two_vk_communities_for_same_user_never_merge_messages(setup, monkeypatch):
    store, service, _, root = setup
    user = store.default_user_id
    vault = conn.ConnectorSecrets(root)
    for gid in (4321, 5432):
        conn.connect_vk_community(
            service, user,
            {"group_id": str(gid), "access_token": "very-long-test-community-token"},
            reader_factory=FakeVK,
        )
    monkeypatch.setattr("universal_userio.channels.vk_community.VKCommunityReader", FakeVK)
    poller = conn.UserAccountPoller(service, vault=vault)
    for item in store.accounts(user_id=user):
        if item["id"].startswith("vk:community:"):
            secret = vault.load(item["credential_ref"], user_id=user, provider="vk")
            poller.poll_vk_community(secret)
    rows = store.conversations(source="vk", user_id=user)
    assert len(rows) == 4
    by_account = {}
    for row in rows:
        by_account.setdefault(row["account_ref"], set()).add(row["id"])
    assert len(by_account) == 2
    assert set(by_account) == {"vk:community:4321", "vk:community:5432"}
    assert by_account["vk:community:4321"].isdisjoint(by_account["vk:community:5432"])
