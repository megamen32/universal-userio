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
