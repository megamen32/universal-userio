"""Native VK message permission and multi-tenant read-only sync contracts."""
from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import parse_qs
from urllib.request import Request, urlopen

import pytest

from universal_userio import account_connections
from universal_userio.channels.vk_native import VKNativeReader, VKPermissionDenied
from universal_userio.http_api import handler, _vk_connect_page
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):return self
    def __exit__(self, *args):return False
    def read(self, limit=-1):return json.dumps(self.payload).encode()


def test_read_only_vk_api_never_calls_send_or_mark_read():
    calls = []
    def opener(request, *, timeout):
        data = parse_qs(request.data.decode())
        calls.append((request.full_url.split("/")[-1], data))
        assert request.get_method() == "POST"
        if request.full_url.endswith("users.get"):
            return _Response({"response":[{"id":42}]})
        if request.full_url.endswith("messages.getConversations"):
            return _Response({"response":{"count":1, "items":[{"conversation":{"peer":{"id":2}},
                                                                    "last_message":{"id":8}}]}})
        return _Response({"response":{"items":[{"id":8,"peer_id":2,"text":"hello","date":100,"out":0}]}})
    reader = VKNativeReader("fake-long-enough-token", opener=opener)
    assert reader.identity() == 42
    assert reader.conversations(count=1)["count"] == 1
    assert reader.history(2)[0]["text"] == "hello"
    assert {method for method, _ in calls} == {
        "users.get", "messages.getConversations", "messages.getHistory"
    }
    assert all(call["access_token"] == ["fake-long-enough-token"] for _, call in calls)
    with pytest.raises(ValueError, match="not allowed"):
        reader.call("messages.markAsRead")


def test_access_denied_never_counts_as_connected():
    def deny(request, *, timeout):
        return _Response({"error":{"error_code":15,"error_msg":"User token has no messages scope"}})
    reader = VKNativeReader("fake-long-enough-token", opener=deny)
    with pytest.raises(VKPermissionDenied, match="доступ"):
        reader.conversations()


@pytest.fixture
def users(tmp_path, monkeypatch):
    monkeypatch.setenv("USERIO_CONNECTION_SECRETS_DIR",str(tmp_path/"vault"))
    store=SQLiteUserIOStore(tmp_path/"db.sqlite3")
    service=UserIOService(store,object(),object())
    other, token=store.create_user("vk-other","a-secure-user-passphrase")
    yield store, service, other.user_id, token
    store.close()


class VKStub:
    def __init__(self, token):self.token=token
    def identity(self):return 42
    def conversations(self, *, offset=0, count=50):
        peers=list(range(11,17))
        return {"count":len(peers),"items":[
            {"conversation":{"peer":{"id":pid}},"last_message":{"id":pid*10+1}}
            for pid in peers[offset:offset+count]
        ]}
    def history(self, peer_id, *, count=30):
        return [
            {"id":peer_id*10, "text":"hello", "date":100+peer_id, "out":0},
            {"id":peer_id*10+1, "text":"reply", "date":200+peer_id, "out":1},
        ]


def test_link_tokens_are_isolated_per_user_and_history_is_per_account(users,monkeypatch):
    store,service,other,_=users
    records=[]
    for uid in (store.default_user_id, other):
        result=account_connections.connect_vk_direct(
            service,uid,{"user_id":"42","display_name":"Мой VK","access_token":"token-good-long"},
            reader_factory=VKStub,
        )
        assert result["id"]=="vk:42"
        account=next(r for r in store.accounts(user_id=uid) if r["id"]==result["id"])
        assert account["credential_ref"].startswith("connector:")
        assert "token-good-long" not in json.dumps(account)
        records.append(account)
    assert records[0]["credential_ref"]!=records[1]["credential_ref"]
    vault=account_connections.ConnectorSecrets()
    with pytest.raises(PermissionError):
        vault.load(records[0]["credential_ref"],user_id=other,provider="vk")
    monkeypatch.setattr("universal_userio.channels.vk_native.VKNativeReader",VKStub)
    worker=account_connections.UserAccountPoller(service,vault=vault)
    for uid,record in zip((store.default_user_id,other),records):
        secret=vault.load(record["credential_ref"],user_id=uid,provider="vk")
        worker.poll_vk(secret)
        assert store.user_preference("vk:dialog-page:vk:42",user_id=uid)=="5"
        worker.poll_vk(secret)
        rows=store.conversations(source="vk",user_id=uid)
        assert len(rows)==6
        assert all(r["account_ref"]=="vk:42" for r in rows)
        history=store.conversation(rows[0]["id"],user_id=uid)["messages"]
        assert {m["direction"] for m in history}=={"incoming","outgoing"}
        assert all(m["provider_read"] is None for m in history)
        assert store.user_preference("vk:dialog-page:vk:42",user_id=uid)=="0"
    # Same provider ids in two UserIO users cannot leak between tenants.
    assert {r["id"] for r in store.conversations(source="vk")} != {
        r["id"] for r in store.conversations(source="vk",user_id=other)}


def test_no_persisting_vk_identity_when_token_is_wrong(users):
    store,service,other,_=users
    class Bad(VKStub):
        def identity(self):return 99
    with pytest.raises(ValueError,match="другому"):
        account_connections.connect_vk_direct(service,other,{
            "user_id":"42","access_token":"token-good-long",
        },reader_factory=Bad)
    assert not store.accounts(user_id=other)


def test_vk_id_login_does_not_downgrade_existing_native_account(users, monkeypatch):
    store,service,other,user_token=users
    account_connections.connect_vk_direct(service,other,{
        "user_id":"42","access_token":"token-good-long",
    },reader_factory=VKStub)
    original=next(a for a in store.accounts(user_id=other) if a["id"]=="vk:42")["credential_ref"]
    web=ThreadingHTTPServer(("127.0.0.1",0),handler(service,token="owner-token"))
    worker=threading.Thread(target=web.serve_forever,daemon=True)
    worker.start()
    try:
        body=json.dumps({"user_id":"42","display_name":"VK ID only"}).encode()
        request=Request(f"http://127.0.0.1:{web.server_port}/v1/vk/accounts",data=body,
            method="POST",headers={"Authorization":"Bearer "+user_token,
                                   "Content-Type":"application/json"})
        with urlopen(request,timeout=6) as response:
            result=json.load(response)
        assert result["mode"]=="vk_personal_messages_readonly"
        actual=next(a for a in store.accounts(user_id=other) if a["id"]=="vk:42")
        assert actual["credential_ref"]==original and "read" in actual["capabilities"]
    finally:
        web.shutdown()
        web.server_close()
        worker.join(timeout=2)


def test_vk_connect_page_does_not_offer_unavailable_messages_scope():
    page=_vk_connect_page("123456")
    assert "Запросить доступ к сообщениям VK" not in page
    assert "mode=direct" not in page
    assert "scope:''" in page
    assert "/v1/vk/native/accounts" not in page
    assert "Серверная синхронизация личных переписок пока не поддерживается" in page
    assert "VK ID" in page


def test_vk_native_http_enrollment_requires_owner_and_message_permission(users, monkeypatch):
    store, service, other, token = users
    monkeypatch.setattr("universal_userio.channels.vk_native.VKNativeReader", VKStub)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="owner-token"))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}/v1/vk/native/accounts"
    body = json.dumps({
        "user_id":"42", "display_name":"VK other", "access_token":"token-good-long",
    }).encode()
    def post(auth):
        req=Request(base, method="POST",data=body,headers={
            "Content-Type":"application/json", "Authorization":"Bearer "+auth,
        })
        try:
            with urlopen(req,timeout=6) as response:
                return response.status,json.load(response)
        except HTTPError as error:
            return error.code,json.load(error)
    try:
        assert post("wrong-token")[0]==401
        status, reply=post(token)
        assert status==409
        assert reply["code"]=="vk_messages_permission_unavailable"
        assert not any(r["id"]=="vk:42" for r in store.accounts(user_id=other))
        assert not any(r["id"]=="vk:42" for r in store.accounts())
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
