"""Synthetic fixtures for DION's documented binary and message wire contracts."""
import base64
import json
import urllib.error

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from universal_userio import dion_chat
from universal_userio.adapters import UnifiedChannels
from universal_userio.dion_chat import DionChatClient, DionCryptoSession, DionChatError
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore

ACCOUNT = "dion:org:user"
RAW = {
    "message_id": "m1", "conversation_id": "room", "creator_id": "peer",
    "created_at": "2026-11-26T12:00:00Z", "updated_at": "2026-11-26T12:00:00Z",
    "content": "", "attachments": [
        {"id":"file1","name":"report.txt","mime_type":"text/plain","size":3},
        {"id":"file2","name":"image.png","mime_type":"image/png","size":4},
    ],
}

class Response:
    status = 200
    def __init__(self, data):
        self.data = data
    def read(self, limit=-1):
        return self.data[:limit]
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False

class Broker:
    def access_token(self, account):
        assert account == ACCOUNT
        return "fixture"

def client_for(opener):
    client = DionChatClient(Broker(), ACCOUNT, opener=opener)
    client.crypto.session_id = "fixture"
    client.crypto._aes = AESGCM(bytes(range(32)))
    return client

def test_projection_preserves_multiple_attachment_identities():
    m = dion_chat.dion_message(RAW, account_id=ACCOUNT, own_user_id="user")
    assert m is not None
    assert m.body == "[Вложение DION]"
    assert [a["idx"] for a in m.attachments] == [0,1]
    assert [a["attachment_id"] for a in m.attachments] == ["file1","file2"]
    assert m.attachments[1]["kind"] == "image"
    assert "src" not in m.attachments[0]

def test_download_binary_interop_and_tamper():
    aes = AESGCM(bytes(range(32)))
    nonce = b"x"*12
    encrypted = nonce + aes.encrypt(nonce,b"abc",None)
    def opener(req, timeout):
        assert req.full_url == "https://dc-data-storage.dion.vc/v1/attachments/file1"
        assert req.get_method() == "GET"
        return Response(encrypted)
    client = client_for(opener)
    assert client.download_attachment("file1",max_bytes=3) == b"abc"
    with pytest.raises(DionChatError,match="exceeds"):
        client.download_attachment("file1",max_bytes=2)
    encrypted = encrypted[:-1] + bytes([encrypted[-1]^1])
    with pytest.raises(DionChatError,match="authentication"):
        client.download_attachment("file1",max_bytes=3)
    with pytest.raises(ValueError):
        client.download_attachment("https://example.test")

def test_multipart_upload_matches_webcrypto_binary_format():
    aes = AESGCM(bytes(range(32)))
    def opener(req, timeout):
        assert req.full_url == "https://dc-data-storage.dion.vc/v1/attachments"
        assert req.get_method() == "POST"
        boundary = req.get_header("Content-type").split("boundary=",1)[1].encode()
        head, rest = req.data.split(b"\r\n\r\n",1)
        assert b'name="file"; filename="fixture.txt"' in head
        body = rest.removesuffix(b"\r\n--"+boundary+b"--\r\n")
        assert aes.decrypt(body[:12],body[12:],None) == b"abc"
        nonce=b"y"*12
        receipt=nonce+aes.encrypt(nonce,b'{"id":"file-new","size":3}',None)
        return Response(json.dumps({"encrypted":base64.b64encode(receipt).decode()}).encode())
    client=client_for(opener)
    assert client.upload_attachment(b"abc",filename="fixture.txt",mime_type="text/plain")["id"]=="file-new"

def test_attachment_download_checks_owned_message_and_live_identity(tmp_path, monkeypatch):
    store=SQLiteUserIOStore(tmp_path/"fixture.sqlite")
    try:
        user=store.default_user_id
        service=UserIOService(store,object(),object())
        store.register_account(account_id=ACCOUNT,provider="dion",display_name="fixture",
            can_read=True,can_reply=True,credential_ref="fixture",enabled=True,user_id=user)
        store.bind_channel_route(user_id=user,source="dion",route_id="dion")
        message=dion_chat.dion_message(RAW,account_id=ACCOUNT,own_user_id="user")
        service.receive(message,route_id="dion",user_id=user,account_ref=ACCOUNT)
        class Client:
            row=RAW
            def message(self,mid):
                assert mid=="m1"
                return self.row
            def download_attachment(self,aid,*,max_bytes):
                assert aid=="file2"
                return b"test"
        client=Client()
        monkeypatch.setattr(dion_chat,"_client",lambda *a:(client,{}))
        channels=UnifiedChannels(store,service,user)
        out=channels.download(file_ref="dion:"+message.message_id,attachment_index=1,max_bytes=4)
        assert (out.filename,out.content_type,out.data)==("image.png","image/png",b"test")
        with pytest.raises(PermissionError):
            dion_chat.download_owned_attachment(service,user,message.message_id,account_id="dion:other:user")
        client.row={**RAW,"conversation_id":"unrelated"}
        with pytest.raises(DionChatError,match="source mismatch"):
            channels.download(file_ref="dion:"+message.message_id,attachment_index=1)
        client.row={**RAW,"attachments":[RAW["attachments"][0],{**RAW["attachments"][1],"id":"changed"}]}
        with pytest.raises(DionChatError,match="attachment changed"):
            channels.download(file_ref="dion:"+message.message_id,attachment_index=1)
    finally:
        store.close()

def test_poll_missing_list_is_not_success(tmp_path,monkeypatch):
    store=SQLiteUserIOStore(tmp_path/"fixture.sqlite")
    try:
        service=UserIOService(store,object(),object())
        class Client:
            def list_conversations(self,**kwargs): return {"unexpected":[]}
        monkeypatch.setattr(dion_chat,"_client",lambda *a:(Client(),{}))
        with pytest.raises(DionChatError,match="list is invalid"):
            dion_chat.poll_dion(service,{"user_id":store.default_user_id,"account_id":ACCOUNT})
        assert not store.user_preference("dion:poll-summary:"+ACCOUNT,user_id=store.default_user_id)
    finally:
        store.close()

@pytest.fixture
def messenger_case(tmp_path,monkeypatch):
    from universal_userio import messenger_dion
    from universal_userio.messenger import Messenger
    store=SQLiteUserIOStore(tmp_path/"messenger.sqlite")
    user=store.default_user_id
    service=UserIOService(store,object(),object())
    store.register_account(account_id=ACCOUNT,provider="dion",display_name="fixture",
        can_read=True,can_reply=True,credential_ref="connector:fixture",enabled=True,user_id=user)
    store.bind_channel_route(user_id=user,source="dion",route_id="dion")
    msg=dion_chat.dion_message(RAW,account_id=ACCOUNT,own_user_id="user")
    service.receive(msg,route_id="dion",user_id=user,account_ref=ACCOUNT)
    cid=store.message(msg.message_id,source="dion",user_id=user)["conversation_id"]
    class Client:
        row=RAW
        calls=[]
        fail=False
        def message(self,mid): return self.row
        def conversation(self,cid):
            assert cid=="room"
            return {"permissions":["conversation.messages.create"],"conversation_type":"DM","name":"Fixture"}
        def upload_attachment(self,data,*,filename,mime_type):
            self.calls.append(("upload",data,filename,mime_type))
            return {"id":"file-new"}
        def send_text(self,peer,text,*,request_id,attachment_ids=(),reply_to=""):
            self.calls.append(("send",peer,text,attachment_ids,reply_to))
            if self.fail:
                raise dion_chat.DionChatUncertain("fixture timeout")
            return "dion:room:intermediate:provider-new"
        parse_receipt=staticmethod(dion_chat.DionChatClient.parse_receipt)
        def by_intermediate(self,peer,intermediate):
            return {**RAW,"message_id":"provider-new","creator_id":"user",
                    "content":"sent","attachments":[]}
    client=Client()
    for module in (dion_chat,messenger_dion):
        monkeypatch.setattr(module,"_client",lambda *a:(client,{"provider_user_id":"user"}))
    yield Messenger(store,service,store.owner()),cid,msg.message_id,client
    store.close()

def test_approved_file_upload_and_send_only_once(messenger_case):
    api,cid,mid,client=messenger_case
    assert api.capabilities(ACCOUNT)["operations"]["file_upload"]["allowed"]
    result=api.upload_bytes(ACCOUNT,cid,mid,filename="fixture.txt",mime_type="text/plain",
                            data=b"abc",request_id="dion-file-stage-fixture")
    action=api.propose(ACCOUNT,cid,mid,"file",{"upload_id":result["upload_id"],"caption":"fixture"},
                       "dion-file-send-fixture")
    assert client.calls==[]
    accepted=api.approve(action["id"],action["hash"],True)
    assert accepted["status"]=="accepted"
    assert client.calls==[("upload",b"abc","fixture.txt","text/plain"),
                          ("send","room","fixture",("file-new",),"m1")]
    assert api.approve(action["id"],action["hash"],True)==accepted
    assert len(client.calls)==2

def test_text_source_change_refused_before_send(messenger_case):
    from universal_userio.contracts import DeliveryNotSubmittedError
    api,cid,mid,client=messenger_case
    action=api.propose(ACCOUNT,cid,mid,"reply",{"text":"fixture"},"dion-text-fixture-change")
    client.row={**RAW,"content":"changed"}
    with pytest.raises(DeliveryNotSubmittedError):
        api.approve(action["id"],action["hash"],True)
    assert client.calls==[]

def test_unknown_file_send_never_reuploads(messenger_case):
    api,cid,mid,client=messenger_case
    result=api.upload_bytes(ACCOUNT,cid,mid,filename="fixture.txt",mime_type="text/plain",
                            data=b"abc",request_id="dion-file-stage-unknown")
    action=api.propose(ACCOUNT,cid,mid,"file",{"upload_id":result["upload_id"]},
                       "dion-file-send-unknown")
    client.fail=True
    with pytest.raises(dion_chat.DionChatUncertain):
        api.approve(action["id"],action["hash"],True)
    with pytest.raises(ValueError,match="delivery_uncertain"):
        api.approve(action["id"],action["hash"],True)
    assert len(client.calls)==2

def test_http_metadata_and_raw_second_attachment(messenger_case, monkeypatch):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.parse import quote
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from universal_userio.http_api import handler
    from universal_userio.contracts import ChannelFile
    api,cid,mid,client=messenger_case
    def file_download(service,user_id,message_id,*,attachment_index=0):
        assert user_id==api.user and message_id==mid and attachment_index==1
        return ChannelFile("image.png","image/png",b"test")
    monkeypatch.setattr(dion_chat,"download_owned_attachment",file_download)
    server=ThreadingHTTPServer(("127.0.0.1",0),handler(api.service,token="owner-fixture-token"))
    thread=Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        root=f"http://127.0.0.1:{server.server_port}/v1/conversations/{quote(cid,safe='')}/media/{quote(mid,safe='')}"
        def get(url):
            return urlopen(Request(url,headers={"Authorization":"Bearer owner-fixture-token"}),timeout=5)
        with get(root+"?attachment_index=1") as response:
            payload=json.load(response)
            assert payload["available"] is True
            assert payload["filename"]=="image.png"
            assert len(payload["attachments"])==2
        with get(root+"/raw?attachment_index=1") as response:
            assert response.read()==b"test"
            assert response.headers["Content-Type"]=="image/png"
        with pytest.raises(HTTPError) as err:
            get(root+"?attachment_index=7")
        assert err.value.code==404
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)


def test_poll_distinguishes_empty_provider_payload_from_confirmed_empty_list(tmp_path,monkeypatch):
    store=SQLiteUserIOStore(tmp_path/"empty.sqlite")
    try:
        service=UserIOService(store,object(),object())
        user=store.default_user_id
        class Client:
            page={}
            def list_conversations(self,**kwargs): return self.page
        client=Client()
        monkeypatch.setattr(dion_chat,"_client",lambda *a:(client,{}))
        account={"user_id":user,"account_id":ACCOUNT}
        summary="dion:poll-summary:"+ACCOUNT
        cursor="dion:conversation-page:"+ACCOUNT
        dion_chat.poll_dion(service,account)
        assert json.loads(store.user_preference(summary,user_id=user))["status"]=="empty_response_unverified"
        assert not store.user_preference(cursor,user_id=user)
        client.page={"conversations":[]}
        dion_chat.poll_dion(service,account)
        assert json.loads(store.user_preference(summary,user_id=user))=={
            "status":"complete_page","conversations":0,"observed_messages":0}
    finally:
        store.close()
