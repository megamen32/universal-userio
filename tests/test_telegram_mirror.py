from dataclasses import replace
import math

import pytest

from universal_userio.adapters import inbox_message_from_envelope
from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore

ACCOUNT = 'telegram:540308572'
PEER = '381703703'

@pytest.fixture
def mirror(tmp_path):
    store = SQLiteUserIOStore(tmp_path / 'mirror.sqlite3')
    service = UserIOService(store, object(), object())
    def ingest(mid, date, **kw):
        return service.receive(InboxMessage('telegram', f'{ACCOUNT}|{PEER}:{mid}', 'Дмитрий',
            f'message {mid}', date, conversation_kind='direct', peer_id=PEER, **kw),
            route_id='telegram', account_ref=ACCOUNT)
    yield store, service, ingest
    store.close()

def test_provider_time_direction_read_are_validated():
    envelope = dict(schema='universal.inbox.message.v1', source='telegram', message_id='1',
        sender='peer', body='body', received_at=123, direction='outgoing', provider_read=False)
    msg = inbox_message_from_envelope(envelope, received_at=999)
    assert (msg.received_at,msg.direction,msg.provider_read) == (123,'outgoing',False)
    for bad in (True, math.inf, math.nan, 0, '123'):
        with pytest.raises(ValueError):
            inbox_message_from_envelope(envelope | {'received_at':bad}, received_at=999)

def test_outgoing_mirror_never_arrives_as_inbound_or_unread(mirror):
    store,service,ingest = mirror
    calls=[]
    service.add_inbound_listener(lambda *a: calls.append(a))
    cid,_=ingest(10,100)
    assert len(calls)==1
    assert ingest(11,200,direction='outgoing',reconciliation=True)[0]==cid
    assert len(calls)==1
    assert len(store.conversation(cid)['messages'])==2
    assert store.conversations()[0]['unread_count']==1
    assert len(store.new_messages())==1
    assert store._connection.execute('SELECT COUNT(*) FROM workspace_events').fetchone()[0]==1
    assert store.owner_messages_after(cid, f'{ACCOUNT}|{PEER}:10')[0]['message_id'].endswith(':11')

def test_legacy_replay_corrects_metadata_without_renaming_or_new_event(mirror):
    store,service,ingest = mirror
    cid,_=ingest(10,999)
    with store._connection:
        store._connection.execute('UPDATE messages SET message_id=?', (f'{PEER}:10',))
        store._connection.execute('UPDATE workspace_events SET message_id=?', (f'{PEER}:10',))
    assert ingest(10,100,direction='outgoing',provider_read=True,reconciliation=True)==(cid,False)
    m=store.conversation(cid)['messages'][0]
    assert (m['message_id'],m['received_at'],m['direction'],m['provider_read'])==(f'{PEER}:10',100,'outgoing',1)
    assert store._connection.execute('SELECT COUNT(*),MAX(eligible) FROM workspace_events').fetchone()[:]==(1,0)

def test_late_history_uses_native_chronology_and_keeps_latest_event(mirror):
    store,service,ingest=mirror
    cid,_=ingest(20,200)
    ingest(10,100,reconciliation=True)
    history=store.bounded_conversation_context(cid,current_message_id=f'{ACCOUNT}|{PEER}:20')
    assert [m['message_id'] for m in history]==[f'{ACCOUNT}|{PEER}:10']
    assert store.conversation_secretary(cid)['conversation']['message_id'].endswith(':20')
    ingest(30,300)
    assert store.bounded_conversation_context(cid,current_message_id=f'{ACCOUNT}|{PEER}:20')==history

def test_read_watermarks_are_account_scoped_and_local_seen_is_distinct(mirror):
    store,service,ingest=mirror
    cid,_=ingest(10,100,provider_read=False)
    ingest(11,200,direction='outgoing',provider_read=False)
    store.mark_seen(source='telegram',message_id=f'{ACCOUNT}|{PEER}:10')
    assert store.conversations()[0]['unread_count']==1
    assert store.telegram_mirror_state(account_ref='telegram:other',peer_id=PEER,
        user_id=store.default_user_id,read_inbox_max_id=100)['changed']==0
    store.telegram_mirror_state(account_ref=ACCOUNT,peer_id=PEER,user_id=store.default_user_id,
        read_inbox_max_id=10,read_outbox_max_id=10)
    assert [m['provider_read'] for m in store.conversation(cid)['messages']]==[1,0]
    assert store.conversations()[0]['unread_count']==0
    ingest(10,100,provider_read=False,reconciliation=True)
    assert store.conversations()[0]['unread_count']==0
    store.telegram_mirror_state(account_ref=ACCOUNT,peer_id=PEER,user_id=store.default_user_id,
        read_inbox_max_id=15)
    ingest(14,140,provider_read=False,reconciliation=True)
    assert store.conversations()[0]['unread_count']==0

def test_absence_only_probes_deletion_preserves_durable_event_and_invalidates_cache(mirror):
    store,service,ingest=mirror
    cid,_=ingest(10,100)
    ingest(11,110)
    ingest(12,120)
    result=store.telegram_mirror_state(account_ref=ACCOUNT,peer_id=PEER,user_id=store.default_user_id,history_ids=[10,12])
    assert result['missing_ids']==[11]
    assert len(store.conversation(cid)['messages'])==3
    work=store.conversation_summary_work(cid)
    last=max(work['new_messages'],key=lambda m:m['message_rowid'])
    store.telegram_mirror_state(account_ref=ACCOUNT,peer_id=PEER,user_id=store.default_user_id,deleted_ids=[11])
    assert len(store.conversation(cid)['messages'])==2
    assert store.message(f'{ACCOUNT}|{PEER}:11',source='telegram') is None
    assert store._connection.execute('SELECT COUNT(*) FROM workspace_events').fetchone()[0]==3
    assert not store.save_conversation_summary(cid,summary='stale',expected_through_message_rowid=0,
        through_message_rowid=last['message_rowid'],through_message_id=last['message_id'],
        summarized_message_count=3,expected_messages=work['new_messages'])

def test_late_history_invalidates_cache_and_first_page_concurrent_save(mirror):
    store,service,ingest=mirror
    cid,_=ingest(20,200)
    work=store.conversation_summary_work(cid)
    last=work['new_messages'][-1]
    ingest(10,100,reconciliation=True)
    assert not store.save_conversation_summary(cid,summary='stale',expected_through_message_rowid=0,
        through_message_rowid=last['message_rowid'],through_message_id=last['message_id'],
        summarized_message_count=1,expected_messages=work['new_messages'])
    work=store.conversation_summary_work(cid)
    last=max(work['new_messages'],key=lambda m:m['message_rowid'])
    assert store.save_conversation_summary(cid,summary='summary',expected_through_message_rowid=0,
        through_message_rowid=last['message_rowid'],through_message_id=last['message_id'],
        summarized_message_count=2,expected_messages=work['new_messages'])
    ingest(5,50,reconciliation=True)
    assert store.conversation_summary(cid) is None

def test_history_anchor_cannot_skip_lower_rowid_future_message(mirror):
    store,service,ingest=mirror
    cid,_=ingest(20,200,direction='outgoing')
    ingest(10,100,reconciliation=True)
    work=store.conversation_summary_work(cid,through_message_id=f'{ACCOUNT}|{PEER}:10')
    assert work['new_messages']==[]
    ingest(30,300)
    work=store.conversation_summary_work(cid,through_message_id=f'{ACCOUNT}|{PEER}:30')
    assert [m['message_id'].rsplit(':',1)[-1] for m in work['new_messages']]==['10','20','30']

def test_registered_ingress_seed_can_apply_state_but_personal_token_cannot(mirror):
    import json
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from universal_userio.http_api import handler
    store,service,ingest=mirror
    cid,_=ingest(10,100,provider_read=False)
    store.register_account(account_id=ACCOUNT,provider='telegram',display_name='Nikita',
        can_read=True,can_reply=True,credential_ref='fixture',enabled=True)
    personal=store._issue_token(store.default_user_id)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(service,token='fixture-ingress-seed'))
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    payload=json.dumps(dict(account_id=ACCOUNT,peer_id=PEER,read_inbox_max_id=10)).encode()
    url=f'http://127.0.0.1:{server.server_port}/v1/telegram/mirror-state'
    try:
        with pytest.raises(HTTPError) as error:
            urlopen(Request(url,data=payload,headers={'Authorization':f'Bearer {personal}'}))
        assert error.value.code==403
        with urlopen(Request(url,data=payload,headers={'Authorization':'Bearer fixture-ingress-seed'})) as result:
            assert result.status==202
        assert store.conversation(cid)['messages'][0]['provider_read']==1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
