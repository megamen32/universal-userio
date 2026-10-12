"""Focused integration: real SMS history projection, expected3/max30 seconds."""
import json
from universal_userio.channels.sms_gateway import AndroidSmsGatewayClient, SmsInboundMessage
from universal_userio.contracts import InboxMessage, ReplyDraft
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore
from universal_userio.sms_ingress import poll_once


def test_history_parser_retains_directions_and_sender_codes():
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({'messages':[{'id':'sms-native:1','contact_number':'900','body':'bank','direction':'incoming','provider_read':True,'receivedAt':'2026-10-12T00:00:00Z'},{'id':'sms-native:2','contact_number':'8 (999) 123-45-67','body':'answer','direction':'outgoing','receivedAt':'2026-10-12T00:01:00Z','receipt_id':'receipt'}],'next_after_id':2,'snapshot_id':2,'has_more':False}).encode()
    c=AndroidSmsGatewayClient('http://gateway','token',runner=lambda *a,**k:Response())
    page=c.history()
    assert [(m.message_id,m.sender,m.direction) for m in page['messages']]==[('sms-native:1','900','incoming'),('sms-native:2','+79991234567','outgoing')]
    assert page['messages'][1].outbox_receipt=='receipt'


def test_incremental_history_cursor_does_not_replay_or_publish_old_events(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'db');svc=UserIOService(store,object(),object())
    aid='sms:s21';store.register_account(account_id=aid,provider='sms',display_name='S21',can_read=True,can_reply=True,credential_ref='sms-native:default')
    class Sink:
        state=None
        def accounts(self):return [{'id':aid,'provider':'sms','enabled':True,'transport':'native','capabilities':['read']}]
        def cursor(self,key):return self.state
        def set_cursor(self,key,value):self.state=value
        def send_message(self,**kw):svc.receive(InboxMessage(kw['source'],kw['message_id'],kw['sender'],kw['body'],kw['received_at'],direction=kw['direction'],reconciliation=kw['reconciliation'],provider_read=kw['provider_read']),route_id='sms',account_ref=aid)
    class Gateway:
        def history(self,**kw):
            return {'messages':[SmsInboundMessage('sms-native:1','900','one',1,provider_read=True),SmsInboundMessage('sms-native:2','900','same',2,direction='outgoing')] if kw['after_id']==0 else [],'next_after_id':2,'snapshot_id':2,'has_more':False}
    s=Sink();g=Gateway();assert poll_once(s,g)==2;assert poll_once(s,g)==0
    rows=store.conversations(source='sms',account_ref=aid);assert len(rows)==1
    c=store.conversation(rows[0]['id']);assert len(c['messages'])==2
    assert c['messages'][0]['seen_at'] is not None
    assert all(not e['eligible'] for e in store.workspace_events(after=0)['events'])


def test_sent_store_receipt_links_only_exact_owned_approval(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'db');svc=UserIOService(store,object(),object())
    aid='sms:s21';store.register_account(account_id=aid,provider='sms',display_name='S21',can_read=True,can_reply=True,credential_ref='sms-native:default')
    cid,_=svc.receive(InboxMessage('sms','first','+79991234567','incoming',1),route_id='sms',account_ref=aid)
    store.add_draft(ReplyDraft('draft-one',cid,'same','proposed'));store.approve('draft-one','native-receipt')
    svc.receive(InboxMessage('sms','sms-native:2','+79991234567','same',2,direction='outgoing',reconciliation=True,outbox_receipt='native-receipt'),route_id='sms',account_ref=aid)
    svc.receive(InboxMessage('sms','sms-native:3','900','same',3,direction='outgoing',reconciliation=True,outbox_receipt='native-receipt'),route_id='sms',account_ref=aid)
    assert store.conversation(cid)['messages'][-1]['outbox_receipt']=='native-receipt'
    other=next(c for c in store.conversations(source='sms') if c['sender']=='900')
    assert not store.conversation(other['id'])['messages'][0].get('outbox_receipt')


def test_native_history_arriving_during_send_links_after_approval(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'db');svc=UserIOService(store,object(),object())
    aid='sms:s21';store.register_account(account_id=aid,provider='sms',display_name='S21',can_read=True,can_reply=True,credential_ref='sms-native:default')
    cid,_=svc.receive(InboxMessage('sms','first','+79991234567','incoming',1),route_id='sms',account_ref=aid)
    store.add_draft(ReplyDraft('sending',cid,'answer','proposed'))
    store.claim_draft_send('sending',user_id=store.default_user_id)
    message=InboxMessage('sms','sms-native:20','+79991234567','answer',2,direction='outgoing',reconciliation=True,outbox_receipt='native-receipt')
    svc.receive(message,route_id='sms',account_ref=aid)
    assert not store.conversation(cid)['messages'][-1].get('outbox_receipt')
    store.approve('sending','native-receipt')
    assert store.conversation(cid)['messages'][-1]['outbox_receipt']=='native-receipt'


def test_native_sms_keeps_exact_whitespace_and_repairs_prior_trim(tmp_path):
    from universal_userio.adapters import inbox_message_from_envelope
    raw={'schema':'universal.inbox.message.v1','source':'sms','message_id':'sms-native:6','sender':'900','body':'\noriginal text ', 'reconciliation':True}
    message=inbox_message_from_envelope(raw,received_at=1)
    assert message.body=='\noriginal text '
    store=SQLiteUserIOStore(tmp_path/'db');svc=UserIOService(store,object(),object())
    aid='sms:s21';store.register_account(account_id=aid,provider='sms',display_name='S21',can_read=True,can_reply=True,credential_ref='sms-native:default')
    cid,_=svc.receive(InboxMessage('sms','sms-native:6','900','original text',1,reconciliation=True),route_id='sms',account_ref=aid)
    svc.receive(message,route_id='sms',account_ref=aid)
    assert store.conversation(cid)['messages'][0]['body']=='\noriginal text '
    assert len(store.conversation(cid)['messages'])==1


def test_new_phone_incoming_and_sent_arrive_after_initial_import_and_restart(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'db'); svc=UserIOService(store,object(),object())
    aid='sms:s21'
    store.register_account(account_id=aid,provider='sms',display_name='S21',can_read=True,can_reply=True,credential_ref='sms-native:default')
    arrivals=[]
    svc.add_inbound_listener(lambda user,cid,message:arrivals.append(message.message_id))
    class Sink:
        def accounts(self):return [{'id':aid,'provider':'sms','enabled':True,'transport':'native','capabilities':['read']}]
        def cursor(self,key):return store.user_preference('source_cursor:'+key)
        def set_cursor(self,key,value):store.set_user_preference('source_cursor:'+key,value)
        def send_message(self,**kw):
            svc.receive(InboxMessage(kw['source'],kw['message_id'],kw['sender'],kw['body'],kw['received_at'],direction=kw['direction'],reconciliation=kw['reconciliation'],provider_read=kw['provider_read']),route_id='sms',account_ref=aid)
    class Gateway:
        rows=[SmsInboundMessage('sms-native:20','900','old',20)]
        def history(self,**kw):
            rows=[m for m in self.rows if int(m.message_id.split(':')[-1])>kw['after_id']]
            return {'messages':rows,'next_after_id':int(rows[-1].message_id.split(':')[-1]) if rows else kw['after_id'],'snapshot_id':22,'has_more':False}
    gateway=Gateway()
    assert poll_once(Sink(),gateway)==1
    assert arrivals==[]
    gateway.rows.extend([SmsInboundMessage('sms-native:21','900','new incoming',21),SmsInboundMessage('sms-native:22','900','sent on phone',22,direction='outgoing')])
    assert poll_once(Sink(),gateway)==2
    assert arrivals==['sms-native:21']
    assert poll_once(Sink(),gateway)==0
    cid=store.conversations(source='sms',account_ref=aid)[0]['id']
    assert [(m['message_id'],m['direction']) for m in store.conversation(cid)['messages']]==[('sms-native:20','incoming'),('sms-native:21','incoming'),('sms-native:22','outgoing')]
    assert json.loads(Sink().cursor('sms-native-history:'+aid))['after_id']==22


def test_transient_history_or_partial_sink_failure_does_not_advance_cursor(tmp_path):
    import pytest
    store=SQLiteUserIOStore(tmp_path/'db'); svc=UserIOService(store,object(),object())
    aid='sms:s21'
    store.register_account(account_id=aid,provider='sms',display_name='S21',can_read=True,can_reply=True,credential_ref='sms-native:default')
    key='sms-native-history:'+aid
    baseline=json.dumps({'after_id':20,'snapshot_id':0,'initialized':True})
    store.set_user_preference('source_cursor:'+key,baseline)
    class Sink:
        fail_on_second=True
        def accounts(self):return [{'id':aid,'provider':'sms','enabled':True,'transport':'native','capabilities':['read']}]
        def cursor(self,key):return store.user_preference('source_cursor:'+key)
        def set_cursor(self,key,value):store.set_user_preference('source_cursor:'+key,value)
        def send_message(self,**kw):
            if self.fail_on_second and kw['message_id']=='sms-native:22':raise OSError('temporary sink failure')
            svc.receive(InboxMessage(kw['source'],kw['message_id'],kw['sender'],kw['body'],kw['received_at'],direction=kw['direction'],reconciliation=kw['reconciliation']),route_id='sms',account_ref=aid)
    class Gateway:
        fail=True
        def history(self,**kw):
            if self.fail:raise TimeoutError('temporary phone outage')
            return {'messages':[SmsInboundMessage('sms-native:21','900','incoming',21),SmsInboundMessage('sms-native:22','900','outgoing',22,direction='outgoing')],'next_after_id':22,'snapshot_id':22,'has_more':False}
    sink=Sink(); gateway=Gateway()
    with pytest.raises(TimeoutError):poll_once(sink,gateway)
    assert sink.cursor(key)==baseline
    gateway.fail=False
    with pytest.raises(OSError):poll_once(sink,gateway)
    assert sink.cursor(key)==baseline
    sink.fail_on_second=False
    assert poll_once(sink,gateway)==2
    cid=store.conversations(source='sms',account_ref=aid)[0]['id']
    assert len(store.conversation(cid)['messages'])==2
    assert json.loads(sink.cursor(key))['after_id']==22


def test_history_client_rechecks_pending_ids_with_native_deadline():
    calls=[]
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({'messages':[],'pending_ids':[21],'next_after_id':22,'snapshot_id':22,'has_more':False}).encode()
    def runner(request,timeout):
        calls.append((request.full_url,timeout)); return Response()
    client=AndroidSmsGatewayClient('http://gateway','token',runner=runner)
    assert client.history(after_id=22,pending_ids=[21])['pending_ids']==[21]
    assert 'pending_ids=21' in calls[0][0]
    assert calls[0][1]==65


def test_pending_phone_send_does_not_block_new_incoming_or_get_lost(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'db');svc=UserIOService(store,object(),object())
    aid='sms:s21'; key='sms-native-history:'+aid
    store.register_account(account_id=aid,provider='sms',display_name='S21',can_read=True,can_reply=True,credential_ref='sms-native:default')
    store.set_user_preference('source_cursor:'+key,json.dumps({'after_id':20,'snapshot_id':0,'initialized':True}))
    arrivals=[];svc.add_inbound_listener(lambda user,cid,message:arrivals.append(message.message_id))
    class Sink:
        def accounts(self):return [{'id':aid,'provider':'sms','enabled':True,'transport':'native','capabilities':['read']}]
        def cursor(self,key):return store.user_preference('source_cursor:'+key)
        def set_cursor(self,key,value):store.set_user_preference('source_cursor:'+key,value)
        def send_message(self,**kw):svc.receive(InboxMessage(kw['source'],kw['message_id'],kw['sender'],kw['body'],kw['received_at'],direction=kw['direction'],reconciliation=kw['reconciliation']),route_id='sms',account_ref=aid)
    class Gateway:
        calls=[]
        def history(self,**kw):
            self.calls.append(kw)
            if kw['after_id']==20:return {'messages':[SmsInboundMessage('sms-native:22','900','new incoming',22)],'pending_ids':[21],'next_after_id':22,'snapshot_id':22,'has_more':False}
            assert kw['pending_ids']==[21]
            return {'messages':[SmsInboundMessage('sms-native:21','900','late sent',21,direction='outgoing')],'pending_ids':[],'next_after_id':22,'snapshot_id':22,'has_more':False}
    gateway=Gateway()
    assert poll_once(Sink(),gateway)==1
    state=json.loads(Sink().cursor(key));assert state['after_id']==22 and state['pending_ids']==[21]
    assert arrivals==['sms-native:22']
    assert poll_once(Sink(),gateway)==1
    assert json.loads(Sink().cursor(key))['pending_ids']==[]
    cid=store.conversations(source='sms',account_ref=aid)[0]['id']
    assert [(m['message_id'],m['direction']) for m in store.conversation(cid)['messages']]==[('sms-native:21','outgoing'),('sms-native:22','incoming')]
    assert arrivals==['sms-native:22']
