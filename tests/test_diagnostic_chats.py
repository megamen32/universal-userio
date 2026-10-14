"""Fast units: real UserIO storage/dedup/isolation; no provider or model."""
import pytest
from dataclasses import replace
from universal_userio.store import SQLiteUserIOStore
from universal_userio.diagnostic_chats import DiagnosticChats, dispatch

def test_real_conversation_messages_dedup_and_reconstruction(tmp_path):
    path = tmp_path/'userio.sqlite'
    store = SQLiteUserIOStore(path)
    subject = store.owner()
    chats = DiagnosticChats(store)
    created = chats.create(user_id=subject.user_id, request_id='diagnostic-chat-test-0001', actor_ref='real-app-member', title='Диагностический чат')
    cid = created['conversation']['id']
    assert store.conversation(cid)['source'] == 'diagnostic'
    first = chats.message(user_id=subject.user_id, conversation_id=cid, request_id='diagnostic-input-test-0001', body='Новый вопрос')
    again = chats.message(user_id=subject.user_id, conversation_id=cid, request_id='diagnostic-input-test-0001', body='Новый вопрос')
    assert again['duplicate'] and again['message_id'] == first['message_id']
    with pytest.raises(ValueError, match='conflict'):
        chats.message(user_id=subject.user_id, conversation_id=cid, request_id='diagnostic-input-test-0001', body='Подмена')
    chats.message(user_id=subject.user_id, conversation_id=cid, request_id='diagnostic-reply-test-0001', body='Ответ', direction='outgoing')
    fresh = DiagnosticChats(SQLiteUserIOStore(path)).get(user_id=subject.user_id, conversation_id=cid)
    assert [m['body'] for m in fresh['messages']] == ['Новый вопрос', 'Ответ']
    assert fresh['external_actions'] == 0 and fresh['actor_ref_is_authority'] is False
    assert len(store.conversation(cid)['messages']) == 2
    events = store.workspace_events(after=0, user_id=subject.user_id)
    assert not any(e.get('eligible') for e in events['events'])
    with pytest.raises(KeyError): chats.get(user_id='foreign-user', conversation_id=cid)

def test_scoped_credentials_do_not_gain_diagnostic_writes(tmp_path):
    store = SQLiteUserIOStore(tmp_path/'db.sqlite')
    owner = store.owner()
    for restricted in (replace(owner, read_only=True), replace(owner, messenger_accounts=('telegram:1',)), replace(owner, meeting_recorder=True)):
        with pytest.raises(PermissionError):
            dispatch(store, restricted, 'userio.diagnostic.create', {'request_id':'diagnostic-chat-test-0001'})

def test_create_identity_is_immutable(tmp_path):
    store = SQLiteUserIOStore(tmp_path/'db.sqlite'); chats = DiagnosticChats(store); uid=store.owner().user_id
    original=chats.create(user_id=uid,request_id='diagnostic-chat-test-0001',actor_ref='Oleg')
    assert chats.create(user_id=uid,request_id='diagnostic-chat-test-0001',actor_ref='Oleg')['conversation']['id']==original['conversation']['id']
    with pytest.raises(ValueError):chats.create(user_id=uid,request_id='diagnostic-chat-test-0001',actor_ref='Nikita')

def test_standard_mcp_and_real_outbox_are_separate(tmp_path):
    from universal_userio.service import UserIOService, DeliveryUnavailableError
    from universal_userio.mcp_dispatch import UserIOToolDispatcher
    from universal_userio.mcp_surface import UserIOMcpSurface
    class NoProvider:
        def send(self, *args, **kwargs):pytest.fail('real provider reached by diagnostic chat')
    store=SQLiteUserIOStore(tmp_path/'db.sqlite');service=UserIOService(store,None,NoProvider())
    dispatcher=UserIOToolDispatcher(store,service);principal=store.owner()
    surface=UserIOMcpSurface(store,service)
    assert {'userio.diagnostic.create','userio.diagnostic.get','userio.diagnostic.message'} <= {t['name'] for t in surface.tool_manifest()['tools']}
    assert not any(t['name'].startswith('userio.diagnostic.') for t in surface.tool_manifest(principal=replace(principal,read_only=True))['tools'])
    result=dispatcher.dispatch('userio.diagnostic.create',{'request_id':'diagnostic-chat-mcp-00001'},principal=principal)
    assert result['diagnostic'];cid=result['conversation']['id']
    assert surface.dispatch('userio.diagnostic.get',{'conversation_id':cid})['diagnostic'] is True
    dispatcher.dispatch('userio.diagnostic.message',{'conversation_id':cid,'request_id':'diagnostic-message-mcp-01','body':'Новый тест'},principal=principal)
    assert dispatcher.dispatch('userio.diagnostic.get',{'conversation_id':cid},principal=principal)['messages'][0]['body']=='Новый тест'
    with pytest.raises(DeliveryUnavailableError):
        service.send_manual_message(cid,body='Не отправлять',request_id='diagnostic-standard-send-01')
    store._connection.execute('DELETE FROM diagnostic_chat_requests')
    with pytest.raises(ValueError,match='conflict'):
        DiagnosticChats(store).message(user_id=principal.user_id,conversation_id=cid,request_id='diagnostic-message-mcp-01',body='Подмена после прерывания')

def test_history_is_latest_bounded_tail(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'db.sqlite');chats=DiagnosticChats(store);uid=store.owner().user_id
    cid=chats.create(user_id=uid,request_id='diagnostic-history-create')['conversation']['id']
    for i in range(202):
        chats.message(user_id=uid,conversation_id=cid,request_id=f'diagnostic-history-{i:05}',body=f'Вопрос {i}')
    history=chats.get(user_id=uid,conversation_id=cid)
    assert history['truncated'] and len(history['messages']) == 200
    assert history['messages'][0]['body']=='Вопрос 2' and history['messages'][-1]['body']=='Вопрос 201'
    assert 'messages' not in history['conversation']
