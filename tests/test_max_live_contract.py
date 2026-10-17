"""Fast unit/focused integration; expected 3s, maximum 30s, provider sends zero."""
from __future__ import annotations
import json
import threading
import pytest
from universal_userio.channels.max_client import MaxClient, OP_DISPATCH
from universal_userio.max_ingress import message_envelope, backfill, run_loop
from universal_userio.adapters import inbox_message_from_envelope
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore

class Transport:
    def __init__(self):
        self.sent = []
        self.frames = []
    def connect(self, timeout):
        return type('Ws', (), {'connected': True})()
    def close(self, ws):
        ws.connected = False
    def send(self, ws, text):
        frame = json.loads(text)
        self.sent.append(frame)
        op = frame['opcode']
        data = {'profile': {'contact': {'id': 77}}, 'chats': []} if op == 19 else {'ready': True}
        if op == 53:
            self.frames.extend([
                {'cmd': 1, 'seq': frame['seq']-1, 'opcode': op, 'payload': {'chats': [{'id': 999}]}},
                {'cmd': 0, 'seq': 999, 'opcode': OP_DISPATCH, 'payload': {'message': {'id': 2}}},
            ])
            data = {'chats': [{'id': 0}]}
        if op == 49:
            data = {'messages': []}
        self.frames.append({'cmd': 1, 'seq': frame['seq'], 'opcode': op, 'payload': data})
    def recv(self, ws, timeout):
        return self.frames.pop(0) if self.frames else None


def test_cold_command_connects_without_recursive_lock_deadlock_and_keeps_events():
    t = Transport(); c = MaxClient('test-token','device',transport=t)
    assert c.chats() == [{'id': 0}]
    assert c.dispatch(timeout=0) == {'message': {'id': 2}}
    c.history(0, backward=200)
    assert t.sent[-1]['payload']['from'] > 0
    assert t.sent[-1]['payload']['backward'] == 100
    c.close()
    assert c.chats() == [{'id': 0}]


def test_no_ping_ack_feedback_loop():
    t = Transport(); c = MaxClient('test-token','device',transport=t); c.connect()
    t.frames.append({'cmd': 1,'seq': 100,'opcode': 1,'payload': {}})
    c.dispatch(timeout=.001)
    assert not any(f['opcode'] == 1 for f in t.sent)


def test_send_missing_provider_receipt_is_not_success():
    c = MaxClient('test-token','device',transport=Transport())
    with pytest.raises(RuntimeError,match='no message receipt'):
        c.send(chat_id=0, text='test')


def test_projection_keeps_zero_chat_sender_time_whitespace_outgoing_and_media():
    e = message_envelope({'id': 7,'sender': 77,'time': 1791294307109,'text': '  same \n',
                          'attaches': [{'_type':'FILE','name':'x','fileId': 5,'token':'NEVER_PROJECT'}]},
                         chat_id='0',own_id='77',reconciliation=True)
    assert e['peer_id'] == '0' and e['sender'] == '77'
    assert e['received_at'] == 1791294307.109
    assert e['direction'] == 'outgoing' and e['reconciliation']
    assert 'NEVER_PROJECT' not in json.dumps(e)
    m = inbox_message_from_envelope(e, received_at=1)
    assert m.source == 'max' and m.body == '  same \n'
    assert m.attachments[0]['attachment_id'] == 5


def test_group_members_and_own_reply_share_exact_chat_and_do_not_reply_to_member(tmp_path):
    store = SQLiteUserIOStore(tmp_path/'db.sqlite')
    store.register_account(account_id='max', provider='max', display_name='MAX fixture',
                           can_read=True, can_reply=True, credential_ref='fixture', enabled=True)
    sent=[]
    class Client:
        def send(self, *, chat_id, text):
            sent.append((chat_id,text)); return {'message_id':'123'}
    service = UserIOService(store,object(),object(),max_client=Client(),
                            max_user_id=store.default_user_id,max_manual_approve=True)
    convs=[]
    for n,sender in enumerate(['10','11','77']):
        e = message_envelope({'id':n,'sender':sender,'time':1791294307109+n,'text':'hello'},
                             chat_id='-5',own_id='77',kind='group',reconciliation=True)
        m = inbox_message_from_envelope(e,received_at=1)
        conv,accepted=service.receive(m,route_id='max',account_ref='max')
        convs.append(conv); assert accepted
        assert not service.receive(m,route_id='max',account_ref='max')[1]
    assert len(set(convs)) == 1
    assert store.conversation(convs[0])['peer_id'] == '-5'
    from universal_userio.adapters import UnifiedChannels
    draft=UnifiedChannels(store,service,store.default_user_id).adapter('max').send(chat_id=convs[0],text='reply')
    assert sent == []
    assert service.approve(draft.id,user_id=store.default_user_id).receipt == '123'
    assert sent == [('-5','reply')]


def test_backfill_includes_saved_messages_chat_zero():
    class Client:
        profile={'profile':{'contact':{'id':77}}}
        def chats(self, **kw):return [{'id':0,'type':'DIALOG'}]
        def history(self, chat, **kw):return [{'id':1,'sender':77,'text':'saved','time':1791294307109}]
    class Sink:
        def send_max_message(self, **kw):self.message=kw['envelope']
    sink=Sink()
    assert backfill(sink,Client()) == 1
    assert sink.message['peer_id'] == '0'
    assert sink.message['direction'] == 'outgoing'


def test_reconnect_failure_is_bounded_and_shutdown_closes_own_socket():
    stop=threading.Event()
    class Client:
        def connect(self):stop.set();raise OSError('offline')
        def close(self):self.closed=True
    c=Client()
    run_loop(object(),c,stop=stop)
    assert c.closed


def test_first_message_from_new_chat_resolves_contact_before_projection():
    stop=threading.Event()
    class Client:
        profile={'profile':{'contact':{'id':77}}}
        def connect(self):return self.profile
        def chats(self,**kw):return [{'id':0,'type':'DIALOG'}]
        def contacts(self,ids):return [{'id':88,'names':[{'name':'Дарья'}]}] if ids else []
        def get_chat(self,chat_id):
            assert chat_id=='900';return {'id':900,'type':'DIALOG','participants':{'77':0,'88':0}}
        def dispatch(self,**kw):return {'chatId':900,'message':{'id':1,'sender':88,'time':1000,'text':'hello'}}
        def close(self):pass
    class Sink:
        def register_account(self,**kw):pass
        def send_max_message(self,**kw):self.envelope=kw['envelope'];stop.set()
    sink=Sink();run_loop(sink,Client(),depth=0,stop=stop)
    assert sink.envelope['conversation_title']=='Дарья'
    assert sink.envelope['conversation_kind']=='direct'
    assert sink.envelope['peer_id']=='900' and sink.envelope['message_id']=='max-900-1'


def test_sticker_projection_keeps_public_thumbnail_without_credentials():
    e=message_envelope({'id':1,'sender':2,'time':1000,'text':'',
        'attaches':[{'_type':'STICKER','stickerId':55,
                     'url':'https://i.oneme.ru/getSmile?smileId=55&smileType=sticker',
                     'token':'NEVER_PROJECT'}]},chat_id='3',own_id='4')
    assert e['body']=='' and e['attachments'][0]['kind']=='sticker'
    assert e['attachments'][0]['src']=='https://i.oneme.ru/getSmile?smileId=55&smileType=sticker'
    assert 'NEVER_PROJECT' not in json.dumps(e)


def test_projection_retains_provider_edit_time():
    e=message_envelope({'id':1,'sender':2,'time':1000,'updateTime':2000,'text':'edited'},
                       chat_id='3',own_id='4')
    assert inbox_message_from_envelope(e,received_at=1).edited_at == 2


def test_max_title_is_chat_scoped_and_independent_of_participant(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'db.sqlite')
    service=UserIOService(store,object(),object())
    for sender in ['1','2','77']:
        e=message_envelope({'id':sender,'sender':sender,'time':1000,'text':'t'},
                           chat_id='-5',own_id='77',kind='group',title='Команда')
        conv,_=service.receive(inbox_message_from_envelope(e,received_at=1),route_id='max',account_ref='max')
    assert store.conversation(conv)['display_name'] == 'Команда'
    from universal_userio.adapters import UnifiedChannels
    assert UnifiedChannels(store,service,store.default_user_id).adapter('max').list()[0]['title'] == 'Команда'


def test_live_saved_messages_dispatch_does_not_drop_chat_zero():
    from universal_userio.channels.max_client import dispatch_to_message
    r=dispatch_to_message({'chatId':0,'message':{'id':17,'sender':77,'time':1000,'text':'saved'}})
    assert r is not None and r['chat_id'] == '0'


def test_max_mcp_history_limit_bounds_cached_read_without_provider_calls(tmp_path):
    from universal_userio.mcp_dispatch import UserIOToolDispatcher
    from universal_userio.contracts import UserPrincipal
    store=SQLiteUserIOStore(tmp_path/'db.sqlite');service=UserIOService(store,object(),object())
    for i in range(3):
        e=message_envelope({'id':i,'sender':7,'time':1000+i,'text':str(i)},chat_id='0',own_id='7')
        conv,_=service.receive(inbox_message_from_envelope(e,received_at=1),route_id='max',account_ref='max')
    d=UserIOToolDispatcher(store,service)
    r=d.dispatch('userio.channels.read',{'channel':'max','chat_id':conv,'history_limit':1},
                 principal=UserPrincipal(store.default_user_id,'owner','owner'))
    assert r['ok'] and [m['body'] for m in r['chat']['messages']] == ['2']
    from universal_userio.mcp_surface import UserIOMcpSurface
    r2=UserIOMcpSurface(store,service).dispatch(
        'userio.channels.read', {'channel':'max','chat_id':conv,'history_limit':1},
        principal=UserPrincipal(store.default_user_id,'owner','owner'))
    assert r2 == r
