"""Focused integration, expected3s/max30s. Actual MAX frame shapes, no network.

Detects hashed-ID substitution, lost reply anchors, wrong-account mutation and
same-ID repeated edit/projection regressions on the real provider adapter.
"""
import json
import pytest
import websocket

from universal_userio.channels.max_client import MaxClient
from universal_userio.contracts import InboxMessage
from universal_userio.messenger import Messenger
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Transport:
    def __init__(self):self.calls=[];self.frames=[];self.inclusive_history=False;self.rows={123:{'id':'123','sender':'77','text':'before','time':1700000000000,'updateTime':1700000001000}}
    def connect(self,timeout):return type('WS',(),{'connected':True})()
    def close(self,ws):ws.connected=False
    def send(self,ws,text):
        f=json.loads(text);self.calls.append(f);p=f['payload'];op=f['opcode'];result={'ready':True}
        if op==19:result={'profile':{'contact':{'id':77}}}
        if op==49:result={'messages':sorted([r for r in self.rows.values() if r['time']<p['from'] or self.inclusive_history and r['time']==p['from']],key=lambda r:r['time'],reverse=True)[:p['backward']]}
        if op==48:result={'chats':[{'id':p['chatIds'][0],'type':'DIALOG'}]}
        if op==71:result={'messages':[self.rows[i] for i in p['messageIds'] if i in self.rows]}
        if op==64:
            row={'id':'900719925474099311','sender':'77','text':p['message']['text'],'time':1700000002000,
                 'link':p['message'].get('link'),'attaches':p['message'].get('attaches',[]),
                 'updateTime':0};self.rows[int(row['id'])]=row
            result={'chatId':0,'message':row}
        if op==67:
            row=self.rows[p['messageId']];row['text']=p['text'];row['updateTime']+=1000;result={'message':row}
        if op==178:result={'reactionInfo':{'reaction':p['reaction']}}
        if op==179:result={'reactionInfo':{'totalCount':0}}
        if op==180:result={'messagesReactions':{str(p['messageIds'][0]):{'totalCount':0}}}
        self.frames.append({'seq':f['seq'],'opcode':op,'cmd':1,'payload':result})
    def recv(self,ws,timeout):return self.frames.pop(0)


def setup(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'store.db');t=Transport();client=MaxClient('fixture','fixture',transport=t)
    service=UserIOService(store,object(),object(),max_client=client,max_user_id=store.default_user_id)
    store.register_account(account_id='max',provider='max',display_name='owned test',can_read=True,can_reply=True,credential_ref='env:fixture')
    cid,_=service.receive(InboxMessage('max','max-0-123','77','before',1700000000,
        direction='outgoing',peer_id='0',edited_at=1700000001,reconciliation=True),route_id='max',account_ref='max')
    return store,t,service,cid,Messenger(store,service,store.owner())


def test_max_login_refusal_reports_reconnect_keeps_history_and_blocks_new_claims(tmp_path):
    """Focused integration expected1s/max5s: live opcode19 login.token.

    Known authentication refusal must not look like an unknown write; cached
    history/actions stay visible and no mutation or blind login retry occurs.
    """
    from universal_userio.messenger import dispatch
    store,t,service,cid,api=setup(tmp_path)
    pending=api.propose('max',cid,'max-0-123','reply',{'text':'unsent'},'auth-refusal-pending-1234')
    old_send=t.send
    def reject_login(ws,text):
        frame=json.loads(text)
        if frame['opcode']==19:
            t.calls.append(frame)
            t.frames.append(dict(frame,cmd=3,payload={'error':'login.token','private':'must-not-appear'}))
        else:old_send(ws,text)
    t.send=reject_login
    args={'account_ref':'max','peer_id':'0','provider_message_id':'123'}
    result=dispatch(store,service,store.owner(),'resolve',args)
    assert result['error']=='provider_authentication_required'
    assert result['reconnect_required'] is True and 'MAX' in result['message']
    assert 'must-not-appear' not in json.dumps(result)
    cap=api.capabilities('max')
    assert cap['provider_status']=='authentication_required' and cap['history_mode']=='stored'
    assert cap['operations']['read']['allowed'] and cap['operations']['history']['allowed']
    for name in ('reply','edit','react','download','file_upload'):
        assert cap['operations'][name]['supported'] and not cap['operations'][name]['allowed']
        assert cap['operations'][name]['reason']=='provider_authentication_required'
    page=api.history('max',cid,refresh=False)
    assert page['messages'][0]['text']=='before' and page.get('provider_read_acknowledged',False) is False
    again=dispatch(store,service,store.owner(),'resolve',args)
    assert again['error']=='provider_authentication_required'
    refused=dispatch(store,service,store.owner(),'approve',{'action_id':pending['id'],'expected_hash':pending['hash'],'confirm':True})
    assert refused['error']=='provider_authentication_required'
    assert api.get(pending['id'])['status']=='proposed' and api.get(pending['id'])['hash']==pending['hash']
    assert sum(f['opcode']==19 for f in t.calls)==1
    assert not any(f['opcode'] in {64,67,92,178} for f in t.calls)
    store.close()


def test_first_max_login_refusal_during_approval_is_known_unsubmitted(tmp_path):
    """Focused integration expected1s/max5s: first login fails before mutation."""
    from universal_userio.messenger import dispatch
    store,t,service,cid,api=setup(tmp_path)
    action=api.propose('max',cid,'max-0-123','reply',{'text':'unsent'},'first-auth-refusal-1234')
    old_send=t.send
    def reject_login(ws,text):
        frame=json.loads(text)
        if frame['opcode']==19:
            t.calls.append(frame)
            t.frames.append(dict(frame,cmd=3,payload={'error':'login.token'}))
        else:old_send(ws,text)
    t.send=reject_login
    result=dispatch(store,service,store.owner(),'approve',{'action_id':action['id'],'expected_hash':action['hash'],'confirm':True})
    assert result['error']=='provider_authentication_required'
    assert result['reconnect_required'] is True
    assert api.get(action['id'])['status']=='proposed' and api.get(action['id'])['receipt'] is None
    assert sum(f['opcode']==19 for f in t.calls)==1
    assert not any(f['opcode'] in {64,67,92,178} for f in t.calls)
    store.close()


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
    assert api.capabilities('max')['operations']['typing']['stop_mode']=='expires_automatically'
    for action,payload in [('react',{'emoji':'👍'}),('typing',{'active':True}),('typing',{'active':False})]:
        a=api.propose(
            'max',cid,'max-0-123',action,payload,'max-rich-123456789-'+action+str(payload.get('active','reaction')))
        assert api.approve(a['id'],a['hash'],True)['status']=='accepted'
    reaction=next(f for f in t.calls if f['opcode']==178)
    assert reaction['payload']=={'chatId':0,'messageId':123,'reaction':{'reactionType':'EMOJI','id':'👍'}}
    assert len([f for f in t.calls if f['opcode']==65])==1
    assert not any(f['opcode']==92 for f in t.calls)
    store.close()



def test_remove_reaction_uses_native_cancel_without_read_ack(tmp_path):
    store,t,service,cid,api=setup(tmp_path)
    action=api.propose('max',cid,'max-0-123','react',{'emoji':''},'max-remove-reaction-123456')
    result=api.approve(action['id'],action['hash'],True)
    assert result['status']=='accepted'
    cancelled=[f for f in t.calls if f['opcode']==179]
    assert len(cancelled)==1 and cancelled[0]['payload']=={'chatId':0,'messageId':123}
    assert not any(f['opcode'] in {92,178} for f in t.calls)
    store.close()


def test_saved_messages_resolve_and_reaction_readback_use_native_zero_peer(tmp_path):
    store,t,service,cid,api=setup(tmp_path)
    resolved=api.resolve('max','0','123')
    assert resolved['conversation_id']==cid
    assert resolved['message']['reaction_info']=={'totalCount':0}
    read=next(f for f in t.calls if f['opcode']==180)
    assert read['payload']=={'chatId':0,'messageIds':[123]}
    assert not any(f['opcode'] in {64,65,67,92,178,179} for f in t.calls)
    store.close()


@pytest.mark.parametrize('error',[ValueError('auxiliary read unavailable'),
                                  websocket.WebSocketConnectionClosedException('auxiliary closed')])
def test_auxiliary_reaction_read_failure_does_not_break_normal_source(tmp_path,error):
    store,t,service,cid,api=setup(tmp_path)
    def fail(**kwargs):raise error
    service.max_client.reactions=fail
    resolved=api.resolve('max','0','123')
    assert resolved['message']['text']=='before'
    assert resolved['message']['reaction_readback']['status']=='unavailable'
    assert 'reaction_info' not in resolved['message']
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


def test_sticker_reference_uses_server_attachment_not_set_local_shortcut(tmp_path):
    store,t,service,cid,api=setup(tmp_path)
    row={'id':'124','sender':'77','text':'','time':1700000003000,
         'attaches':[{'_type':'STICKER','stickerId':4830150843,'setId':44,'stickerType':'STATIC'}]}
    t.rows[124]=row
    from universal_userio.max_ingress import message_envelope
    from universal_userio.adapters import inbox_message_from_envelope
    service.receive(inbox_message_from_envelope(message_envelope(row,chat_id='0',own_id='77',
        reconciliation=True),received_at=1),route_id='max',account_ref='max')
    a=api.propose('max',cid,'max-0-123','sticker',{'media_message_id':'max-0-124',
        'attachment_index':0},'max-sticker-reference-123456')
    accepted=api.approve(a['id'],a['hash'],True)
    assert accepted['status']=='accepted'
    send=next(f['payload']['message'] for f in t.calls if f['opcode']==64)
    assert send['attaches']==row['attaches']
    assert 'stickerId' not in send and 'setId' not in send
    saved=store.attachments_for_message(source='max',message_id='max-0-900719925474099311')
    assert saved[0]['attachment_id']=='4830150843' and saved[0]['kind']=='sticker'
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


@pytest.mark.parametrize('limit', [2, 100])
def test_max_inclusive_history_boundary_does_not_hide_older_pages(tmp_path, limit):
    """Focused integration expected1s/max5s: native from includes boundary.

    Real SDK limit2 stalled after page2 despite a known older source. Reproduce
    the same inclusive frame and preserve lookahead within native100 limit.
    """
    store,t,service,cid,api=setup(tmp_path)
    t.inclusive_history=True
    for n in range(124,244):
        t.rows[n]={'id':str(n),'sender':'88','text':'older '+str(n),'time':1700000000000+n*1000}
    first=api.history('max',cid,limit=limit,refresh=True)
    assert first['next_cursor']
    second=api.history('max',cid,limit=limit,cursor=first['next_cursor'],refresh=True)
    if limit==2:
        assert second['next_cursor'], 'boundary consumed lookahead and hid existing older messages'
        third=api.history('max',cid,limit=limit,cursor=second['next_cursor'],refresh=True)
        assert third['messages'] and not set(m['source']['message_id'] for m in third['messages']) & set(m['source']['message_id'] for m in second['messages'])
    else:
        assert len(first['messages'])==98 and first['page_limit']==98
        messages=first['messages']+second['messages']
        assert len(messages)==len(t.rows) and len({m['source']['message_id'] for m in messages})==len(messages)
    assert all(f['payload']['backward']<=100 for f in t.calls if f['opcode']==49)
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
