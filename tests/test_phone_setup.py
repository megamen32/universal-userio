"""Focused integration: owned-phone onboarding; expected3/max30 seconds."""
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from universal_userio.channels.sms_gateway import AndroidSmsGatewayClient
from universal_userio.contracts import InboxMessage
from universal_userio.http_api import handler
from universal_userio.phone_setup import connect_phone, phone_binding, phone_device_alias
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Gateway:
    def __init__(self):self.enrollments=[];self.sends=[]
    def enroll(self,**kw):
        self.enrollments.append(kw)
        return {**kw,'gateway_device_id':kw['account_id'][4:],'device_token':'fixture-device-only'}
    def send(self,**kw):self.sends.append(kw);return 'fixture-receipt'


def test_two_users_own_same_physical_id_independently_and_send_exact_device(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'db');gw=Gateway()
    svc=UserIOService(store,object(),object(),sms_gateway=gw,sms_user_id=store.default_user_id)
    alice,_=store.create_user('alice-phone','password-123')
    bob,_=store.create_user('bob-phone','password-123')
    a=connect_phone(svc,alice.user_id,{'device_id':'android-1234abcd','user_id':bob.user_id})
    b=connect_phone(svc,bob.user_id,{'device_id':'android-1234abcd'})
    assert a['account_id']!=b['account_id']
    assert gw.enrollments[0]['user_id']==alice.user_id
    assert len(store.accounts(user_id=alice.user_id))==len(store.accounts(user_id=bob.user_id))==1
    assert store.accounts()==[]
    cid,_=svc.receive(InboxMessage('sms','incoming','+79991234567','hello',1),route_id='sms',user_id=alice.user_id,account_ref=a['account_id'])
    draft=svc.create_manual_draft(cid,body='reply',user_id=alice.user_id)
    svc.approve(draft.id,user_id=alice.user_id)
    assert gw.sends==[{'to':'+79991234567','body':'reply','device_id':a['account_id'][4:],'idempotency_key':draft.id}]
    assert phone_binding(svc,alice.user_id,a['account_id'])['registered']
    assert not phone_binding(svc,bob.user_id,a['account_id'])['registered']
    store.delete_account(a['account_id'],user_id=alice.user_id)
    assert not phone_binding(svc,alice.user_id,a['account_id'])['registered']


def test_other_user_cannot_select_operator_native_phone(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'db');gw=Gateway()
    svc=UserIOService(store,object(),object(),sms_gateway=gw,sms_user_id=store.default_user_id)
    user,_=store.create_user('phone-user','password-123')
    store.bind_channel_route(user_id=user.user_id,source='sms',route_id='sms')
    store.register_account(account_id='sms:borrowed',provider='sms',display_name='bad',can_read=True,can_reply=True,credential_ref='sms-native:default',user_id=user.user_id)
    cid,_=svc.receive(InboxMessage('sms','incoming','+79991234567','hi',1),route_id='sms',user_id=user.user_id,account_ref='sms:borrowed')
    draft=svc.create_manual_draft(cid,body='reply',user_id=user.user_id)
    with pytest.raises(ValueError,match='not bound'):svc.approve(draft.id,user_id=user.user_id)
    assert gw.sends==[]


def test_first_phone_enrollment_has_real_binding_and_failure_restores_metadata(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'db');gw=Gateway()
    svc=UserIOService(store,object(),object(),sms_gateway=gw)
    user,_=store.create_user('first-phone','password-123')
    def enroll(**kw):
        assert phone_binding(svc,kw['user_id'],kw['account_id'])['registered']
        raise ValueError('gateway unavailable')
    gw.enroll=enroll
    with pytest.raises(ValueError,match='gateway unavailable'):
        connect_phone(svc,user.user_id,{'device_id':'android-first'})
    assert store.accounts(user_id=user.user_id)==[]
    account='sms:'+phone_device_alias(user.user_id,'android-first')
    store.register_account(account_id=account,provider='sms',display_name='Original',
        can_read=True,can_reply=False,credential_ref='sms-agent:'+account[4:],enabled=False,user_id=user.user_id)
    before=store.accounts(user_id=user.user_id)
    with pytest.raises(ValueError,match='gateway unavailable'):
        connect_phone(svc,user.user_id,{'device_id':'android-first','display_name':'changed'})
    assert store.accounts(user_id=user.user_id)==before


def test_enroll_client_accepts_created_response_and_exact_subject():
    calls=[]
    class Response:
        status=201
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return b'{"device_token":"fixture-device-only"}'
    def runner(request,**kw):calls.append(request);return Response()
    client=AndroidSmsGatewayClient('http://gateway','operator',runner=runner)
    assert client.enroll(user_id='alice',device_id='android-1234',account_id='sms:alias')['device_token']=='fixture-device-only'
    assert json.loads(calls[0].data)=={'user_id':'alice','device_id':'android-1234','account_id':'sms:alias'}


def test_http_setup_current_user_and_service_binding_and_ingress(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'db');svc=UserIOService(store,object(),object(),sms_gateway=Gateway())
    alice,alice_token=store.create_user('alice-http-phone','password-123')
    bob,bob_token=store.create_user('bob-http-phone','password-123')
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(svc,token='operator-token'))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def request(path,token,payload=None):
        req=Request(f'http://127.0.0.1:{server.server_port}'+path,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},data=None if payload is None else json.dumps(payload).encode())
        try:
            with urlopen(req,timeout=5) as r:return r.status,json.load(r)
        except HTTPError as e:return e.code,json.load(e)
    try:
        with urlopen(f'http://127.0.0.1:{server.server_port}/connect-phone',timeout=5) as public:
            assert public.status==200
            assert 'Скачать SMS Agent APK' in public.read().decode()
        assert request('/v1/sms/device-connect','operator-token',{'device_id':'android-1234'})[0]==403
        status,pair=request('/v1/sms/device-connect',alice_token,{'device_id':'android-1234','user_id':bob.user_id})
        assert status==201 and pair['account_id']=='sms:'+phone_device_alias(alice.user_id,'android-1234')
        path=f"/v1/sms/agent-binding?user_id={alice.user_id}&account_id={pair['account_id']}"
        assert request(path,bob_token)[0]==403
        assert request(path,'operator-token')[1]['registered']
        status,_=request('/v1/messages','operator-token',{'account_id':pair['account_id'],'route_id':'sms','message':{'schema':'universal.inbox.message.v1','source':'sms','message_id':'one','sender':'900','body':'from phone','reconciliation':True}})
        assert status==202
        assert len(store.conversations(source='sms',user_id=alice.user_id))==1
        assert store.conversations(source='sms',user_id=bob.user_id)==store.conversations(source='sms')==[]
    finally:server.shutdown();server.server_close();thread.join(timeout=5)
