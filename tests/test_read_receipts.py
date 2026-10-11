"""Focused integration: human read cursor/stealth/account isolation; expected2s/max15s."""
import pytest
from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Telegram:
    def __init__(self): self.calls=[]; self.fail=False
    def acknowledge(self, **args):
        self.calls.append(args)
        if self.fail: raise RuntimeError('provider unavailable')
        return {'ok':True}


def fixture(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'read.sqlite3');tg=Telegram()
    store.register_account(account_id='telegram:540',provider='telegram',display_name='Owner',can_read=True,can_reply=True,credential_ref='telegram-qr:account-1')
    svc=UserIOService(store,object(),object(),telegram_outbox=tg)
    cid=None
    for n in (1,2,3):
        cid,_=svc.receive(InboxMessage('telegram','381:'+str(n),'Peer',str(n),n,peer_id='381'),route_id='telegram',account_ref='telegram:540')
    return store,svc,tg,cid


def test_default_on_and_exact_visible_cursor(tmp_path):
    store,svc,tg,cid=fixture(tmp_path)
    result=svc.read_conversation(conversation_id=cid,message_id='telegram:540|381:2',automatic=True)
    assert result['changed']==2 and result['provider_acknowledged'] is True
    assert tg.calls==[{'account_ref':'telegram:540','chat_id':'381','message_id':'2'}]
    messages=store.conversation(cid)['messages']
    assert [bool(m['seen_at']) for m in messages]==[True,True,False]
    assert messages[1]['provider_read']==1


def test_actual_ingress_account_peer_message_id_shape(tmp_path):
    store,svc,tg,cid=fixture(tmp_path)
    cid,_=svc.receive(InboxMessage('telegram','381:4','Peer','live',4,peer_id='381'),route_id='telegram',account_ref='telegram:540')
    svc.read_conversation(conversation_id=cid,message_id='telegram:540|381:4')
    assert tg.calls[-1]['message_id']=='4' and tg.calls[-1]['chat_id']=='381'


def test_provider_watermark_does_not_ack_higher_native_id_at_equal_date(tmp_path):
    store,svc,tg,cid=fixture(tmp_path)
    for n in (10,9):
        cid,_=svc.receive(InboxMessage('telegram','381:'+str(n),'Peer','same time',20,peer_id='381'),route_id='telegram',account_ref='telegram:540')
    svc.read_conversation(conversation_id=cid,message_id='telegram:540|381:9')
    messages={m['message_id']:m for m in store.conversation(cid)['messages']}
    assert messages['telegram:540|381:9']['provider_read']==1
    assert not messages['telegram:540|381:10']['provider_read']


def test_stealth_is_persistent_and_manual_read_is_explicit(tmp_path):
    store,svc,tg,cid=fixture(tmp_path)
    store.set_user_preference('auto_read','false')
    assert svc.read_conversation(conversation_id=cid,message_id='telegram:540|381:2',automatic=True)['skipped']=='stealth'
    assert not tg.calls and all(m['seen_at'] is None for m in store.conversation(cid)['messages'])
    assert svc.read_conversation(conversation_id=cid,message_id='telegram:540|381:2',automatic=False)['changed']==2
    assert store.user_preference('auto_read')=='false'


def test_foreign_cursor_and_provider_failure_do_not_mark_read(tmp_path):
    store,svc,tg,cid=fixture(tmp_path)
    with pytest.raises(ValueError): svc.read_conversation(conversation_id=cid,message_id='foreign',automatic=True)
    assert not tg.calls
    tg.fail=True
    with pytest.raises(RuntimeError): svc.read_conversation(conversation_id=cid,message_id='telegram:540|381:2',automatic=True)
    assert all(m['seen_at'] is None for m in store.conversation(cid)['messages'])


def test_other_user_cannot_read_owner_conversation(tmp_path):
    store,svc,tg,cid=fixture(tmp_path)
    other,_=store.create_user('other-reader','correct horse battery staple')
    with pytest.raises(ValueError): svc.read_conversation(conversation_id=cid,message_id='telegram:540|381:2',automatic=True,user_id=other.user_id)
    assert not tg.calls


def test_normal_reads_remain_side_effect_free(tmp_path):
    store,svc,tg,cid=fixture(tmp_path)
    store.conversation(cid);store.conversations();store.workspace_events()
    assert not tg.calls and all(m['seen_at'] is None for m in store.conversation(cid)['messages'])


def test_read_preferences_http_auth_persistence_and_invalid_input(tmp_path):
    import json,threading
    from http.server import ThreadingHTTPServer
    from urllib.request import Request,urlopen
    from urllib.error import HTTPError
    from universal_userio.http_api import handler
    store,svc,tg,cid=fixture(tmp_path)
    _,other_token=store.create_user('other-preference','correct horse battery staple')
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(svc,token='owner-token'))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    def req(token,payload=None):
        r=Request(f'http://127.0.0.1:{server.server_port}/v1/preferences/reading',data=json.dumps(payload).encode() if payload is not None else None,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        try:
            with urlopen(r) as response: return response.status,json.loads(response.read())
        except HTTPError as error:return error.code,json.loads(error.read())
    try:
        assert req('')[0]==401
        assert req('owner-token')==(200,{'auto_read':True})
        assert req(other_token,{'auto_read':False})==(200,{'auto_read':False})
        assert req(other_token)==(200,{'auto_read':False})
        assert req('owner-token')==(200,{'auto_read':True})
        assert req('owner-token',{'auto_read':'false'})[0]==400
    finally:server.shutdown();server.server_close()
