"""Native username failure reasons survive connector, transport and domain boundaries."""
import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from test_messenger import case
from universal_userio.contact_check import has_contacted
from universal_userio.messenger import Messenger
from universal_userio.messenger_telegram_mirror import TelegramMirrorProvider


@pytest.mark.parametrize('code,seconds,reason', [('FLOOD_WAIT', 900, 'identity_rate_limited'),
    ('USERNAME_NOT_OCCUPIED', None, 'identity_source_unavailable'),
    ('USERNAME_INVALID', None, 'identity_source_unavailable'),
    ('TIMEOUT', None, 'identity_transport_unavailable')])
def test_native_typed_error_survives_legacy_http_bridge(case, monkeypatch, code, seconds, reason):
    monkeypatch.setenv('USERIO_API_TOKEN', 'fixture')
    ref = case[2].account_ref
    detail = {'schema': 'userio.identity-read-error.v1', 'code': code,
              'retry_after_seconds': seconds, 'observed_at': 1000}
    body = json.dumps({'error': json.dumps(detail), 'code': 'resolve_failed'}).encode()
    def read(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 400, 'Bad Request', {}, io.BytesIO(body))
    provider = TelegramMirrorProvider(case[1], ref, case[0].default_user_id, opener=read)
    case[1].messenger_providers[ref] = provider
    result = has_contacted(Messenger(case[0], case[1], case[0].owner()), ref,
                           telegram_username='fixture_user', refresh=True)
    assert result['status'] == 'UNKNOWN' and result['reason'] == reason
    assert result['availability']['native_code'] == code
    assert result['availability']['retry_at'] == (1900 if seconds else None)
    assert not result['coverage']['exhausted'] and not result['scope']['peer_id']
    assert 'fixture_user' not in json.dumps(result)


def test_http_timeout_is_not_source_unoccupied(case, monkeypatch):
    monkeypatch.setenv('USERIO_API_TOKEN', 'fixture')
    ref = case[2].account_ref
    def read(request, timeout):
        raise TimeoutError('private URL/query')
    case[1].messenger_providers[ref] = TelegramMirrorProvider(case[1], ref,
        case[0].default_user_id, opener=read)
    result = has_contacted(Messenger(case[0], case[1], case[0].owner()), ref,
                           telegram_username='fixture_user', refresh=True)
    assert result['reason'] == 'identity_transport_unavailable'
    assert result['availability']['retryable'] is True
    assert result['availability']['retry_at'] is None
    assert 'private' not in json.dumps(result)


def test_legacy_notfound_without_native_proof_stays_unknown(case, monkeypatch):
    monkeypatch.setenv('USERIO_API_TOKEN', 'fixture')
    ref = case[2].account_ref
    def read(request, timeout):
        raise urllib.error.HTTPError(request.full_url,404,'not found',{},
            io.BytesIO(b'{"code":"contact_not_found","error":"private username"}'))
    case[1].messenger_providers[ref]=TelegramMirrorProvider(case[1],ref,case[0].default_user_id,opener=read)
    result=has_contacted(Messenger(case[0],case[1],case[0].owner()),ref,
                         telegram_username='fixture_user',refresh=True)
    assert result['reason']=='identity_provider_unavailable'
    assert result['availability']['native_code']=='UNKNOWN_NATIVE_ERROR'
    assert 'private' not in json.dumps(result)
