"""Receipt-first, exact provider-history fallback; never replay an uncertain send."""
import json
import io
import time
import urllib.error

import pytest

from test_telegram_delivery_reconciliation import (
    _JournalOutbox, _insert_provider_message, _make_due, _telegram_service,
)
from universal_userio.delivery_reconciliation import TelegramDeliveryReconciliationWorker
from universal_userio.adapters import TelegramQrHttpOutbox


class HistoryOutbox(_JournalOutbox):
    def __init__(self, state='absent'):
        super().__init__({'state': state})
        self.page = {'account_id': 'telegram:540308572', 'peer_id': '-1003724214344',
                     'message_ids': [501], 'oldest_id': 501, 'has_more': False,
                     'provider_read_acknowledged': False, 'fetched': 1, 'mirrored': 1}
        self.requests = []

    def reconcile_history(self, **kwargs):
        self.requests.append(kwargs)
        return dict(self.page)


def scenario(tmp_path, *, state='absent', direction='outgoing', body=None, age=1):
    outbox = HistoryOutbox(state)
    service, draft_id = _telegram_service(tmp_path, outbox)
    with pytest.raises(RuntimeError):
        service.approve(draft_id)
    draft = service._store.draft(draft_id)
    claim = service._store.draft_delivery_state(draft_id)
    _insert_provider_message(service, draft_id, '501', claim['sending_at'] + age,
                             direction=direction, body=draft.body if body is None else body)
    _make_due(service, draft_id)
    return service, draft_id, outbox


@pytest.mark.parametrize('journal_state', ['absent', 'pending', 'unknown'])
def test_background_closes_exact_fresh_outgoing_history_match_without_resending(tmp_path, journal_state):
    service, draft_id, outbox = scenario(tmp_path, state=journal_state)
    assert TelegramDeliveryReconciliationWorker(service).run_once() == 1
    state = service._store.draft_delivery_state(draft_id)
    assert state['status'] == 'approved'
    assert state['receipt'] == 'telegram-history:telegram:540308572|-1003724214344:501'
    assert state['next_check_at'] is None
    assert state['evidence']['history']['matched_message_id'].endswith(':501')
    assert len(outbox.sent) == 1
    assert outbox.requests


@pytest.mark.parametrize('change', ['incoming', 'wrong_text', 'old', 'future', 'wrong_account',
                                    'wrong_peer', 'stale_local_only', 'attachment', 'duplicate', 'reply_context'])
def test_history_requires_exact_scoped_outgoing_payload(tmp_path, change):
    service, draft_id, outbox = scenario(
        tmp_path, direction='incoming' if change == 'incoming' else 'outgoing',
        body='другой ответ' if change == 'wrong_text' else None,
        age=-7200 if change == 'old' else 172800 if change == 'future' else 1)
    if change == 'wrong_account':
        outbox.page['account_id'] = 'telegram:someone_else'
    if change == 'wrong_peer':
        outbox.page['peer_id'] = '123456'
    if change == 'stale_local_only':
        outbox.page['message_ids'] = []
    if change == 'attachment':
        service._store._connection.execute(
            'INSERT INTO message_attachments (user_id,source,message_id,idx,kind,content_type,filename) VALUES (?,?,?,?,?,?,?)',
            (service._store.default_user_id, 'telegram', 'telegram:540308572|-1003724214344:501',
             0, 'document', 'application/pdf', 'unexpected.pdf'))
        service._store._connection.commit()
    if change == 'duplicate':
        draft = service._store.draft(draft_id)
        _insert_provider_message(service, draft_id, '502', time.time(), direction='outgoing', body=draft.body)
        outbox.page['message_ids'].append(502)
    if change == 'reply_context':
        service._store._connection.execute(
            "UPDATE messages SET provider_reply_to='different-message' WHERE message_id=?",
            ('telegram:540308572|-1003724214344:501',))
        service._store._connection.commit()
    assert TelegramDeliveryReconciliationWorker(service).run_once() == 1
    state = service._store.draft_delivery_state(draft_id)
    assert state['status'] in ('sending', 'delivery_unknown')
    assert not state['receipt']
    assert state['next_check_at'] is not None
    assert len(outbox.sent) == 1


def test_history_no_match_keeps_claim_and_prevents_approve(tmp_path):
    service, draft_id, outbox = scenario(tmp_path, body='не совпало')
    TelegramDeliveryReconciliationWorker(service).run_once()
    with pytest.raises(Exception):
        service.approve(draft_id)
    assert len(outbox.sent) == 1
    assert service._store.draft_delivery_state(draft_id)['evidence']['history']['reason'] == 'no_exact_match'


def test_history_paginates_with_saved_cursor_instead_of_repeating_latest_page(tmp_path):
    service, draft_id, outbox = scenario(tmp_path)
    outbox.page.update(message_ids=[900], oldest_id=900, has_more=True)
    _insert_provider_message(service, draft_id, '900', time.time() + 60)
    TelegramDeliveryReconciliationWorker(service).run_once()
    _make_due(service, draft_id)
    outbox.page.update(message_ids=[501], oldest_id=501, has_more=False)
    TelegramDeliveryReconciliationWorker(service).run_once()
    assert outbox.requests[-1]['before_id'] == 900
    assert service._store.draft(draft_id).status == 'approved'
    assert len(outbox.sent) == 1


def test_history_cannot_override_attempt_generation_or_expired_worker(tmp_path):
    service, draft_id, outbox = scenario(tmp_path)
    original = outbox.reconcile_history
    def stale(**kwargs):
        result = original(**kwargs)
        service._store._connection.execute('UPDATE drafts SET delivery_attempt_generation=delivery_attempt_generation+1 WHERE id=?', (draft_id,))
        service._store._connection.commit()
        return result
    outbox.reconcile_history = stale
    TelegramDeliveryReconciliationWorker(service).run_once()
    assert service._store.draft(draft_id).status == 'sending'
    assert len(outbox.sent) == 1


def test_provider_second_precision_does_not_hide_a_same_second_send(tmp_path):
    service, draft_id, outbox = scenario(tmp_path)
    claim = service._store.draft_delivery_state(draft_id)
    service._store._connection.execute('UPDATE messages SET received_at=? WHERE message_id=?',
        (int(claim['sending_at']), 'telegram:540308572|-1003724214344:501'))
    service._store._connection.commit()
    TelegramDeliveryReconciliationWorker(service).run_once()
    assert service._store.draft(draft_id).status == 'approved'
    assert len(outbox.sent) == 1


def test_history_adapter_preserves_pagination_and_checks_peer_identity():
    requests = []
    def runner(request, **kwargs):
        requests.append((json.loads(request.data), kwargs))
        return io.BytesIO(json.dumps({'account_id': 'telegram:1', 'peer_id': '999'}).encode())
    outbox = TelegramQrHttpOutbox('http://unused', 'protected', runner=runner)
    with pytest.raises(RuntimeError, match='identity mismatch'):
        outbox.reconcile_history(account_ref='telegram:1', peer_id='2', before_id=77)
    assert requests[0][0]['before_id'] == 77
    assert requests[0][1]['timeout'] == 20


def test_history_adapter_honors_429_without_leaking_response_text():
    def runner(request, **kwargs):
        raise urllib.error.HTTPError(request.full_url, 429, 'quota',
            {'Retry-After': '123'}, io.BytesIO(b'private provider response'))
    outbox = TelegramQrHttpOutbox('http://unused', 'protected', runner=runner)
    with pytest.raises(Exception) as error:
        outbox.reconcile_history(account_ref='telegram:1', peer_id='2')
    assert error.value.retry_after_seconds == 123
    assert 'private' not in str(error.value)
