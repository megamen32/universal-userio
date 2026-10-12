"""Focused integration, expected3s/max30s. Actual MAX frame shapes, no network.

Detects hashed-ID substitution, lost reply anchors, wrong-account mutation and
same-ID repeated edit/projection regressions on the real provider adapter.
"""
import json

from universal_userio.channels.max_client import MaxClient
from universal_userio.contracts import InboxMessage
from universal_userio.messenger import Messenger
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Transport:
    def __init__(self):self.calls=[];self.frames=[];self.rows={123:{'id':'123','sender':'77','text':'before','time':1700000000000,'updateTime':1700000001000}}
    def connect(self,timeout):return type('WS',(),{'connected':True})()
    def close(self,ws):ws.connected=False
    def send(self,ws,text):
        f=json.loads(text);self.calls.append(f);p=f['payload'];op=f['opcode'];result={'ready':True}
        if op==19:result={'profile':{'contact':{'id':77}}}
        if op==49:result={'messages':sorted([r for r in self.rows.values() if r['time']<p['from']],key=lambda r:r['time'],reverse=True)[:p['backward']]}
        if op==48:result={'chats':[{'id':p['chatIds'][0],'type':'DIALOG'}]}
        if op==71:result={'messages':[self.rows[i] for i in p['messageIds'] if i in self.rows]}
        if op==64:
            row={'id':'900719925474099311','sender':'77','text':p['message']['text'],'time':1700000002000,
                 'link':p['message'].get('link'),'updateTime':0};self.rows[int(row['id'])]=row
            result={'chatId':0,'message':row}
        if op==67:
            row=self.rows[p['messageId']];row['text']=p['text'];row['updateTime']+=1000;result={'message':row}
        if op==178:result={'reactionInfo':{'reaction':p['reaction']}}
        self.frames.append({'seq':f['seq'],'opcode':op,'cmd':1,'payload':result})
    def recv(self,ws,timeout):return self.frames.pop(0)


def setup(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'store.db');t=Transport();client=MaxClient('fixture','fixture',transport=t)
    service=UserIOService(store,object(),object(),max_client=client,max_user_id=store.default_user_id)
    store.register_account(account_id='max',provider='max',display_name='owned test',can_read=True,can_reply=True,credential_ref='env:fixture')
    cid,_=service.receive(InboxMessage('max','max-0-123','77','before',1700000000,
        direction='outgoing',peer_id='0',edited_at=1700000001,reconciliation=True),route_id='max',account_ref='max')
    return store,t,service,cid,Messenger(store,service,store.owner())


def test_generic_reply_native_anchor_opaque_id_and_durable_projection(tmp_path):
    store,t,service,cid,api=setup(tmp_path)
    a=api.propose('max',cid,'max-0-123','reply',{'text':'fresh reply'},'one-max-reply-123456')
    r=api.approve(a['id'],a['hash'],True)
    assert r['receipt']['provider_message_id']=='900719925474099311'
    assert r['status']=='accepted'
    sent=[f['payload'] for f in t.calls if f['opcode']==64]
    assert len(sent)==1 and sent[0]['message']['link']=={'type':'REPLY','messageId':123}
    row=store.message('max-0-900719925474099311',source='max')
    assert row and row['body']=='fresh reply' and row['provider_reply_to']=='123'
    assert api.approve(a['id'],a['hash'],True)['receipt']==r['receipt']
    assert len([f for f in t.calls if f['opcode']==64])==1
    store.close()


def test_generic_same_id_can_edit_again_after_previous_receipt(tmp_path):
    store,t,service,cid,api=setup(tmp_path)
    old='before'
    for n,text in enumerate(['https://example.com/a','https://example.com/b','plain again']):
        a=api.propose('max',cid,'max-0-123','edit',{'text':text,'expected_text':old},'max-edit-same-id-1234-'+str(n))
        r=api.approve(a['id'],a['hash'],True)
        assert r['status']=='accepted' and r['receipt']['provider_message_id']=='123'
        assert store.message('max-0-123',source='max')['body']==text
        old=text
    assert len([f for f in t.calls if f['opcode']==67])==3
    store.close()


def test_bound_reaction_and_typing_never_mark_read(tmp_path):
    store,t,service,cid,api=setup(tmp_path)
    for action,payload in [('react',{'emoji':'👍'}),('typing',{'active':True}),('typing',{'active':False})]:
        a=api.propose(
            'max',cid,'max-0-123',action,payload,'max-rich-123456789-'+action+str(payload.get('active','reaction')))
        assert api.approve(a['id'],a['hash'],True)['status']=='accepted'
    reaction=next(f for f in t.calls if f['opcode']==178)
    assert reaction['payload']=={'chatId':0,'messageId':123,'reaction':{'reactionType':'EMOJI','id':'👍'}}
    assert len([f for f in t.calls if f['opcode']==65])==1
    assert not any(f['opcode']==92 for f in t.calls)
    store.close()



def test_max_file_reference_has_stable_native_identity_and_no_upload(tmp_path):
    store,t,service,cid,api=setup(tmp_path)
    row={'id':'124','sender':'77','text':'file source','time':1700000003000,
         'attaches':[{'_type':'FILE','fileId':900719925474099333,'name':'fixture.txt','size':8}]}
    t.rows[124]=row
    from universal_userio.max_ingress import message_envelope
    from universal_userio.adapters import inbox_message_from_envelope
    msg=inbox_message_from_envelope(message_envelope(row,chat_id='0',own_id='77',reconciliation=True),received_at=1)
    service.receive(msg,route_id='max',account_ref='max')
    a=api.propose('max',cid,'max-0-123','file',{'media_message_id':'max-0-124','attachment_index':0,'caption':'existing file'},'max-file-reference-123456')
    accepted=api.approve(a['id'],a['hash'],True)
    assert accepted['status']=='accepted'
    send=next(f['payload']['message'] for f in t.calls if f['opcode']==64)
    assert send['attaches']==[{'_type':'FILE','fileId':900719925474099333}]
    assert send['text']=='existing file' and send['link']['messageId']==123
    assert not any(f['opcode']==87 for f in t.calls)
    assert api.approve(a['id'],a['hash'],True)['receipt']==accepted['receipt']
    assert sum(f['opcode']==64 for f in t.calls)==1
    store.close()


def test_max_missing_attachment_stays_proposed_without_provider_write(tmp_path):
    store,t,service,cid,api=setup(tmp_path)
    t.rows[124]={'id':'124','sender':'77','text':'file source','time':1700000003000,'attaches':[]}
    service.receive(InboxMessage('max','max-0-124','77','file source',1700000003,peer_id='0',direction='outgoing',
        attachments=({'kind':'file','attachment_id':'1234'},)),route_id='max',account_ref='max')
    a=api.propose('max',cid,'max-0-123','file',{'media_message_id':'max-0-124','attachment_index':0},'max-denied-file-123456')
    import pytest
    with pytest.raises(Exception):api.approve(a['id'],a['hash'],True)
    assert api.get(a['id'])['status']=='proposed'
    assert not any(f['opcode']==64 for f in t.calls)
    store.close()



def test_max_provider_refresh_preserves_raw_source_and_cursor_scope(tmp_path):
    store,t,service,cid,api=setup(tmp_path)
    provider=service.messenger_providers['max']
    projected=provider.message(peer_id='0',provider_message_id='123',conversation_id=cid)
    assert projected['author']['id']=='77' and projected['source']['direction']=='outgoing'
    provider.import_projection(projected,peer_id='0',conversation_id=cid,kind='direct')
    for n in range(124,128):t.rows[n]={'id':str(n),'sender':'88','text':'  wire '+str(n)+'\n','time':1700000000000+n*1000}
    first=api.history('max',cid,limit=2,refresh=True)
    assert [m['source']['provider_message_id'] for m in first['messages']]==['126','127']
    assert first['messages'][0]['text']=='  wire 126\n' and first['next_cursor']
    second=api.history('max',cid,limit=2,cursor=first['next_cursor'],refresh=True)
    assert [m['source']['provider_message_id'] for m in second['messages']]==['124','125']
    assert api.events('max',after=0)['events']==[]  # refresh must not dispatch history
    assert not any(f['opcode'] in {64,92} for f in t.calls)
    store.close()


def test_max_photo_media_uses_actual_attachment_and_bytes_without_urls(tmp_path,monkeypatch):
    store,t,service,cid,api=setup(tmp_path)
    t.rows[123]['attaches']=[{'_type':'PHOTO','photoId':987,'baseUrl':'https://i.mycdn.me/native','photoToken':'private-fixture'}]
    from universal_userio import messenger_max_media as reader
    seen=[]
    def read(client,**args):
        seen.append(args)
        return {'kind':'photo','status':'ready','image':{'mimeType':'image/png','dataBase64':'eA=='}}
    monkeypatch.setattr(reader,'read_attachment',read)
    result=api.media('max',cid,'max-0-123')
    assert result['content']['image']['dataBase64']=='eA=='
    assert seen[0]['attachment']['photoId']==987
    assert 'private-fixture' not in json.dumps(result)
    store.close()


def test_new_max_document_upload_waits_ready_and_preserves_incoming(tmp_path,monkeypatch):
    import base64,hashlib,io,urllib.request
    store,t,service,cid,api=setup(tmp_path)
    orig=t.send
    def send(ws,text):
        f=json.loads(text)
        if f['opcode']==87:
            t.calls.append(f)
            t.frames.append({'seq':f['seq'],'opcode':87,'cmd':1,'payload':{'info':[{'fileId':987654321,'url':'https://upload.oneme.ru/owned-upload'}]}})
            t.frames.append({'opcode':128,'cmd':0,'payload':{'chatId':99,'message':{'id':'555','text':'foreign incoming retained'}}})
            t.frames.append({'opcode':136,'cmd':0,'payload':{'fileId':123456}})
            t.frames.append({'opcode':136,'cmd':0,'payload':{'fileId':987654321}})
        else:orig(ws,text)
    t.send=send
    posts=[]
    class Response(io.BytesIO):
        status=200
        def __enter__(self):return self
        def __exit__(self,*a):self.close()
    class Opener:
        def open(self,req,timeout):posts.append(req);return Response(b'OK')
    monkeypatch.setattr(urllib.request,'build_opener',lambda *a:Opener())
    data=b'MAX new exported report'
    u=api.upload('max',cid,'max-0-123',filename='report.txt',mime_type='text/plain',
        data_base64=base64.b64encode(data).decode(),sha256=hashlib.sha256(data).hexdigest(),request_id='new-max-document-stage-123456')
    a=api.propose('max',cid,'max-0-123','file',{'upload_id':u['upload_id'],'caption':'report'},'new-max-document-send-123456')
    assert not posts
    accepted=api.approve(a['id'],a['hash'],True)
    assert accepted['status']=='accepted' and len(posts)==1 and posts[0].data==data
    assert posts[0].get_header('Content-range')=='0-22/23'
    msg=next(f['payload']['message'] for f in t.calls if f['opcode']==64)
    assert msg['attaches']==[{'_type':'FILE','fileId':987654321}]
    assert msg['link']['messageId']==123 and msg['text']=='report'
    assert service.max_client.dispatch(timeout=0)['message']['id']=='555'
    assert api.approve(a['id'],a['hash'],True)==accepted and len(posts)==1
    assert not any(f['opcode']==92 for f in t.calls)
    store.close()


def test_max_send_reconnect_after_upload_never_reuploads(tmp_path,monkeypatch):
    import base64,hashlib,pytest
    from universal_userio.contracts import DeliveryNotSubmittedError
    store,t,service,cid,api=setup(tmp_path)
    uploads=[]
    def upload(**kw):uploads.append(kw);return {'_type':'FILE','fileId':777}
    def send(**kw):raise DeliveryNotSubmittedError('write connection refused after upload')
    monkeypatch.setattr(service.max_client,'upload_file',upload)
    monkeypatch.setattr(service.max_client,'send_bound',send)
    data=b'new document'
    u=api.upload('max',cid,'max-0-123',filename='report.txt',mime_type='text/plain',data_base64=base64.b64encode(data).decode(),
        sha256=hashlib.sha256(data).hexdigest(),request_id='max-reconnect-stage-123456')
    a=api.propose('max',cid,'max-0-123','file',{'upload_id':u['upload_id']},'max-reconnect-action-123456')
    with pytest.raises(RuntimeError):api.approve(a['id'],a['hash'],True)
    assert api.get(a['id'])['status']=='sending'
    with pytest.raises(ValueError,match='delivery_uncertain'):api.approve(a['id'],a['hash'],True)
    assert len(uploads)==1
    store.close()
