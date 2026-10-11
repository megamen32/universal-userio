"""Account connection isolation and no-read-receipt regressions."""
from __future__ import annotations
import json
import stat
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

import pytest
from universal_userio import account_connections as connect
from universal_userio.channels.matrix import MatrixMessage
from universal_userio.http_api import handler
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv("USERIO_CONNECTION_SECRETS_DIR", str(tmp_path / "secrets"))
    store=SQLiteUserIOStore(tmp_path / "db.sqlite3")
    service=UserIOService(store,object(),object())
    user, token=store.create_user("second-human","correct-horse-battery-staple")
    yield service,store,user.user_id,token,tmp_path/"secrets"
    store.close()


def fake_matrix(monkeypatch):
    monkeypatch.setattr(connect,"matrix_homeserver",lambda _: "https://chat.example.org")
    def matrix_json(url, *, token="", payload=None):
        if url.endswith("/login"):
            return {"access_token":"mock-" + payload["identifier"]["user"]}
        return {"user_id":token.removeprefix("mock-")}
    monkeypatch.setattr(connect,"_matrix_json",matrix_json)


def test_matrix_connection_is_per_user(context,monkeypatch):
    service,store,other,token,root=context
    fake_matrix(monkeypatch)
    params={"homeserver":"https://chat.example.org","user_id":"@alice:example.org","password":"secret"}
    a=connect.connect_matrix(service,store.default_user_id,params)
    b=connect.connect_matrix(service,other,params)
    assert a["id"]!=b["id"]
    owner=next(r for r in store.accounts() if r["id"]==a["id"])
    assert owner["credential_ref"].startswith("connector:")
    assert "secret" not in json.dumps(owner) and "mock-" not in json.dumps(owner)
    secrets=connect.ConnectorSecrets(root)
    assert secrets.load(owner["credential_ref"],user_id=store.default_user_id,provider="matrix")["matrix_user_id"]=="@alice:example.org"
    with pytest.raises(PermissionError):
        secrets.load(owner["credential_ref"],user_id=other,provider="matrix")
    assert stat.S_IMODE(root.stat().st_mode)==0o700
    assert all(stat.S_IMODE(p.stat().st_mode)==0o600 for p in root.iterdir())
    assert store.route_allowed(user_id=other,source=b["id"],route_id="matrix-read-only")


def test_matrix_rejects_wrong_token_identity(context,monkeypatch):
    service,store,other,token,root=context
    monkeypatch.setattr(connect,"matrix_homeserver",lambda _: "https://chat.example.org")
    monkeypatch.setattr(connect,"_matrix_json",lambda url,**kw: {"user_id":"@eve:example.org"})
    with pytest.raises(ValueError,match="другому Matrix"):
        connect.connect_matrix(service,other,{"homeserver":"https://chat.example.org",
            "user_id":"@alice:example.org","access_token":"wrong"})
    assert not root.exists()


def test_matrix_mirror_is_scoped_and_preserves_outgoing(context,monkeypatch):
    service,store,other,token,root=context
    fake_matrix(monkeypatch)
    params={"homeserver":"https://chat.example.org","user_id":"@alice:example.org","password":"secret"}
    accounts=[(store.default_user_id,connect.connect_matrix(service,store.default_user_id,params)),
              (other,connect.connect_matrix(service,other,params))]
    class Reader:
        def __init__(self,host,token,rooms,user,*,include_own):
            assert include_own and rooms==("*",)
        def poll(self,cursor,*,limit):
            return [MatrixMessage("!room:hs","$in","@bob:hs","hello",100,"incoming"),
                    MatrixMessage("!room:hs","$out","@alice:example.org","reply",101,"outgoing")],"next"
    monkeypatch.setattr(connect,"MatrixReader",Reader)
    worker=connect.UserAccountPoller(service,vault=connect.ConnectorSecrets(root))
    for user,account in accounts:
        ref=next(a["credential_ref"] for a in store.accounts(user_id=user) if a["id"]==account["id"])
        secret=worker.vault.load(ref,user_id=user,provider="matrix")
        worker.poll_matrix(secret)
        rows=store.conversations(source="matrix",user_id=user)
        assert len(rows)==1 and rows[0]["account_ref"]==account["id"]
        history=store.conversation(rows[0]["id"],user_id=user)
        assert len(history["messages"])==2
        assert {m["direction"] for m in history["messages"]}=={"incoming","outgoing"}
        assert store.user_preference("source_cursor:"+account["id"],user_id=user)=="next"


def test_imap_per_user_and_read_only_peek(context,monkeypatch):
    service,store,other,token,root=context
    monkeypatch.setattr(connect,"public_hostname",lambda host:host)
    mailbox=connect.connect_imap(service,other,{"host":"imap.example.org","port":993,
        "username":"alice@example.org","password":"secret"},verifier=lambda *args:None)
    assert not store.conversations(source="mail")
    ref=next(a["credential_ref"] for a in store.accounts(user_id=other) if a["id"]==mailbox["id"])
    secret=connect.ConnectorSecrets(root).load(ref,user_id=other,provider="email")
    accesses=[]
    class Imap:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def login(self,u,p):assert u=="alice@example.org" and p=="secret"
        def select(self,box,readonly=False):
            assert (box,readonly)==("INBOX",True)
            return "OK",[b"1"]
        def uid(self,command,*args):
            accesses.append((command,args))
            if command=="search":return "OK",[b"21 22"]
            assert command=="fetch" and args[1]=="(BODY.PEEK[])"
            return "OK",[(b"",
                ("From: bob@example.org\r\nSubject: hi\r\n"
                 "Content-Type: text/plain; charset=utf-8\r\n\r\nhello").encode())]
    monkeypatch.setattr(connect.imaplib,"IMAP4_SSL",lambda *a,**kw:Imap())
    connect.UserAccountPoller(service,vault=connect.ConnectorSecrets(root)).poll_imap(secret)
    rows=store.conversations(source="mail",user_id=other)
    assert len(rows)==1 and len(store.conversation(rows[0]["id"],user_id=other)["messages"])==2
    assert not store.conversations(source="mail")
    assert len([x for x in accesses if x[0]=="fetch"])==2
    assert store.user_preference("source_cursor:"+mailbox["id"],user_id=other)=="22"


def test_connection_forms_and_http_account_scope(context,monkeypatch):
    service,store,other,token,root=context
    fake_matrix(monkeypatch)
    server=ThreadingHTTPServer(("127.0.0.1",0),handler(service,token="owner-token"))
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    base=f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(Request(base+"/accounts/connect/new",headers={
            "Authorization":"Bearer "+token}),timeout=5) as response:
            html=response.read().decode()
        assert all(x in html for x in ("Добавить аккаунт","Matrix","IMAP","ВКонтакте"))
        body=json.dumps({"homeserver":"https://chat.example.org",
            "user_id":"@alice:example.org","password":"secret"}).encode()
        request=Request(base+"/v1/matrix/accounts",data=body,method="POST",
            headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
        with urlopen(request,timeout=6) as response:
            assert response.status==201
            account=json.load(response)
        assert account["accepted"]
        assert any(a["id"]==account["id"] for a in store.accounts(user_id=other))
        assert not any(a["id"]==account["id"] for a in store.accounts())
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


@pytest.mark.parametrize("host",["127.0.0.1","localhost","169.254.169.254","192.168.1.1",".local"])
def test_reject_internal_hostnames(host):
    with pytest.raises(ValueError):
        connect.public_hostname(host)


def test_matrix_sso_login_token_exchanged_and_wrong_user_rejected(context,monkeypatch):
    service,store,other,token,root=context
    monkeypatch.setattr(connect,"matrix_homeserver",lambda _: "https://chat.example.org")
    requests=[]
    def mock(url,*,token="",payload=None):
        requests.append((url,payload))
        if url.endswith("/login"):
            assert payload["type"]=="m.login.token" and payload["token"]=="one-time-login-token"
            return {"access_token":"matrix-access-secret"}
        return {"user_id":"@alice:example.org"}
    monkeypatch.setattr(connect,"_matrix_json",mock)
    record=connect.connect_matrix(service,other,{"homeserver":"https://chat.example.org",
        "user_id":"@alice:example.org","login_token":"one-time-login-token"})
    assert record["display_name"]=="@alice:example.org"
    assert len(requests)==2
    assert "one-time-login-token" not in json.dumps(store.accounts(user_id=other))
    with pytest.raises(ValueError,match="другому Matrix"):
        connect.connect_matrix(service,other,{"homeserver":"https://chat.example.org",
            "user_id":"@eve:example.org","login_token":"one-time-login-token"})


def test_disconnect_removes_only_owners_secret(context,monkeypatch):
    service,store,other,token,root=context
    fake_matrix(monkeypatch)
    account=connect.connect_matrix(service,other,{"homeserver":"https://chat.example.org",
        "user_id":"@alice:example.org","password":"secret"})
    record=next(x for x in store.accounts(user_id=other) if x["id"]==account["id"])
    secret_path=root/(record["credential_ref"].removeprefix("connector:")+".json")
    assert secret_path.exists()
    server=ThreadingHTTPServer(("127.0.0.1",0),handler(service,token="owner-token"))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        request=Request(
            f"http://127.0.0.1:{server.server_port}/v1/accounts/{account['id']}",
            method="DELETE",headers={"Authorization":"Bearer "+token},
        )
        with urlopen(request,timeout=5) as response:
            assert json.load(response)["deleted"] is True
        assert not secret_path.exists()
        assert not any(x["id"]==account["id"] for x in store.accounts(user_id=other))
    finally:
        server.shutdown();server.server_close();thread.join(2)


def test_split_dns_exception_is_matrix_only(monkeypatch):
    # Emulate a Matrix hostname that resolves to RFC1918.
    import socket
    monkeypatch.setattr(connect.socket,"getaddrinfo",
        lambda *args,**kwargs:[(socket.AF_INET,socket.SOCK_STREAM,6,"",("192.168.2.100",0))])
    monkeypatch.delenv("USERIO_MATRIX_TRUSTED_HOSTS",raising=False)
    with pytest.raises(ValueError):
        connect.matrix_homeserver("https://chat.example.org")
    monkeypatch.setenv("USERIO_MATRIX_TRUSTED_HOSTS","chat.example.org")
    assert connect.matrix_homeserver("https://chat.example.org")=="https://chat.example.org"
    with pytest.raises(ValueError):
        connect.public_hostname("chat.example.org")
    with pytest.raises(ValueError):
        connect.matrix_homeserver("https://attacker.example")
