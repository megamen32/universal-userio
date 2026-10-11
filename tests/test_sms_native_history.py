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
