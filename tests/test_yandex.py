"""Fast unit: Xiva codec and account/author projections; expected2s/max20s."""
import json
import pytest
from universal_userio.yandex.client import packet, decode
from universal_userio.yandex.sync import project, poll

ACCOUNT={'account_id':'yandex:100000001','user_id':'test-owner','guid':'own-guid'}
def chat(author='other-guid',peer='group-test'):
    return {'ChatId':peer,'ChatInfo':{'Name':'Synthetic group'},'Messages':[
        {'ServerMessage':{'ServerMessageInfo':{'Timestamp':1700000000000000,
          'From':{'Guid':author,'DisplayName':'Test author'}},
          'ClientMessage':{'Plain':{'Text':{'MessageText':'Synthetic message'}}}}}]}

def test_wire_packet_roundtrip():
    data=packet(5,'history',{'Limit':20})
    assert decode(data)==(1,[0,5,'history'],{'Limit':20})

@pytest.mark.parametrize('data',[b'',b'\x01',b'\x01\x93\x00',b'\x01\x92\x00\x01'])
def test_truncated_packet_refused(data):
    with pytest.raises((ValueError,IndexError)):decode(data)

def test_authors_do_not_become_chat_destination():
    a=list(project(chat(),ACCOUNT))[0]
    b=list(project(chat(author='second-guid'),ACCOUNT))[0]
    assert a.sender!=b.sender and a.peer_id==b.peer_id=='group-test'
    assert a.conversation_kind=='group' and a.reconciliation
    assert a.message_id.startswith(ACCOUNT['account_id']+'|')

def test_owner_direction_and_private_chat():
    value=chat(author='own-guid',peer='11111111-1111-1111-1111-111111111111_22222222-2222-2222-2222-222222222222')
    value['PartnerInfo']={'Guid':'other-guid','DisplayName':'Synthetic partner'}
    message=list(project(value,ACCOUNT))[0]
    assert message.direction=='outgoing' and message.conversation_kind=='direct'
    assert message.received_at==1700000000
    assert message.conversation_title=='Synthetic partner'
    assert message.body=='Synthetic message'

def test_initial_history_never_dispatches_old_messages(monkeypatch):
    from universal_userio.yandex import sync
    class Store:
        prefs={}
        def user_preference(self,key,**kwargs):return self.prefs.get(key)
        def set_user_preference(self,key,value,**kwargs):self.prefs[key]=value
    class Service:
        _store=Store()
        messages=[]
        def receive(self,message,**kwargs):
            self.messages.append(message)
            assert kwargs['user_id']=='test-owner'
            assert kwargs['account_ref']==ACCOUNT['account_id']
            return 'conversation-test',True
    monkeypatch.setattr(sync,'read',lambda *args,**kwargs:{'chats':[] if kwargs else [chat()]})
    service=Service()
    assert poll(service,ACCOUNT)['initial_history']
    assert service.messages[0].reconciliation
    assert not poll(service,ACCOUNT)['initial_history']
    assert not service.messages[1].reconciliation


def test_group_identity_names_and_tenant_isolation(tmp_path):
    from universal_userio.service import UserIOService
    from universal_userio.store import SQLiteUserIOStore
    store=SQLiteUserIOStore(tmp_path/'test.db')
    service=UserIOService(store,None,None)
    account=dict(ACCOUNT,user_id=store.default_user_id)
    store.register_account(account_id=account['account_id'],provider='yandex',
        display_name='Synthetic Yandex',can_read=True,can_reply=False,
        credential_ref='test-only',user_id=account['user_id'])
    ids=[]
    for author in ['other-guid','second-guid']:
        c=chat(author);c['Messages'][0]['ServerMessage']['ServerMessageInfo']['Timestamp']+=len(ids)
        message=list(project(c,account))[0]
        cid,_=service.receive(message,route_id='manual',user_id=account['user_id'],account_ref=account['account_id'])
        ids.append(cid)
    assert ids[0]==ids[1]
    result=store.conversation(ids[0],user_id=account['user_id'])
    assert result['display_name']=='Synthetic group'
    assert len(result['messages'])==2
    assert result['messages'][0]['sender_name']=='Test author'
    assert store.conversation(ids[0],user_id='different-tenant') is None
    store.close()


def test_global_latest_message_does_not_mark_history_complete(monkeypatch):
    from universal_userio.yandex import sync
    class Store:
        prefs={}
        def user_preference(self,key,**kwargs):return self.prefs.get(key)
        def set_user_preference(self,key,value,**kwargs):self.prefs[key]=value
    class Service:
        _store=Store()
        def receive(self,*args,**kwargs):return 'test-chat',True
    calls=[]
    def read(account,**kwargs):
        calls.append(kwargs)
        return {'chats':[chat()]} if not kwargs else {'chats':[]}
    monkeypatch.setattr(sync,'read',read)
    sync.poll(Service(),ACCOUNT)
    assert calls==[{}, {'chat_id':'group-test','before':1700000000000000,'limit':50}]


def test_projection_repair_preserves_message_id_without_fake_edit(tmp_path):
    from dataclasses import replace
    from universal_userio.service import UserIOService
    from universal_userio.store import SQLiteUserIOStore
    store=SQLiteUserIOStore(tmp_path/'test.db');service=UserIOService(store,None,None)
    message=list(project(chat(peer='0/0/synthetic-room'),ACCOUNT))[0]
    cid,_=service.receive(replace(message,body="{'MessageText': 'Synthetic message'}",conversation_kind='direct'),route_id='manual',account_ref=ACCOUNT['account_id'])
    service.receive(message,route_id='manual',account_ref=ACCOUNT['account_id'])
    result=store.conversation(cid)
    assert result['conversation_kind']=='group'
    assert len(result['messages'])==1
    assert result['messages'][0]['body']=='Synthetic message'
    assert result['messages'][0]['edited_at'] is None
    assert result['messages'][0]['received_at']==1700000000
    store.close()
