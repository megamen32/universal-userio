"""Focused integration: exact call grouping/old references/data preservation; max30s."""
import pytest
from universal_userio.contracts import InboxMessage, ReplyDraft
from universal_userio.phone_grouping import phone_peer_ref
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


def setup(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'phone.sqlite3')
    store.register_account(account_id='phone100',provider='phone',display_name='S21',can_read=True,can_reply=False,credential_ref='fixture')
    service=UserIOService(store,object(),object())
    return store,service


def test_future_calls_use_person_and_account_not_call_uuid(tmp_path):
    store,svc=setup(tmp_path);peer=phone_peer_ref('phone100','+79991234567')
    a,_=svc.receive(InboxMessage('phone','a','call-a','Собеседник: A',1,peer_id=peer),route_id='phone',account_ref='phone100')
    b,_=svc.receive(InboxMessage('phone','b','call-b','Собеседник: B',2,peer_id=peer),route_id='phone',account_ref='phone100')
    other,_=svc.receive(InboxMessage('phone','c','call-c','C',3,peer_id=phone_peer_ref('phone100','+79991234568')),route_id='phone',account_ref='phone100')
    assert a==b and other!=a
    assert [m['sender'] for m in store.conversation(a)['messages']]==['call-a','call-b']


def test_historical_merge_preserves_drafts_receipts_event_ids_aliases_and_unknown(tmp_path):
    store,svc=setup(tmp_path);ids=[]
    for call,ts in [('call-a',2),('call-b',1),('unknown',3),('different',4)]:
        cid,_=svc.receive(InboxMessage('phone',call,call,call,ts),route_id='phone');ids.append(cid)
    store.add_draft(ReplyDraft('draft-a',ids[0],'pending','proposed'))
    store.add_draft(ReplyDraft('draft-b',ids[1],'accepted','proposed'))
    store.approve('draft-b','receipt-preserved')
    before=store.workspace_events(after=0)['events']
    peer=phone_peer_ref('phone100','+79991234567')
    peers={'call-a':peer,'call-b':peer,'different':phone_peer_ref('phone100','+79991234568')}
    result=store.group_phone_conversations(account_ref='phone100',call_peers=peers,labels={peer:'Contact'})
    assert result['groups']==2 and result['aliases_added']==1 and result['unmatched']==1
    assert len(store.conversations(source='phone'))==3
    canonical=store.conversation(ids[0]);assert canonical['id']==ids[1]
    assert [m['message_id'] for m in canonical['messages']]==['call-b','call-a']
    assert {d['id'] for d in canonical['drafts']}=={'draft-a','draft-b'}
    assert store.draft('draft-b').receipt=='receipt-preserved'
    assert [e['seq'] for e in store.workspace_events(after=0)['events']]==[e['seq'] for e in before]
    assert store.conversation(ids[2])['messages'][0]['message_id']=='unknown'
    store.add_draft(ReplyDraft('old-link-draft',ids[0],'another','proposed'))
    assert store.draft('old-link-draft').conversation_id==ids[1]
    assert len(store.bounded_conversation_context(ids[0],message_limit=10,token_limit=1000))==2
    again=store.group_phone_conversations(account_ref='phone100',call_peers=peers,labels={peer:'Contact'})
    assert again['aliases_added']==0 and len(store.conversations(source='phone'))==3


def test_conflicting_policy_refuses_whole_transaction(tmp_path):
    store,svc=setup(tmp_path);peer=phone_peer_ref('phone100','+79991234567')
    ids=[svc.receive(InboxMessage('phone',c,c,c,1),route_id='phone')[0] for c in ['a','b']]
    store.add_workspace_exclusion(conversation_id=ids[0],reason='preserve')
    with pytest.raises(ValueError,match='workspace_exclusions'):
        store.group_phone_conversations(account_ref='phone100',call_peers={'a':peer,'b':peer})
    assert len(store.conversations(source='phone'))==2


def test_phone_normalization_and_accounts_are_distinct():
    assert phone_peer_ref('phone100','8 (999) 123-45-67')==phone_peer_ref('phone100','+7 999 1234567')
    assert phone_peer_ref('phone100','+79991234567')!=phone_peer_ref('other-account','+79991234567')


def test_actual_phone_number_is_visible_after_existing_grouping(tmp_path):
    store,svc=setup(tmp_path)
    cid,_=svc.receive(InboxMessage('phone','call','call','ended',1),route_id='phone')
    peer=phone_peer_ref('phone100','+79991234567')
    store.group_phone_conversations(account_ref='phone100',call_peers={'call':peer},labels={peer:'Телефон · ••••4567'})
    store.group_phone_conversations(account_ref='phone100',call_peers={'call':peer},labels={peer:'Телефон · +79991234567'})
    assert store.conversation(cid)['display_name']=='Телефон · +79991234567'
    assert len(store.conversation(cid)['messages'])==1


def test_cdr_label_uses_full_normalized_destination(tmp_path):
    import csv
    from universal_userio.phone_grouping import cdr_peers
    p=tmp_path/'cdr.csv'
    with p.open('w') as stream:
        csv.writer(stream).writerow(['','','8 (999) 123-45-67','gateway-outbound','','','','AudioSocket','aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa,127.0.0.1'])
    peers,labels=cdr_peers(str(p),'phone100')
    assert labels[peers['aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa']]=='Телефон · +79991234567'


def test_join_keeps_started_ended_entries_and_each_call_reply_unchanged(tmp_path):
    from universal_userio.agentcall_ingress import AgentCallEventProjector
    from universal_userio.adapters import inbox_message_from_envelope
    store, svc = setup(tmp_path)
    projector = AgentCallEventProjector('S21')
    before = []
    ids = []
    for n, call in enumerate(('call-first', 'call-second')):
        for event in ({'event':'active'}, {'event':'transcript_final','speaker':'agent','text':'Здравствуйте'},
                      {'event':'transcript_final','speaker':'remote','text':'Да'}, {'event':'ended','reason':'hangup'}):
            projected = projector.project({**event, 'callId':call, 'at':10*n+len(before)+1})
            message = inbox_message_from_envelope(projected, received_at=projected['received_at'])
            cid, _ = svc.receive(message, route_id='phone')
            ids.append(cid)
            before.append(dict(store.conversation(cid)['messages'][-1]))
    peer = phone_peer_ref('phone100', '+79991234567')
    store.group_phone_conversations(account_ref='phone100', call_peers={c:peer for c in ('call-first','call-second')})
    assert store.conversation(ids[-1])['messages'] == before
    assert len([m for m in before if m['direction']=='system']) == 4
    assert len([m for m in before if m['direction']=='outgoing']) == 2


def test_scoped_backup_rollback_preserves_foreign_data_and_new_read_flags(tmp_path):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('phone_cli', Path(__file__).parents[1]/'scripts/reconcile_phone_conversations.py')
    cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
    store, svc = setup(tmp_path)
    ids = [svc.receive(InboxMessage('phone', c, c, c, n+1), route_id='phone')[0] for n,c in enumerate(('a','b'))]
    foreign = svc.receive(InboxMessage('max','foreign','someone','must remain',3),route_id='max')[0]
    before = store.conversation(foreign)
    peer = phone_peer_ref('phone100','+79991234567'); peers = {'a':peer,'b':peer}
    plan = store.group_phone_conversations(account_ref='phone100', call_peers=peers, dry_run=True)
    state = tmp_path/'private';state.mkdir(mode=0o700)
    result = cli.apply_with_backup(store, plan, {peer:'Contact'}, state, peers)
    assert result['backup_consistent'] is True
    store.mark_seen(source='phone',message_id='a')
    cli.rollback(store, result['receipt'])
    assert len(store.conversations(source='phone')) == 2
    assert store.conversation(ids[0])['messages'][0]['seen_at'] is not None
    assert store.conversation(foreign) == before
    assert store._connection.execute("SELECT COUNT(*) FROM contact_names WHERE source='phone' AND sender LIKE 'chat:%'").fetchone()[0] == 0
    result2 = cli.apply_with_backup(store, plan, {peer:'Contact'}, state, peers)
    svc.receive(InboxMessage('phone','new','new-call','new',4,peer_id=peer),route_id='phone',account_ref='phone100')
    with pytest.raises(ValueError,match='changed'):
        cli.rollback(store,result2['receipt'])
    assert len(store.conversation(ids[0])['messages']) == 3


def test_phone_alias_is_user_scoped_and_contact_search_uses_person(tmp_path):
    from universal_userio.search import search_conversations
    store, svc = setup(tmp_path)
    ids = [svc.receive(InboxMessage('phone',c,c,c,n+1),route_id='phone')[0] for n,c in enumerate(('a','b'))]
    peer = phone_peer_ref('phone100','+79991234567')
    store.group_phone_conversations(account_ref='phone100',call_peers={'a':peer,'b':peer},labels={peer:'Владелец'})
    assert store.resolve_conversation_id(ids[1],user_id='unrelated') == ids[1]
    assert store.conversation(ids[1],user_id='unrelated') is None
    assert [c['id'] for c in search_conversations(store,'Владелец')] == [ids[0]]
    assert store.group_phone_conversations(account_ref='phone100',call_peers={'a':peer,'b':peer})['groups'] == 0
    draft = svc.create_manual_draft(ids[1],body='retained proposed')
    assert draft.conversation_id == ids[0]


def test_grouped_phone_account_is_fixed_and_late_other_account_is_separate(tmp_path):
    store, svc = setup(tmp_path)
    store.register_account(account_id='phone200',provider='phone',display_name='Other',can_read=True,can_reply=False,credential_ref='fixture2')
    ids = [svc.receive(InboxMessage('phone',c,c,c,n+1),route_id='phone')[0] for n,c in enumerate(('a','b'))]
    peer = phone_peer_ref('phone100','+79991234567')
    store.group_phone_conversations(account_ref='phone100',call_peers={'a':peer,'b':peer})
    assert store.set_conversation_account(ids[1],'phone200') is False
    assert store.set_conversation_account(ids[1],'') is False
    assert store.set_conversation_account(ids[1],'phone100') is True
    own,_ = svc.receive(InboxMessage('phone','late-own','b','ended',5),route_id='phone',account_ref='phone100')
    other,_ = svc.receive(InboxMessage('phone','late-other','b','ended',6),route_id='phone',account_ref='phone200')
    assert own == ids[0] and other != own
    assert store.conversation(other)['account_ref'] == 'phone200'
    assert len(store.workspace_chat_rules(source='phone')) == 2


def test_live_http_old_call_link_reads_one_chronological_person_dialogue(tmp_path):
    import json
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import Request, urlopen
    from universal_userio.http_api import handler
    store, svc = setup(tmp_path)
    ids = [svc.receive(InboxMessage('phone',c,c,body,n+1,direction='system'),route_id='phone')[0]
           for n,(c,body) in enumerate((('a','Звонок начат'),('b','Звонок завершён')))]
    peer=phone_peer_ref('phone100','+79991234567')
    store.group_phone_conversations(account_ref='phone100',call_peers={'a':peer,'b':peer})
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(svc,token='fixture-token'))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        req=Request(f'http://127.0.0.1:{server.server_port}/v1/conversations/{ids[1]}',headers={'Authorization':'Bearer fixture-token'})
        with urlopen(req,timeout=3) as response: result=json.load(response)
        assert result['id']==ids[0]
        assert [m['body'] for m in result['messages']]==['Звонок начат','Звонок завершён']
        assert all(m['direction']=='system' for m in result['messages'])
    finally:
        server.shutdown();server.server_close();store.close()


@pytest.mark.parametrize('new_reference', ['policy','summary'])
def test_rollback_refuses_new_person_policy_or_summary_atomically(tmp_path,new_reference):
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('phone_cli',Path(__file__).parents[1]/'scripts/reconcile_phone_conversations.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    store,svc=setup(tmp_path)
    ids=[svc.receive(InboxMessage('phone',c,c,c,n+1),route_id='phone')[0] for n,c in enumerate(('a','b'))]
    peer=phone_peer_ref('phone100','+79991234567');peers={'a':peer,'b':peer}
    plan=store.group_phone_conversations(account_ref='phone100',call_peers=peers,dry_run=True)
    state=tmp_path/'private';state.mkdir(mode=0o700)
    result=cli.apply_with_backup(store,plan,{peer:'Contact'},state,peers)
    if new_reference=='policy':
        store.add_workspace_exclusion(conversation_id=ids[0],reason='preserve deny')
    else:
        # Model/cache evolution after application must also prevent rollback
        # from attaching a combined-person summary to one old call.
        with store._connection:
            store._connection.execute('INSERT INTO conversation_summaries(user_id,conversation_id,summary,through_message_rowid,through_message_id,summarized_message_count,updated_at) VALUES (?,?,?,?,?,?,?)',
                ('user_owner',ids[0],'Both calls summary',2,'b',2,1))
    with pytest.raises(ValueError,match='policy or cached context changed'):
        cli.rollback(store,result['receipt'])
    assert store.resolve_conversation_id(ids[1])==ids[0]
    assert len(store.conversation(ids[0])['messages'])==2


def test_phone_history_pages_expose_all_grouped_calls_without_duplicates(tmp_path):
    store,svc=setup(tmp_path);peer=phone_peer_ref('phone100','+79991234567')
    for n in range(225):
        cid,_=svc.receive(InboxMessage('phone',f'call-{n:03}',f'uuid-{n}',f'call entry {n}',n//2+1,peer_id=peer),route_id='phone',account_ref='phone100')
    conversation=store.conversation(cid)
    assert len(conversation['messages'])==200
    assert conversation['phone_history']['has_more'] is True
    page=store.phone_history_page(cid,before_message_id=conversation['phone_history']['oldest_id'],limit=100)
    assert len(page['messages'])==25 and page['has_more'] is False
    all_messages=page['messages']+conversation['messages']
    assert len({m['message_id'] for m in all_messages})==225
    assert [m['body'] for m in all_messages]==[f'call entry {n}' for n in range(225)]
    with pytest.raises(KeyError):store.phone_history_page(cid,before_message_id='call-224',user_id='other')
    with pytest.raises(ValueError):store.phone_history_page(cid,before_message_id='not-this-history')


def test_phone_history_http_uses_owned_read_capability_and_exact_cursor(tmp_path):
    import json, threading
    from http.server import ThreadingHTTPServer
    from urllib.request import Request,urlopen
    from urllib.error import HTTPError
    from universal_userio.http_api import handler
    store,svc=setup(tmp_path);peer=phone_peer_ref('phone100','+79991234567')
    for n in range(3): cid,_=svc.receive(InboxMessage('phone',str(n),str(n),str(n),n+1,peer_id=peer),route_id='phone',account_ref='phone100')
    other,other_token=store.create_user('phone-reader','correct horse battery staple')
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(svc,token='fixture-token'));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def get(bearer):
        request=Request(f'http://127.0.0.1:{server.server_port}/v1/conversations/{cid}/phone-history?before=2&limit=1',headers={'Authorization':'Bearer '+bearer})
        try:
            with urlopen(request,timeout=3) as response:return response.status,json.load(response)
        except HTTPError as e:return e.code,json.load(e)
    try:
        assert get('')[0]==401
        assert get(other_token)[0]==404
        status,page=get('fixture-token');assert status==200 and page['has_more'] is True
        assert [m['message_id'] for m in page['messages']]==['1']
        store.set_user_capability('read',False)
        assert get('fixture-token')[0]==403
    finally:server.shutdown();server.server_close();store.close()
