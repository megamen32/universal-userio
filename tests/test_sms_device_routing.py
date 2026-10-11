"""Focused integration: APK exact-device routing; native remains default. Max15s."""
import json
from universal_userio.channels.sms_gateway import AndroidSmsGatewayClient
from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


def test_wire_device_filter_and_draft_idempotency():
    calls=[]
    class Response:
        status=202
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({'id':'receipt','status':'queued_for_device'} if calls[-1].method=='POST' else {'messages':[{'id':'one','from':'555','body':'test','receivedAt':'2026-10-12T00:00:00Z','device_id':'own'},{'id':'foreign','from':'444','body':'foreign','receivedAt':'2026-10-12T00:00:00Z','device_id':'other'}]}).encode()
    def runner(request,**kwargs):calls.append(request);return Response()
    client=AndroidSmsGatewayClient('http://gateway','secret',runner=runner)
    assert [m.message_id for m in client.inbound(device_id='own')]==['one']
    assert calls[-1].full_url.endswith('?device_id=own')
    assert client.send(to='555',body='test',device_id='own',idempotency_key='draft-one')=='receipt'
    assert json.loads(calls[-1].data)['device_id']=='own'
    assert calls[-1].get_header('Idempotency-key')=='draft-one'
    client.send(to='555',body='native')
    assert 'device_id' not in json.loads(calls[-1].data)


def test_same_phone_on_two_apks_has_distinct_chats_and_explicit_device_send(tmp_path):
    class Gateway:
        def __init__(self):self.calls=[]
        def send(self,**args):self.calls.append(args);return 'receipt'
    store=SQLiteUserIOStore(tmp_path/'sms.sqlite3');gateway=Gateway()
    svc=UserIOService(store,object(),object(),sms_gateway=gateway,sms_user_id=store.default_user_id)
    ids=[]
    for device in ('one','two'):
        store.register_account(account_id='sms:'+device,provider='sms',display_name=device,can_read=True,can_reply=True,credential_ref='sms-agent:'+device)
        cid,_=svc.receive(InboxMessage('sms','1','555','inbound',1),route_id='sms',account_ref='sms:'+device);ids.append(cid)
    assert ids[0]!=ids[1]
    draft=svc.create_manual_draft(ids[0],body='reply')
    svc.approve(draft.id)
    assert gateway.calls==[{'to':'555','body':'reply','device_id':'one','idempotency_key':draft.id}]


def test_native_binding_preserves_old_account_id_without_apk_queue(tmp_path):
    class Gateway:
        def __init__(self):self.calls=[]
        def send(self,**args):self.calls.append(args);return 'native-receipt'
    store=SQLiteUserIOStore(tmp_path/'native.sqlite3');gw=Gateway()
    svc=UserIOService(store,object(),object(),sms_gateway=gw,sms_user_id=store.default_user_id)
    aid='sms:old-android-id'
    store.register_account(account_id=aid,provider='sms',display_name='S21',can_read=True,can_reply=True,credential_ref='sms-native:default')
    cid,_=svc.receive(InboxMessage('sms','one','555','test',1),route_id='sms',account_ref=aid)
    draft=svc.create_manual_draft(cid,body='native');svc.approve(draft.id)
    assert gw.calls==[{'to':'555','body':'native','idempotency_key':draft.id}]


def test_native_repoll_hydrates_old_history_without_duplicate(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'hydrate.sqlite3')
    svc=UserIOService(store,object(),object())
    message=InboxMessage('sms','old1','555','retained',1)
    old,_=svc.receive(message,route_id='sms',account_ref='sms')
    aid='sms:old-android'
    store.register_account(account_id=aid,provider='sms',display_name='S21',can_read=True,can_reply=True,credential_ref='sms-native:default')
    current,accepted=svc.receive(message,route_id='sms',account_ref=aid)
    assert current==old and accepted is False
    assert len(store.conversation(old)['messages'])==1
