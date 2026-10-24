"""Fast unit: real receipt decision/HTTP seam; expected3s/max30s, no provider calls.

Detects false fallback from missing speech, pending teardown or lost receipts.
"""
import json
from pathlib import Path
import tempfile
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import Mock
import uuid

import pytest

from universal_userio import matrix_calls, noticeplace_calls, telegram_calls


PAYLOAD = {'incident_id': 'incident-test-001', 'title': 'Сервис недоступен',
           'body': 'Сервис сейчас не отвечает. Проверьте его состояние.'}


class Ledger:
    def __init__(self, stages):
        self.stages, self.active, self.records, self.starts, self.reads = stages, {}, {}, [], 0

    def start(self, **kwargs):
        self.starts.append(kwargs)
        key = kwargs['user_id'] + '\0' + kwargs['request_id'] if 'room_id' in kwargs else (
            'userio:telegram:' + kwargs['user_id'] + ':' + kwargs['request_id'])
        call_id = str(uuid.uuid5(uuid.NAMESPACE_URL, key))
        self.record = dict(kwargs, call_id=call_id)
        self.records[call_id] = self.record
        return self._next()

    def _next(self):
        self.reads += 1
        status, answered, running, extra = self.stages[min(self.reads - 1, len(self.stages) - 1)]
        self.record.update(status=status, answered=answered, **extra)
        if running:
            self.active[self.record['call_id']] = object()
        else:
            self.active.pop(self.record['call_id'], None)
        return self.record.copy()

    def get(self, call_id, user_id):
        if call_id not in self.records:
            raise KeyError('call not found')
        return self._next()


@pytest.fixture
def setup(monkeypatch):
    for key, value in {'USERIO_MATRIX_NOTICE_ACCOUNT': 'matrix:caller',
                       'USERIO_MATRIX_NOTICE_ROOM': '!test:example.invalid',
                       'USERIO_MATRIX_NOTICE_TARGET': '@test:example.invalid',
                       'USERIO_TELEGRAM_NOTICE_ACCOUNT': 'telegram:100001',
                       'USERIO_TELEGRAM_NOTICE_TARGET': '100002',
                       'USERIO_MESSENGER_RELAY_ACCOUNT': 'telegram:100003',
                       'USERIO_TELEGRAM_RELAY_PEER': '100004'}.items():
        monkeypatch.setenv(key, value)
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(noticeplace_calls, 'time', SimpleNamespace(
        monotonic=lambda: clock.now, sleep=lambda seconds: setattr(clock, 'now', clock.now + seconds)))
    def bind(stages):
        ledger = Ledger(stages)
        monkeypatch.setattr(matrix_calls, 'calls_for', lambda _: ledger)
        monkeypatch.setattr(telegram_calls, 'calls_for', lambda _: ledger)
        return ledger
    return bind


@pytest.mark.parametrize('channel', ['matrix', 'telegram'])
def test_native_answer_without_phrase_stops_chain_and_keeps_dialogue_running(setup, channel):
    ledger = setup([('active', True, True, {'acknowledged': False})])
    result = noticeplace_calls.noticeplace_call(Mock(), 'owner', PAYLOAD, channel=channel)
    assert result['ok'] and result['answered'] is True
    assert not result['acknowledged'] and not result['ended'] and not result['safe_to_fallback']
    assert ledger.reads == 1 and ledger.active
    if channel == 'telegram':
        assert ledger.starts[0]['account_ref'] == 'telegram:100001'
        assert ledger.starts[0]['target'] == result['target'] == '100002'


@pytest.mark.parametrize('key', ['USERIO_TELEGRAM_NOTICE_ACCOUNT', 'USERIO_TELEGRAM_NOTICE_TARGET'])
def test_missing_noticeplace_destination_never_uses_generic_read_peer(setup, monkeypatch, key):
    ledger = setup([])
    monkeypatch.delenv(key)
    with pytest.raises(KeyError, match=key):
        noticeplace_calls.noticeplace_call(Mock(), 'owner', PAYLOAD, channel='telegram')
    assert not ledger.starts


@pytest.mark.parametrize('channel', ['matrix', 'telegram'])
def test_false_waits_for_native_end_and_manager_teardown(setup, channel):
    ledger = setup([('ringing', False, True, {}), ('unanswered', False, True, {}),
                    ('unanswered', False, False, {})])
    result = noticeplace_calls.noticeplace_call(Mock(), 'owner', PAYLOAD, channel=channel)
    assert result['ok'] and result['answered'] is False
    assert result['ended'] and result['safe_to_fallback'] and ledger.reads == 3


@pytest.mark.parametrize('stage', [
    ('unknown', False, False, {}), ('failed', False, False, {}),
    ('accepted', False, False, {}), ('ringing', False, True, {}),
    ('unanswered', False, False, {'relay_reconciliation': 'pending'}),
    ('unanswered', False, False, {'relay_reconciliation': 'unavailable'}),
])
def test_unknown_failed_restart_timeout_or_pending_receipt_never_permit_fallback(setup, stage):
    ledger = setup([stage])
    result = noticeplace_calls.noticeplace_call(Mock(), 'owner', PAYLOAD, channel='telegram')
    assert not result['ok'] and result['answered'] is None and result['uncertain']
    assert result['error'] == 'call_outcome_unknown' and not result['safe_to_fallback']
    assert len(ledger.starts) == 1


@pytest.mark.parametrize('channel', ['matrix', 'telegram'])
def test_same_delivery_receipt_is_read_without_redial_even_after_http_loss(setup, channel):
    ledger = setup([('unknown', False, False, {})])
    payload = dict(PAYLOAD, delivery_key='delivery-test-001')
    first = noticeplace_calls.noticeplace_call(Mock(), 'owner', payload, channel=channel)
    retry = noticeplace_calls.noticeplace_call(Mock(), 'owner', dict(payload, title='Новое описание'), channel=channel)
    assert first == retry and len(ledger.starts) == 1
    noticeplace_calls.noticeplace_call(Mock(), 'owner', dict(payload, delivery_key='delivery-test-002'), channel=channel)
    assert len(ledger.starts) == 2


def test_old_matrix_receipt_preserves_legacy_id_without_greeting_conflict(setup):
    ledger = setup([('unanswered', False, False, {})])
    request_id = 'noticeplace:' + PAYLOAD['incident_id']
    ledger.record = {'call_id': str(uuid.uuid5(uuid.NAMESPACE_URL, 'owner\0' + request_id)),
                     'account_ref': 'matrix:caller', 'room_id': '!test:example.invalid',
                     'target': '@test:example.invalid', 'purpose': 'Старое полное описание',
                     'greeting': 'Старое приветствие'}
    ledger.records[ledger.record['call_id']] = ledger.record
    assert matrix_calls.noticeplace_call(Mock(), 'owner', PAYLOAD)['safe_to_fallback']
    assert not ledger.starts


@pytest.mark.parametrize('channel', ['matrix', 'telegram'])
def test_long_incident_content_is_safe_bounded_and_identifies_action(setup, channel):
    ledger = setup([('active', True, True, {})])
    payload = dict(PAYLOAD, incident_id='x' * 128, title='Сервис\x00 ' + 'я' * 990, body='я' * 12000)
    noticeplace_calls.noticeplace_call(Mock(), 'owner', payload, channel=channel)
    call = ledger.starts[0]
    assert call['agent_context']['incident_id'] == payload['incident_id']
    assert call['agent_context']['event_type'] == 'noticeplace.call'
    assert 16 <= len(call['request_id']) <= 128
    assert len(call['purpose']) <= 500 and len(call['greeting']) <= 1000
    assert '\x00' not in call['greeting'] and 'Notice Place' in call['greeting']
    assert 'Текущая ситуация:' in call['greeting'] and 'Пожалуйста, проверьте сервис' in call['greeting']


@pytest.mark.parametrize('patch', [{'title': ''}, {'body': 'x' * 12001}, {'delivery_key': 'x' * 129},
                                 {'incident_id': []}, {'title': ['invalid']}])
def test_invalid_payload_has_no_admission(setup, patch):
    ledger = setup([])
    with pytest.raises(ValueError):
        noticeplace_calls.noticeplace_call(Mock(), 'owner', dict(PAYLOAD, **patch))
    assert not ledger.starts


def test_http_both_noticeplace_routes_reuse_protected_token_and_send_capability(monkeypatch):
    from universal_userio.http_api import handler
    from universal_userio.service import UserIOService
    from universal_userio.store import SQLiteUserIOStore
    temporary = tempfile.TemporaryDirectory(prefix='noticeplace-http-')
    service = UserIOService(SQLiteUserIOStore(Path(temporary.name) / 'db.sqlite3'), Mock(), Mock())
    monkeypatch.setenv('USERIO_MATRIX_NOTICE_TOKEN', 'test-notice-token')
    deliver = Mock(return_value={'ok': True, 'answered': False, 'ended': True, 'safe_to_fallback': True})
    monkeypatch.setattr(noticeplace_calls, 'noticeplace_call', deliver)
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler(service, token='owner-test-token'))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def request(channel, token):
        req = Request(f'http://127.0.0.1:{server.server_port}/v1/calls/{channel}/noticeplace',
                      data=json.dumps(PAYLOAD).encode(), headers={'Authorization': 'Bearer ' + token,
                                                                'Content-Type': 'application/json'})
        try:
            with urlopen(req, timeout=2) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())
    try:
        for channel in ('matrix', 'telegram'):
            assert request(channel, 'wrong')[0] == 401
            assert request(channel, 'test-notice-token')[0] == 200
            assert deliver.call_args.kwargs['channel'] == channel
            assert deliver.call_args.kwargs['machine_authorized'] is True
        assert deliver.call_count == 2
        service._store.set_user_capability('send', False)
        for channel in ('matrix', 'telegram'):
            assert request(channel, 'test-notice-token') == (403, {'error': 'send_capability_disabled'})
        assert deliver.call_count == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
        temporary.cleanup()


POLICY_DESTINATION = {'account_ref':'matrix:qa-a','room_id':'!qa:hs','target':'@b:hs',
                      'participants':['@a:hs','@b:hs']}


def test_explicit_policy_destination_requires_server_authorization_not_owner_defaults(setup, monkeypatch):
    ledger=setup([('unknown',False,False,{})])
    monkeypatch.delenv('USERIO_MATRIX_NOTICE_ROUTES_FILE',raising=False)
    with pytest.raises(PermissionError):
        noticeplace_calls.noticeplace_call(Mock(),'owner',dict(PAYLOAD,destination=POLICY_DESTINATION))
    assert not ledger.starts


def test_scoped_bot_call_freezes_destination_and_uses_approved_account_owner(tmp_path,monkeypatch):
    control=tmp_path/'control.json';control.write_text(json.dumps({'socket_path':'/run/agentcall/primary88/control.sock','execution_host':'roomhacker-server-88'}));control.chmod(0o600)
    monkeypatch.setattr(matrix_calls,'CONTROL_CONFIG',control)
    route=tmp_path/'routes.json';route.write_text(json.dumps({'routes':[dict(POLICY_DESTINATION,user_id='qa-user')]}));route.chmod(0o600)
    monkeypatch.setenv('USERIO_MATRIX_NOTICE_ROUTES_FILE',str(route))
    service=Mock();service._store.default_user_id='owner';service._store.capability_enabled.return_value=True
    service._store.accounts.side_effect=lambda *,user_id:[{'id':'matrix:qa-a','provider':'matrix','enabled':True,'display_name':'QA A','credential_ref':'connector:qa'}] if user_id in {'qa-user','other-qa-owner'} else []
    monkeypatch.setattr(matrix_calls.ConnectorSecrets,'load',lambda *a,**k:{'homeserver':'https://hs','access_token':'not-real','matrix_user_id':'@a:hs'})
    monkeypatch.setattr(matrix_calls,'request_json',lambda url,*a,**k: {'joined':{'@a:hs':{},'@b:hs':{}}} if url.endswith('/joined_members') else {'user_id':'@a:hs'})
    calls=matrix_calls.MatrixCalls(service,tmp_path/'calls');monkeypatch.setattr(matrix_calls,'calls_for',lambda _:calls)
    started=[]
    def answer(record,*_):
        started.append(record['call_id']);record.update(status='active',answered=True);calls._save(record)
    monkeypatch.setattr(calls,'_run',answer)
    monkeypatch.setattr(matrix_calls.threading.Thread,'start',lambda thread:thread._target(*thread._args))
    payload=dict(PAYLOAD,destination=POLICY_DESTINATION,delivery_key='scoped-once')
    with pytest.raises(PermissionError):
        noticeplace_calls.noticeplace_call(service,'owner',payload)
    monkeypatch.setattr(matrix_calls,'request_json',lambda url,*a,**k: {'joined':{'@a:hs':{},'@b:hs':{},'@extra:hs':{}}} if url.endswith('/joined_members') else {'user_id':'@a:hs'})
    with pytest.raises(PermissionError,match='membership mismatch'):
        noticeplace_calls.noticeplace_call(service,'owner',payload,machine_authorized=True)
    assert not started and not list((tmp_path/'calls').glob('*/call.json'))
    monkeypatch.setattr(matrix_calls,'request_json',lambda url,*a,**k: {'joined':{'@a:hs':{},'@b:hs':{}}} if url.endswith('/joined_members') else {'user_id':'@a:hs'})
    first=noticeplace_calls.noticeplace_call(service,'owner',payload,machine_authorized=True)
    assert first['answered'] and first['destination']==POLICY_DESTINATION
    record=calls.get(first['call_id'],'qa-user');assert record['user_id']=='qa-user'
    assert first['call_id']==str(uuid.uuid5(uuid.NAMESPACE_URL,'owner\0'+noticeplace_calls._request(payload)[0]))
    assert record['participants']==POLICY_DESTINATION['participants']
    assert record['agent_context']=={'call_mode':'incoming_guest'}
    assert noticeplace_calls.noticeplace_call(service,'owner',payload,machine_authorized=True)['call_id']==first['call_id']
    assert len(started)==1
    route.write_text(json.dumps({'routes':[dict(POLICY_DESTINATION,user_id='other-qa-owner')]}))
    with pytest.raises(ValueError,match='request conflict'):
        noticeplace_calls.noticeplace_call(service,'owner',payload,machine_authorized=True)
    assert len(started)==1
    changed=dict(POLICY_DESTINATION,target='@c:hs',participants=['@a:hs','@c:hs'])
    route.write_text(json.dumps({'routes':[dict(changed,user_id='qa-user')]}))
    with pytest.raises(ValueError,match='destination changed'):
        noticeplace_calls.noticeplace_call(service,'owner',dict(payload,destination=changed),machine_authorized=True)
    assert len(started)==1


@pytest.mark.parametrize('mutation', ['other-target','public-config','send-disabled'])
def test_scoped_route_authorization_rejects_before_call_admission(tmp_path,monkeypatch,mutation):
    binding=dict(POLICY_DESTINATION)
    route=tmp_path/'routes.json'
    route.write_text(json.dumps({'routes':[dict(binding,user_id='qa-user')]}));route.chmod(0o600)
    monkeypatch.setenv('USERIO_MATRIX_NOTICE_ROUTES_FILE',str(route))
    service=Mock();service._store.capability_enabled.return_value=mutation!='send-disabled'
    if mutation=='public-config':route.chmod(0o644)
    if mutation=='other-target':binding.update(target='@c:hs',participants=['@a:hs','@c:hs'])
    calls=Mock();monkeypatch.setattr(matrix_calls,'calls_for',lambda _:calls)
    with pytest.raises(PermissionError):
        noticeplace_calls.noticeplace_call(service,'owner',dict(PAYLOAD,destination=binding),machine_authorized=True)
    calls.start.assert_not_called();calls.get.assert_not_called()


def test_public_json_cannot_supply_machine_admission_scope(tmp_path):
    calls=matrix_calls.MatrixCalls(Mock(),tmp_path/'calls')
    with pytest.raises(PermissionError,match='server-controlled'):
        calls.start(user_id='qa-user',account_ref='matrix:qa-a',room_id='!qa:hs',target='@b:hs',
                    purpose='QA',request_id='stable-qa-request-0001',confirm=True,
                    participants=POLICY_DESTINATION['participants'],request_scope='owner',
                    _scope_authority='owner')
    assert not (tmp_path/'calls').exists()


def test_scoped_call_requires_private_remote_control_configuration(tmp_path,monkeypatch):
    monkeypatch.setattr(matrix_calls,'CONTROL_CONFIG',tmp_path/'missing.json',raising=False)
    with pytest.raises(PermissionError):
        matrix_calls.control_route(required=True)
    config=tmp_path/'control.json';config.write_text(json.dumps({'socket_path':'/run/agentcall/primary88/control.sock','execution_host':'roomhacker-server-88'}));config.chmod(0o600)
    monkeypatch.setattr(matrix_calls,'CONTROL_CONFIG',config)
    assert matrix_calls.control_route(required=True)['execution_host']=='roomhacker-server-88'
    config.chmod(0o644)
    with pytest.raises(PermissionError):matrix_calls.control_route(required=True)
    with pytest.raises(PermissionError):matrix_calls.control_route(required=False)


def test_primary_control_stream_keeps_backend_receipt_and_transfers_account_snapshot(tmp_path,monkeypatch):
    import asyncio
    control={'socket_path':'/run/agentcall/primary88/control.sock','execution_host':'roomhacker-server-88'}
    call_id=str(uuid.uuid4());voice_id=str(uuid.uuid4())
    record={'call_id':call_id,'user_id':'qa-user','account_ref':'matrix:qa-a','history_account_ref':'matrix:qa-a',
            'room_id':'!qa:hs','target':'@b:hs','purpose':'QA','greeting':'QA','max_seconds':30,
            'status':'accepted','answered':False,'control_route':control}
    account={'homeserver':'https://hs','access_token':'fixture-only','matrix_user_id':'@a:hs'}
    identity={'user_id':'@a:hs'}
    calls=matrix_calls.MatrixCalls(Mock(),tmp_path/'receipts');(calls.root/call_id).mkdir(parents=True)
    monkeypatch.setattr(calls,'_emit',Mock())
    monkeypatch.setenv('USERIO_MATRIX_RTC_AUTH_URL','https://rtc.example.org')
    sent=[]
    class Reader:
        events=[{'event':'active','callId':voice_id},{'event':'ended'},None]
        async def readline(self):
            event=self.events.pop(0)
            return (json.dumps(event)+'\n').encode() if event else b''
    class Writer:
        def write(self,value):sent.append(json.loads(value))
        async def drain(self):pass
        def close(self):pass
        async def wait_closed(self):pass
    async def connect(path,**kwargs):
        assert path==control['socket_path']
        return Reader(),Writer()
    monkeypatch.setattr(matrix_calls.asyncio,'open_unix_connection',connect)
    calls._run(record,account,identity,threading.Event())
    assert sent[0]['call']['room_id']=='!qa:hs' and sent[0]['account']==account and sent[0]['identity']==identity
    receipt=calls.get(call_id,'qa-user')
    assert receipt['status']=='completed' and receipt['answered'] is True
    assert receipt['recording_host']=='roomhacker-server-88'
    assert 'received_audio_bytes' not in receipt  # Remote archive was not measured locally.
