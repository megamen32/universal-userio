"""Focused integration expected2s/max10s: personal WA uses its own sender
and group peer, even though the message author is another group member.
"""
import json
from universal_userio.contracts import InboxMessage
from universal_userio.store import SQLiteUserIOStore
from universal_userio.service import UserIOService
from universal_userio.channels.whatsapp import WhatsAppBridgeClient


def test_personal_group_send_is_account_bound_not_author_addressed(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'wa.db');user,_=store.create_user('wa-fixture','fixture-only-password')
    class Sender:
        def __init__(self):self.calls=[]
        def send(self,**kwargs):self.calls.append(kwargs);return 'accepted-fixture-id'
    sender=Sender();service=UserIOService(store,object(),object(),whatsapp_outbox=sender)
    ref='whatsapp:1234567:account-2'
    store.register_account(account_id=ref,provider='whatsapp',display_name='mine',can_read=True,
        can_reply=True,credential_ref='whatsapp-qr:account-2',user_id=user.user_id)
    store.bind_channel_route(user_id=user.user_id,source='whatsapp',route_id='whatsapp-reply')
    cid,_=service.receive(InboxMessage('whatsapp',ref+'|group@g.us:in1','member@s.whatsapp.net',
        'incoming',1000,peer_id='group@g.us',conversation_kind='group',reconciliation=True),
        route_id='whatsapp-reply',user_id=user.user_id,account_ref=ref)
    result=service.send_manual_message(cid,body='new reply',request_id='fixture-personal-wa-send-0001',user_id=user.user_id)
    assert result.receipt=='accepted-fixture-id'
    assert sender.calls==[dict(chat_id='group@g.us',text='new reply',user_id=user.user_id,account_ref=ref)]
    assert store.conversations(source='whatsapp',user_id=store.default_user_id)==[]


def test_bridge_client_transmits_exact_account_and_user_without_guessing():
    calls=[]
    class Reply:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self):return b'{"messageId":"fixture-id"}'
    def runner(req,**kwargs):calls.append(json.loads(req.data));return Reply()
    client=WhatsAppBridgeClient('http://127.0.0.1:1',runner=runner,token='fixture-token')
    assert client.send(chat_id='group@g.us',text='reply',user_id='alice',account_ref='whatsapp:1234567:account-2')=='fixture-id'
    assert calls==[dict(chatId='group@g.us',message='reply',user_id='alice',account_ref='whatsapp:1234567:account-2')]
