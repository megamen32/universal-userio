"""Provider transport failures must surface as typed read errors, never unknown.

Reproduces the production failure: messenger.history(refresh=true) on a
Telegram QR account whose native /reconcile call hits a bridge timeout. The
provider read must translate transport timeouts into a typed ValueError the
dispatch boundary already reports honestly, instead of leaking a TimeoutError
into the catch-all provider_result_unknown_read_action_status.
"""
import io
import json
import socket
import urllib.error

import pytest

from test_messenger import case as messenger_case, dispatch  # noqa: F401  (fixtures/helpers)
from universal_userio.adapters import _download_via_bridge
from universal_userio.channels.core import AdapterNotSupported
from universal_userio.contracts import InboxMessage
from universal_userio.messenger import Messenger
from universal_userio.messenger_telegram_mirror import TelegramMirrorProvider
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore

REF = 'telegram:900000103'
PEER = '540308572'


class TimeoutConnector:
    """Bridge whose /reconcile stalls past the client timeout (prod shape)."""

    def __init__(self):
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        raise TimeoutError('timed out')


class UnreachableConnector:
    """Bridge whose TCP connect/read fails outright."""

    def open(self, request, timeout=None):
        raise urllib.error.URLError(socket.gaierror(-2, 'Name or service not known'))


def mirror_provider(service, store, connector):
    provider = TelegramMirrorProvider(service, REF, store.default_user_id, opener=connector.open)
    service.messenger_providers[REF] = provider
    return provider


def fresh_history_case(tmp_path, monkeypatch, connector):
    """A stored conversation plus a live mirror provider wired to the connector."""
    monkeypatch.setenv('USERIO_API_TOKEN', 'fixture')
    store = SQLiteUserIOStore(tmp_path / 'transport.sqlite3')
    service = UserIOService(store, object(), object())
    store.register_account(account_id=REF, provider='telegram', display_name='Secretary',
                           can_read=True, can_reply=True, credential_ref='telegram-qr:slot-1')
    cid, _ = service.receive(InboxMessage('telegram', f'{PEER}:3148', 'speaker42', 'fixture body',
                                          1788545316.0, peer_id=PEER, conversation_kind='direct',
                                          reconciliation=True), route_id='telegram', account_ref=REF)
    mirror_provider(service, store, connector)
    return store, service, cid


def test_bridge_timeout_in_refresh_history_is_typed_not_unknown(tmp_path, monkeypatch):
    """The exact production failure: /reconcile timeout inside history(refresh=True)."""
    store, service, cid = fresh_history_case(tmp_path, monkeypatch, TimeoutConnector())
    api = Messenger(store, service, store.owner())
    with pytest.raises(ValueError) as excinfo:
        api.history(REF, cid, refresh=True)
    assert 'provider_read_timeout' in str(excinfo.value)


def test_bridge_unreachable_in_refresh_history_is_typed_not_unknown(tmp_path, monkeypatch):
    store, service, cid = fresh_history_case(tmp_path, monkeypatch, UnreachableConnector())
    api = Messenger(store, service, store.owner())
    with pytest.raises(ValueError) as excinfo:
        api.history(REF, cid, refresh=True)
    assert 'provider_read_unavailable' in str(excinfo.value)


def test_dispatch_reports_bridge_timeout_as_provider_read_timeout(tmp_path, monkeypatch):
    """The dispatch boundary maps the typed error into an honest MCP result."""
    store, service, cid = fresh_history_case(tmp_path, monkeypatch, TimeoutConnector())
    result = dispatch((store, service, None, cid, None), 'history',
                      {'account_ref': REF, 'conversation_id': cid, 'refresh': True})
    assert result['ok'] is False
    assert result['error'] == 'provider_read_timeout'
    assert result['error'] != 'provider_result_unknown_read_action_status'


def test_dispatch_reports_unreachable_bridge_as_provider_read_unavailable(tmp_path, monkeypatch):
    store, service, cid = fresh_history_case(tmp_path, monkeypatch, UnreachableConnector())
    result = dispatch((store, service, None, cid, None), 'history',
                      {'account_ref': REF, 'conversation_id': cid, 'refresh': True})
    assert result['ok'] is False
    assert result['error'] == 'provider_read_unavailable'


def test_bridge_http_error_in_refresh_history_is_typed_not_unknown(tmp_path, monkeypatch):
    """Bridge answers 400 {"error":"TIMEOUT"} (prod receipt) -> typed, honest code."""
    class HttpErrorConnector:
        def open(self, request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 400,
                                         '{"error":"TIMEOUT"}', {}, io.BytesIO(b'{"error":"TIMEOUT"}'))

    store, service, cid = fresh_history_case(tmp_path, monkeypatch, HttpErrorConnector())
    api = Messenger(store, service, store.owner())
    with pytest.raises(ValueError, match='provider_read_http_400'):
        api.history(REF, cid, refresh=True)


def test_resolve_username_transport_failure_keeps_its_own_typed_receipt(tmp_path, monkeypatch):
    """Identity reads keep IdentityReadUnavailable; the guard must not override it."""
    store, service, cid = fresh_history_case(tmp_path, monkeypatch, UnreachableConnector())
    provider = service.messenger_providers[REF]
    with pytest.raises(ValueError, match='identity_transport_unavailable'):
        provider.resolve_username('fixture_user')


def test_mirror_message_live_read_timeout_is_typed(tmp_path, monkeypatch):
    """resolve()/message() uses the same /reconcile route and must stay typed too."""
    store, service, cid = fresh_history_case(tmp_path, monkeypatch, TimeoutConnector())
    provider = service.messenger_providers[REF]
    with pytest.raises(ValueError, match='provider_read_timeout'):
        provider.message(peer_id=PEER, provider_message_id='3148', conversation_id=cid)


def test_download_bridge_timeout_stays_typed(tmp_path, monkeypatch):
    """Attachment downloads already normalize transport errors; keep them typed."""
    def runner(*_args, **_kwargs):
        raise TimeoutError('timed out')

    with pytest.raises(AdapterNotSupported):
        _download_via_bridge(
            channel='telegram',
            message={'sender': PEER},
            file_ref='1',
            bridge_url='http://bridge.invalid',
            token_env='MISSING_TEST_TOKEN',
            chat_field='chat',
            chat_id_field='chat_id',
            message_field='message_id',
            runner=runner,
            max_bytes=1024,
        )


def test_download_bridge_generic_oserror_stays_typed(tmp_path, monkeypatch):
    """TimeoutError IS an OSError subclass; a plain OSError must stay typed too."""
    def runner(*_args, **_kwargs):
        raise OSError('connection reset mid-body')

    with pytest.raises(AdapterNotSupported):
        _download_via_bridge(
            channel='telegram',
            message={'sender': PEER},
            file_ref='1',
            bridge_url='http://bridge.invalid',
            token_env='MISSING_TEST_TOKEN',
            chat_field='chat',
            chat_id_field='chat_id',
            message_field='message_id',
            runner=runner,
            max_bytes=1024,
        )
