"""Personal VK Messenger import: one UserIO user per token, no read receipts."""
from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import parse_qs
from urllib.request import Request, urlopen

import pytest

from universal_userio import account_connections as conn
from universal_userio.channels.vk_personal import VKPersonalReader, VKPermissionDenied
from universal_userio.http_api import handler
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class ReadResponse:
    def __init__(self, payload): self.payload = json.dumps(payload).encode()
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self, *_): return self.payload


class StubVK:
    def __init__(self, token): self.token = token
    def identity(self): return 42
    def conversations(self, *, offset=0, count=50):
        ids = [111, 2_000_000_001]
        return {
            "count": len(ids),
            "profiles": [{"id": 111, "first_name": "Иван", "last_name": "Иванов"}],
            "items": [
                {"conversation": {
                    "peer": {"id": peer},
                    "in_read": 11,
                    "chat_settings": {"title": "Беседа" if peer > 2_000_000_000 else ""},
                }, "last_message": {"id": 12 if peer == 111 else 31}}
                for peer in ids[offset:offset+count]
            ],
        }
    def history(self, peer_id, *, count=30, offset=0):
        values = (
            [{"id": 11, "date": 1760000000, "from_id": 111, "out": 0, "text": "Привет"},
             {"id": 12, "date": 1760000001, "from_id": 42, "out": 1, "text": "Ответ"}]
            if peer_id == 111 else
            [{"id": 31, "date": 1760000002, "from_id": 987, "out": 0, "text": "Беседа"}]
        )
        return values[offset:offset+count]


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("USERIO_CONNECTION_SECRETS_DIR", str(tmp_path/"secrets"))
    store=SQLiteUserIOStore(tmp_path/"db.sqlite3")
    service=UserIOService(store,object(),object())
    other,token=store.create_user("vk-personal-owner", "user-owned-password-for-testing")
    yield store,service,other.user_id,token,tmp_path/"secrets"
    store.close()


def test_personal_token_api_readonly_and_denied_access():
    names=[]
    def opener(request, *, timeout):
        name=request.full_url.rsplit("/",1)[-1]
        args=parse_qs(request.data.decode())
        names.append(name)
        assert args["access_token"] == ["private-messenger-test-token"]
        if name == "users.get":
            return ReadResponse({"response":[{"id":42}]})
        if name == "messages.getConversations":
            return ReadResponse({"response":{"items":[],"count":0}})
        if name == "messages.getHistory":
            return ReadResponse({"response":{"items":[]}})
        if name == "messages.getLongPollServer":
            return ReadResponse({"response":{"server":"https://lp.example.org","key":"x","ts":123}})
        raise AssertionError("unexpected read method")
    reader=VKPersonalReader("private-messenger-test-token",opener=opener)
    assert reader.identity()==42
    assert reader.conversations()["count"]==0
    assert reader.history(111)==[]
    assert reader.longpoll_server()["ts"]==123
    with pytest.raises(ValueError,match="not allowed"):
        reader.call("messages.markAsRead",peer_id=111)
    with pytest.raises(ValueError,match="not allowed"):
        reader.call("messages.send",peer_id=111,message="wrong")
    assert names==["users.get","messages.getConversations","messages.getHistory","messages.getLongPollServer"]
    def deny(request,*,timeout):
        return ReadResponse({"error":{"error_code":15}})
    with pytest.raises(VKPermissionDenied):
        VKPersonalReader("private-messenger-test-token",opener=deny).conversations()


def test_personal_token_tenants_and_reconnect_without_leaking_secrets(app,monkeypatch):
    store,service,other,_,vault_path=app
    params={"access_token":"private-messenger-test-token","user_id":"42","display_name":"VK личный"}
    records=[]
    for user in (store.default_user_id,other):
        a=conn.connect_vk_personal(service,user,params,reader_factory=StubVK)
        assert a["id"]=="vk:42"
        record=next(x for x in store.accounts(user_id=user) if x["id"]=="vk:42")
        assert record["credential_ref"].startswith("connector:")
        assert "private-messenger-test-token" not in json.dumps(record)
        records.append(record)
    assert records[0]["credential_ref"]!=records[1]["credential_ref"]
    with pytest.raises(PermissionError):
        conn.ConnectorSecrets(vault_path).load(
            records[0]["credential_ref"],user_id=other,provider="vk")
    monkeypatch.setattr("universal_userio.channels.vk_personal.VKPersonalReader",StubVK)
    poller=conn.UserAccountPoller(service)
    for user,account in zip((store.default_user_id,other),records):
        secret=poller.vault.load(account["credential_ref"],user_id=user,provider="vk")
        poller.poll_vk_personal(secret)
        rows=store.conversations(source="vk",user_id=user)
        assert len(rows)==2 and all(r["account_ref"]=="vk:42" for r in rows)
        dm=next(r for r in rows if "Ответ" in r["preview"])
        history=store.conversation(dm["id"],user_id=user)["messages"]
        assert len(history)==2
        assert {r["direction"] for r in history}=={"incoming","outgoing"}
        assert any(r["provider_read"] == 1 for r in history if r["direction"]=="incoming")
        assert store.user_preference("vk-personal:message-cursor:vk:42:111",user_id=user)=="12"
        poller.poll_vk_personal(secret)
        assert len(store.conversation(dm["id"],user_id=user)["messages"])==2
    assert {r["id"] for r in store.conversations(source="vk")} != {
        r["id"] for r in store.conversations(source="vk",user_id=other)}
    old=records[1]["credential_ref"]
    conn.connect_vk_personal(service,other,params,reader_factory=StubVK)
    assert not (vault_path / (old.removeprefix("connector:")+".json")).exists()


def test_denied_permission_or_wrong_user_never_links(app):
    store,service,other,_,root=app
    class Denied(StubVK):
        def conversations(self,**kwargs):
            raise VKPermissionDenied("denied")
    with pytest.raises(VKPermissionDenied):
        conn.connect_vk_personal(
            service,other,{"access_token":"private-messenger-test-token"},
            reader_factory=Denied)
    with pytest.raises(ValueError,match="другому"):
        conn.connect_vk_personal(
            service,other,{"access_token":"private-messenger-test-token","user_id":"99"},
            reader_factory=StubVK)
    assert not any(a["id"]=="vk:42" for a in store.accounts(user_id=other))


def test_personal_api_requires_owner_and_real_permission(app,monkeypatch):
    store,service,other,owner_token,_=app
    monkeypatch.setattr("universal_userio.channels.vk_personal.VKPersonalReader",StubVK)
    server=ThreadingHTTPServer(("127.0.0.1",0),handler(service,token="system-token"))
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    url=f"http://127.0.0.1:{server.server_port}/v1/vk/personal/accounts"
    body=json.dumps({"access_token":"private-messenger-test-token","user_id":"42"}).encode()
    def post(token):
        headers={"Content-Type":"application/json"}
        if token: headers["Authorization"]="Bearer "+token
        request=Request(url,data=body,method="POST",headers=headers)
        try:
            with urlopen(request,timeout=8) as response:
                return response.status,json.load(response)
        except HTTPError as error:
            return error.code,json.load(error)
    try:
        assert post("wrong")[0]==401
        status,created=post(owner_token)
        assert status==201
        assert created["id"]=="vk:42"
        assert created["mode"]=="vk_personal_messages_readonly"
        assert "access_token" not in json.dumps(created)
        assert not any(a["id"]=="vk:42" for a in store.accounts())
        assert any(a["id"]=="vk:42" for a in store.accounts(user_id=other))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_browser_oauth_login_ui_is_scoped_to_user_and_does_not_ask_password():
    from pathlib import Path
    body=(Path(__file__).parents[1]/"src/universal_userio/static/connect-accounts.html").read_text()
    assert 'data-open="vk-personal"' in body
    assert 'id="vk-personal"' in body
    assert "client_id=51453752" in body
    assert "/v1/vk/personal/accounts" in body
    assert "VK Мессенджер" in body
    assert "authorization" in body
    assert "vk-community" in body
    assert "rel=\"noopener noreferrer\"" in body
    assert "type=\"password\"" in body


def test_large_personal_vk_id_is_not_group_and_old_guess_corrects_without_reimport(app, monkeypatch):
    store, service, user, _, _ = app
    large = 200000000106
    class LargePeer(StubVK):
        typed = False
        def conversations(self, **kwargs):
            return {"count": 1, "items": [{"conversation": {
                "peer": {"id": large, **({"type": "user"} if self.typed else {})}},
                "last_message": {"id": 12}}]}
        def history(self, peer_id, **kwargs):
            return [{"id": 12, "date": 1760000001, "from_id": 42, "out": 1, "text": "Self test"}]
    monkeypatch.setattr("universal_userio.channels.vk_personal.VKPersonalReader", LargePeer)
    conn.connect_vk_personal(service, user, {"access_token": "private-personal-token"}, reader_factory=LargePeer)
    account = next(a for a in store.accounts(user_id=user) if a["id"] == "vk:42")
    poller = conn.UserAccountPoller(service)
    secret = poller.vault.load(account["credential_ref"], user_id=user, provider="vk")
    poller.poll_vk_personal(secret)
    cid = store.conversations(source="vk", user_id=user)[0]["id"]
    assert store.conversation(cid, user_id=user)["conversation_kind"] == "group"
    LargePeer.typed = True
    poller.poll_vk_personal(secret)
    conv = store.conversation(cid, user_id=user)
    assert conv["conversation_kind"] == "direct"
    assert len(conv["messages"]) == 1
    assert store.user_preference(f"vk-personal:message-cursor:vk:42:{large}", user_id=user) == "12"
    assert store.conversations(source="vk") == []
