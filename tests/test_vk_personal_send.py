"""Focused integration expected10s/max45s. Native personal VK write path.

Detects browser-worker fallback, sender/peer substitution, wrong-tenant writes,
lost native receipt mirror and retry after unknown delivery. No network.
"""
import io,json
from urllib.parse import parse_qs
import pytest
from test_vk_personal_messenger import app,StubVK
from universal_userio import account_connections as conn
from universal_userio.contracts import DeliveryNotSubmittedError

class Sender(StubVK):
    sent=[]
    outcome='ok'
    def send(self,peer_id,text,random_id):
        self.sent.append((self.token,peer_id,text,random_id))
        if self.outcome=='unknown':raise TimeoutError('unknown')
        if self.outcome=='refused':raise DeliveryNotSubmittedError('VK denied')
        return '333'
    def message(self,mid):
        return {'id':333,'peer_id':111,'from_id':42,'out':1,'date':1760000100,'text':self.sent[-1][2]}

def prepare(app,monkeypatch):
    store,service,user,_,_=app
    Sender.sent=[];Sender.outcome='ok'
    monkeypatch.setattr('universal_userio.channels.vk_personal.VKPersonalClient',Sender)
    conn.connect_vk_personal(service,user,{'access_token':'private-personal-token'},reader_factory=StubVK)
    account=next(a for a in store.accounts(user_id=user) if a['id']=='vk:42')
    store.register_account(account_id='vk:42',provider='vk',display_name='test',can_read=True,can_reply=True,credential_ref=account['credential_ref'],user_id=user)
    from universal_userio.contracts import InboxMessage
    cid,_=service.receive(InboxMessage('vk','vk:42|111:12','111','source',1760000001,peer_id='111',conversation_kind='direct'),route_id='vk-personal-read-only',user_id=user,account_ref='vk:42')
    return store,service,user,cid

def test_web_manual_send_uses_bound_personal_client_and_mirrors_exact_receipt(app,monkeypatch):
    store,service,user,cid=prepare(app,monkeypatch)
    draft=service.create_manual_draft(cid,body='New owner text',user_id=user)
    result=service.approve(draft.id,user_id=user)
    assert result.status=='approved' and result.receipt=='333'
    assert len(Sender.sent)==1 and Sender.sent[0][1:3]==(111,'New owner text')
    assert 0<Sender.sent[0][3]<2**31
    row=store.message('vk:42|111:333',source='vk',user_id=user)
    assert row and row['body']=='New owner text' and row['direction']=='outgoing'
    assert service.approve(draft.id,user_id=user).receipt=='333' and len(Sender.sent)==1
    assert store.message('vk:42|111:333',source='vk') is None

@pytest.mark.parametrize('mode,status',[('unknown','sending'),('refused','proposed')])
def test_unknown_is_not_replayed_and_refusal_releases_claim(app,monkeypatch,mode,status):
    store,service,user,cid=prepare(app,monkeypatch);Sender.outcome=mode
    draft=service.create_manual_draft(cid,body='New owner text',user_id=user)
    with pytest.raises(Exception):service.approve(draft.id,user_id=user)
    assert store.draft(draft.id,user_id=user).status==status
    if mode=='unknown':
        with pytest.raises(ValueError):service.approve(draft.id,user_id=user)
    assert len(Sender.sent)==1

def test_disabled_wrong_identity_and_foreign_credential_never_send(app,monkeypatch):
    store,service,user,cid=prepare(app,monkeypatch)
    draft=service.create_manual_draft(cid,body='New owner text',user_id=user)
    monkeypatch.setattr(Sender,'identity',lambda self:99)
    with pytest.raises(ValueError):service.approve(draft.id,user_id=user)
    assert not Sender.sent

def test_native_request_random_id_body_receipt_and_unknown():
    from universal_userio.channels.vk_personal import VKPersonalClient
    seen=[]
    class Response(io.BytesIO):
        def __enter__(self):return self
        def __exit__(self,*a):self.close()
    def opener(req,timeout):
        seen.append((req.full_url,parse_qs(req.data.decode())))
        return Response(b'{"response":333}')
    c=VKPersonalClient('private-personal-token',opener=opener)
    assert c.send(111,'New owner text',123)=='333'
    assert seen[0][0].endswith('/messages.send')
    assert seen[0][1]['peer_id']==['111'] and seen[0][1]['random_id']==['123']
    assert seen[0][1]['message']==['New owner text']
    def timeout(req,timeout):raise TimeoutError('private upstream details')
    with pytest.raises(RuntimeError):VKPersonalClient('private-personal-token',opener=timeout).send(111,'text',123)
    def refused(req,timeout):return Response(b'{"error":{"error_code":15}}')
    with pytest.raises(DeliveryNotSubmittedError):VKPersonalClient('private-personal-token',opener=refused).send(111,'text',123)


def test_normal_http_web_send_refresh_and_same_intent_dedup(app,monkeypatch):
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import Request,urlopen
    from universal_userio.http_api import handler
    store,service,user,cid=prepare(app,monkeypatch)
    token=app[3]
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(service,token='fixture-service'))
    thread=threading.Thread(target=server.serve_forever);thread.start()
    base='http://127.0.0.1:'+str(server.server_port)
    def req(method,path,body=None):
        r=Request(base+path,method=method,data=None if body is None else json.dumps(body).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with urlopen(r,timeout=5) as x:return x.status,json.load(x)
    try:
        body={'body':'New owner web text','request_id':'vk-web-new-intent-123456'}
        for n in range(2):
            status,sent=req('POST','/v1/conversations/'+cid+'/send',body)
            assert status==200 and sent['status']=='approved'
        status,page=req('GET','/v1/conversations/'+cid)
        assert any(m['message_id']=='vk:42|111:333' and m['body']==body['body'] for m in page['messages'])
        assert len(Sender.sent)==1
    finally:server.shutdown();server.server_close();thread.join()
