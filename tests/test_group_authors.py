"""Focused integration: sender names belong to each group message; max30s."""
from universal_userio.adapters import inbox_message_from_envelope
from universal_userio.max_ingress import backfill, group_sender_names
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore

def test_max_backfill_keeps_real_sender_names_and_receipts_on_reconciliation(tmp_path):
    class Client:
        profile={'profile':{'contact':{'id':77}}}
        def chats(self,**kw):return [{'id':100,'type':'GROUP','title':'Team','participants':{'1':{},'2':{}}}]
        def contacts(self,ids):return [{'id':i,'names':[{'name':{1:'Анна Иванова',2:'Борис Петров'}[i]}]} for i in ids]
        def history(self,*a,**kw):return [{'id':i,'sender':i,'time':1791756630808+i,'text':'body'} for i in [1,2]]
    store=SQLiteUserIOStore(tmp_path/'db');service=UserIOService(store,object(),object())
    class Sink:
        def send_max_message(self,*,envelope,account_id,route_id):
            service.receive(inbox_message_from_envelope(envelope,received_at=1),route_id=route_id,account_ref=account_id)
    sink=Sink();backfill(sink,Client());chat=store.conversations(source='max')[0]
    first=store.conversation(chat['id']);ids=[m['message_id'] for m in first['messages']]
    assert first['conversation_kind']=='group'
    assert [m['sender_name'] for m in first['messages']]==['Анна Иванова','Борис Петров']
    assert [m['sender'] for m in first['messages']]==['1','2']
    backfill(sink,Client());again=store.conversation(chat['id'])
    assert [m['message_id'] for m in again['messages']]==ids
    assert not any(m['edited_at'] for m in again['messages'])

def test_max_sender_profile_cache_batches_and_retains_username():
    class Client:
        def contacts(self,ids):
            calls.append(ids);return [{'id':i,'names':[{'name':'Person '+str(i)}],'username':'user'+str(i)} for i in ids]
    calls=[];cache={};rows=[{'sender':i} for i in range(205)]
    group_sender_names(Client(),rows,cache)
    assert [len(c) for c in calls]==[100,100,5]
    assert cache['1']=='Person 1 · @user1'
    group_sender_names(Client(),rows,cache);assert len(calls)==3

def test_partial_max_profile_is_retried_after_bounded_delay(monkeypatch):
    now=[10.0];calls=[];cache={};retry={}
    monkeypatch.setattr('universal_userio.max_ingress.time.monotonic',lambda:now[0])
    class Client:
        def contacts(self,ids):
            calls.append(ids)
            return [] if len(calls)==1 else [{'id':1,'names':[{'name':'Анна Иванова'}]}]
    client=Client();rows=[{'sender':1}]
    group_sender_names(client,rows,cache,retry);assert not cache.get('1')
    group_sender_names(client,rows,cache,retry);assert len(calls)==1
    now[0]=41.0
    group_sender_names(client,rows,cache,retry)
    assert cache['1']=='Анна Иванова' and not retry
