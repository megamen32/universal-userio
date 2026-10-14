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
                       'USERIO_MESSENGER_RELAY_ACCOUNT': 'telegram:100001',
                       'USERIO_TELEGRAM_RELAY_PEER': '100002'}.items():
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
