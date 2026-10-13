"""Focused integration: web send/edit and idle socket regression; expected 3s/max30s."""
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import pytest
from universal_userio.channels.max_client import MaxClient, MaxSendNotSubmittedError
from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore
from universal_userio.http_api import handler

class Transport:
    def __init__(self): self.sent=[]; self.frames=[]; self.sockets=[]; self.text='before'; self.fail=False
    def connect(self, timeout):
        if self.fail: raise OSError('connect failed')
        ws=type('Ws',(),{'connected':True,'stale':False})(); self.sockets.append(ws); return ws
    def close(self, ws): ws.connected=False
    def send(self, ws, text):
        if ws.stale: raise BrokenPipeError('idle close')
        f=json.loads(text); self.sent.append(f); op=f['opcode']
        row={'id':123,'sender':77,'text':self.text,'time':1700000000000,'updateTime':1700000001000}
        payload={'ready':True}
        if op==19: payload={'profile':{'contact':{'id':77}}}
        if op==64: payload={'chatId':0,'message':{'id':124}}
        if op==71: payload={'messages':[row]}
        if op==67:
            self.text=f['payload']['text']; row['text']=self.text; payload={'message':row}
        self.frames.append({'seq':f['seq'],'opcode':op,'cmd':1,'payload':payload})
    def recv(self, ws, timeout): return self.frames.pop(0)


def test_idle_socket_is_replaced_before_send_without_replaying_message():
    t=Transport(); c=MaxClient('fixture','device',transport=t); c.connect(); t.sockets[0].stale=True
    assert c.send(chat_id=0,text='own')['message_id']=='124'
    assert len(t.sockets)==2
    assert len([f for f in t.sent if f['opcode']==64])==1


def test_failed_preflight_never_submits_send_opcode():
    t=Transport(); t.fail=True; c=MaxClient('fixture','device',transport=t)
    with pytest.raises(MaxSendNotSubmittedError): c.send(chat_id=0,text='own')
    assert t.sent==[]


def test_edit_uses_provider_id_and_preserves_exact_native_message():
    t=Transport(); c=MaxClient('fixture','device',transport=t)
    row=c.edit(chat_id=0,message_id=123,text='after',expected_text='before')
    assert row['id']==123 and row['text']=='after'
    assert [f['payload'] for f in t.sent if f['opcode']==67]==[
        {'chatId':0,'messageId':123,'text':'after','elements':[],'attachments':[]}]


def test_edit_conflicting_native_body_never_submits_edit():
    t=Transport(); c=MaxClient('fixture','device',transport=t)
    with pytest.raises(ValueError,match='уже изменилось'):
        c.edit(chat_id=0,message_id=123,text='after',expected_text='wrong')
    assert not any(f['opcode']==67 for f in t.sent)


def test_edit_link_preview_uses_new_text_instead_of_retaining_old_preview():
    t=Transport(); t.text='See https://example.com/old'
    c=MaxClient('fixture','device',transport=t)
    c.get_message=lambda **kw: {'id':123,'sender':77,'text':t.text,
        'attaches':[{'_type':'SHARE','url':'https://example.com/old'}],
        'elements':[{'type':'LINK','from':4,'length':23}]}
    edited=c.edit(chat_id=0,message_id=123,text='See https://example.com/new',
                  expected_text='See https://example.com/old')
    assert edited['text']=='See https://example.com/new'
    edit=next(f['payload'] for f in t.sent if f['opcode']==67)
    assert edit['attachments']==[] and edit['elements']==[]


def test_edit_link_and_real_media_does_not_drop_the_media():
    t=Transport(); c=MaxClient('fixture','device',transport=t)
    c.get_message=lambda **kw: {'id':123,'sender':77,'text':'before',
        'attaches':[{'_type':'SHARE','url':'https://example.com'}, {'_type':'PHOTO','photoId':42}]}
    with pytest.raises(ValueError,match='вложениями'):
        c.edit(chat_id=0,message_id=123,text='after',expected_text='before')
    assert not any(f['opcode']==67 for f in t.sent)


def setup(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'db.sqlite'); t=Transport(); c=MaxClient('fixture','device',transport=t)
    svc=UserIOService(store,object(),object(),max_client=c,max_user_id=store.default_user_id,max_manual_approve=True)
    store.register_account(account_id='max',provider='max',display_name='fixture',can_read=True,can_reply=True,credential_ref='env:USERIO_MAX_TOKEN')
    conv,_=svc.receive(InboxMessage('max','max-0-123','77','before',1700000000,
        direction='outgoing',peer_id='0',reconciliation=True),route_id='max',account_ref='max')
    return store,t,svc,conv


def test_human_send_is_immediate_idempotent_and_ai_draft_remains_manual(tmp_path):
    store,t,svc,conv=setup(tmp_path)
    result=svc.send_manual_message(conv,body='hello',request_id='one-request-0000001')
    assert result.status=='approved' and result.receipt=='124'
    svc.send_manual_message(conv,body='hello',request_id='one-request-0000001')
    assert len([f for f in t.sent if f['opcode']==64])==1
    with pytest.raises(ValueError,match='другому'): svc.send_manual_message(conv,body='different',request_id='one-request-0000001')
    draft=svc.create_manual_draft(conv,body='AI suggestion')
    assert draft.status=='proposed'
    assert len([f for f in t.sent if f['opcode']==64])==1


def test_preflight_releases_claim_but_uncertain_provider_write_does_not(tmp_path):
    store,t,svc,conv=setup(tmp_path); t.fail=True
    with pytest.raises(MaxSendNotSubmittedError): svc.send_manual_message(conv,body='hello',request_id='one-request-0000001')
    draft=store.conversation(conv)['drafts'][0]
    assert draft['status']=='proposed'
    t.fail=False
    original=t.send
    def broken(ws,text):
        if json.loads(text)['opcode']==64: raise BrokenPipeError('unknown provider acceptance')
        original(ws,text)
    t.send=broken
    with pytest.raises(BrokenPipeError): svc.send_manual_message(conv,body='hello',request_id='one-request-0000001')
    assert store.draft(draft['id']).status=='sending'
    with pytest.raises(ValueError,match='уточняется'): svc.send_manual_message(conv,body='hello',request_id='one-request-0000001')


def test_edit_updates_mirror_same_id_no_new_arrival_and_other_user_denied(tmp_path):
    store,t,svc,conv=setup(tmp_path)
    before=store.workspace_events(after=0)
    row=svc.edit_max_message(conv,message_id='max-0-123',body='after',expected_body='before')
    assert row['body']=='after' and row['edited_at']==1700000001
    assert len(store.conversation(conv)['messages'])==1
    assert store.workspace_events(after=0)==before
    with pytest.raises(ValueError): svc.edit_max_message(conv,message_id='max-0-999',body='wrong',expected_body='before')


def test_max_edit_removes_old_link_preview_and_remains_editable(tmp_path):
    store,t,svc,conv=setup(tmp_path)
    store.upsert_attachment(source='max',message_id='max-0-123',
        attachment={'kind':'share','provider_ref':'fixture'},user_id=store.default_user_id)
    row=svc.edit_max_message(conv,message_id='max-0-123',body='after',expected_body='before')
    assert store.attachments_for_message(source='max',message_id='max-0-123') == []
    svc.edit_max_message(conv,message_id='max-0-123',body='again',expected_body='after')
    assert store.conversation(conv)['messages'][0]['body']=='again'


def test_sent_max_receipt_is_mirrored_before_the_web_refresh(tmp_path):
    store,t,svc,conv=setup(tmp_path)
    svc.max_client.get_message=lambda **kw: {'id':124,'sender':77,'text':'hello',
        'time':1700000002000,'attaches':[{'_type':'SHARE','url':'https://example.com'}]}
    svc.send_manual_message(conv,body='hello',request_id='mirror-request-00001')
    current=store.conversation(conv)
    assert any(m['message_id']=='max-0-124' and m['body']=='hello' and m['direction']=='outgoing'
               for m in current['messages'])
    assert current['drafts'][0]['status']=='approved'
    assert store.workspace_events(after=0)['events']==[]


def test_max_mirror_read_failure_does_not_replay_or_fail_an_accepted_send(tmp_path):
    store,t,svc,conv=setup(tmp_path)
    def unavailable(**kw): raise TimeoutError('provider read delayed')
    svc.max_client.get_message=unavailable
    first=svc.send_manual_message(conv,body='hello',request_id='mirror-request-00001')
    second=svc.send_manual_message(conv,body='hello',request_id='mirror-request-00001')
    assert first.status==second.status=='approved' and first.receipt==second.receipt=='124'
    assert len([f for f in t.sent if f['opcode']==64])==1


def test_http_normal_human_send_and_edit(tmp_path):
    store,t,svc,conv=setup(tmp_path)
    session=store.create_oauth_session(store.default_user_id)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(svc,token='owner-fixture'))
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    def call(method,path,payload,token='owner-fixture'):
        request=Request(f'http://127.0.0.1:{server.server_port}'+path,method=method,
            data=json.dumps(payload).encode(),headers={**({'Cookie':'userio_web_session='+session} if token=='owner-fixture' else {'Authorization':'Bearer '+token}),'Content-Type':'application/json'})
        try:
            with urlopen(request) as r:return r.status,json.load(r)
        except HTTPError as r:return r.code,json.load(r)
    try:
        assert call('POST',f'/v1/conversations/{conv}/send',{'body':'hello','request_id':'http-request-000001'},token='wrong')[0]==401
        code,result=call('POST',f'/v1/conversations/{conv}/send',{'body':'hello','request_id':'http-request-000001'})
        assert code==200 and result['status']=='approved' and result['receipt']=='124'
        assert call('PATCH',f'/v1/conversations/{conv}/messages/max-0-123',{'body':'after','expected_body':'before'})[0]==200
    finally: server.shutdown();server.server_close();thread.join()


def test_sdk_edit_accepts_native_integer_reference():
    import asyncio
    from universal_userio.channels.max import MaxChannel
    class Client:
        def history(self, chat_id, backward): return [{'id':123,'sender':77,'time':1700000000000,'text':'before'}]
        def edit(self, **kwargs):
            assert kwargs == {'chat_id':'0','message_id':'123','text':'after','expected_text':'before'}
            return {'id':123,'sender':77,'time':1700000000000,'text':'after','out':True}
    message=asyncio.run(MaxChannel(Client()).edit_message('0',123,'after'))
    from userio_adapter_sdk import stable_ref_id
    assert message.id==stable_ref_id('123') and message.text=='after' and message.out


def test_concurrent_approval_reports_pending_and_never_replays(tmp_path):
    store,t,svc,conv=setup(tmp_path)
    draft=svc.create_manual_draft(conv,body='one')
    started=threading.Event(); release=threading.Event(); original=t.send; results=[]
    def block(ws,text):
        if json.loads(text)['opcode']==64:
            started.set(); assert release.wait(2)
        original(ws,text)
    t.send=block
    thread=threading.Thread(target=lambda:results.append(svc.approve(draft.id))); thread.start()
    try:
        assert started.wait(2)
        with pytest.raises(ValueError,match='уточняется'): svc.approve(draft.id)
    finally: release.set(); thread.join(3)
    assert len(results)==1 and results[0].receipt=='124'
    assert len([f for f in t.sent if f['opcode']==64])==1
