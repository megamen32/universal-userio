"""Live-confirmed resolve for the Telegram mirror provider.

The mirror owns the native session. A stored row is only the projected content
of a native record the provider still has: a saved row alone must never be
published as a fresh source.
"""
import io
import json
import urllib.request

import pytest

from universal_userio.contracts import InboxMessage
from universal_userio.messenger import Messenger
from universal_userio.messenger_telegram_mirror import TelegramMirrorProvider
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore

REF = 'telegram:900000103'
PEER = '540308572'
OTHER_PEER = '540308573'
NATIVE = '3148'


def page(**overrides):
    """One native page as the connector's /reconcile route reports it."""
    receipt = {'account_id': REF, 'peer_id': PEER, 'message_ids': [int(NATIVE)],
               'fetched': 1, 'oldest_id': int(NATIVE), 'has_more': True,
               'provider_read_acknowledged': False, 'scan': True}
    receipt.update(overrides)
    return receipt


class FakeConnector:
    """The connector's HTTP boundary, without a Telegram session."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        return io.BytesIO(json.dumps(self.pages.pop(0)).encode())


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setenv('USERIO_API_TOKEN', 'fixture')
    store = SQLiteUserIOStore(tmp_path / 'store.db')
    service = UserIOService(store, object(), object())
    store.register_account(account_id=REF, provider='telegram', display_name='Secretary',
                           can_read=True, can_reply=True, credential_ref='telegram-qr:slot-1')
    cid, _ = service.receive(InboxMessage('telegram', f'{PEER}:{NATIVE}', 'speaker42', 'fixture body',
                                           1788545316.0, peer_id=PEER, conversation_kind='direct',
                                           reconciliation=True,
                                           attachments=({'kind': 'telegram_routing',
                                                         'provider_ref': json.dumps({
                                                             'author_id': '42',
                                                             'author_name': 'Real speaker'})},)),
                               route_id='telegram', account_ref=REF)
    other_cid, _ = service.receive(InboxMessage('telegram', f'{OTHER_PEER}:9001', 'speaker43',
                                                'other body', 1788545400.0, peer_id=OTHER_PEER,
                                                conversation_kind='direct', reconciliation=True),
                                  route_id='telegram', account_ref=REF)
    yield store, service, cid, other_cid
    store.close()


def provider_for(service, store, connector):
    return TelegramMirrorProvider(service, REF, store.default_user_id, opener=connector.open)


def test_live_native_id_returns_the_canonical_projection(case):
    store, service, cid, _ = case
    connector = FakeConnector([page()])
    value = provider_for(service, store, connector).message(
        peer_id=PEER, provider_message_id=NATIVE, conversation_id=cid)
    source = value['source']
    assert source['account_ref'] == REF and source['peer_id'] == PEER
    assert source['conversation_id'] == cid
    assert source['provider_message_id'] == NATIVE
    assert source['message_id'] == f'{REF}|{PEER}:{NATIVE}'
    assert source['direction'] == 'incoming'
    assert value['author']['id'] == '42' and value['author']['name'] == 'Real speaker'
    assert value['text'] == 'fixture body'
    assert value['at'] > 0 and value['media'] == []
    request = connector.requests[0]
    assert request.full_url.endswith('/reconcile')
    assert json.loads(request.data) == {'account_id': REF, 'peer_id': PEER, 'limit': 1,
                                         'before_id': int(NATIVE) + 1, 'scan': True}
    assert request.headers['Authorization'] == 'Bearer fixture'


def test_absent_native_id_is_never_replaced_by_the_stored_row(case):
    store, service, cid, _ = case
    connector = FakeConnector([page(message_ids=[3147], fetched=1, oldest_id=3147)])
    with pytest.raises(ValueError, match='provider_source_not_live'):
        provider_for(service, store, connector).message(
            peer_id=PEER, provider_message_id=NATIVE, conversation_id=cid)


def test_empty_native_page_means_no_live_source(case):
    store, service, cid, _ = case
    connector = FakeConnector([page(message_ids=[], fetched=0, oldest_id=0, has_more=False)])
    with pytest.raises(ValueError, match='provider_source_not_live'):
        provider_for(service, store, connector).message(
            peer_id=PEER, provider_message_id=NATIVE, conversation_id=cid)


def test_foreign_page_scope_is_refused(case):
    store, service, cid, _ = case
    connector = FakeConnector([page(account_id='telegram:900000999')])
    with pytest.raises(ValueError, match='scope conflict'):
        provider_for(service, store, connector).message(
            peer_id=PEER, provider_message_id=NATIVE, conversation_id=cid)


def test_foreign_peer_page_scope_is_refused(case):
    store, service, cid, _ = case
    connector = FakeConnector([page(peer_id=OTHER_PEER)])
    with pytest.raises(ValueError, match='scope conflict'):
        provider_for(service, store, connector).message(
            peer_id=PEER, provider_message_id=NATIVE, conversation_id=cid)


def test_conversation_of_another_peer_is_refused(case):
    store, service, cid, other_cid = case
    connector = FakeConnector([page()])
    with pytest.raises(ValueError, match='scope conflict'):
        provider_for(service, store, connector).message(
            peer_id=PEER, provider_message_id=NATIVE, conversation_id=other_cid)


def test_read_acknowledgement_is_never_claimed(case):
    store, service, cid, _ = case
    connector = FakeConnector([page(provider_read_acknowledged=True)])
    with pytest.raises(ValueError, match='invalid fresh Telegram receipt'):
        provider_for(service, store, connector).message(
            peer_id=PEER, provider_message_id=NATIVE, conversation_id=cid)


def test_resolve_reads_the_mirror_instead_of_reporting_provider_resolve_unavailable(case):
    store, service, cid, _ = case
    connector = FakeConnector([page()])
    service.messenger_providers[REF] = provider_for(service, store, connector)
    api = Messenger(store, service, store.owner())
    result = api.resolve(REF, PEER, NATIVE)
    assert result['fresh'] is True and result['provider_read_acknowledged'] is False
    assert result['conversation_id'] == cid
    assert result['message']['source']['provider_message_id'] == NATIVE
    assert result['message']['source']['message_id'] == f'{REF}|{PEER}:{NATIVE}'
    assert result['message']['author']['id'] == '42'


def test_resolve_uses_the_qr_credential_provider_without_a_registered_provider(case, monkeypatch):
    """The telegram-qr credential selects the mirror provider by itself."""
    store, service, cid, _ = case
    connector = FakeConnector([page()])
    monkeypatch.setattr(urllib.request, 'build_opener',
                        lambda *args, **kwargs: type('Opener', (), {'open': staticmethod(connector.open)})())
    api = Messenger(store, service, store.owner())
    result = api.resolve(REF, PEER, NATIVE)
    assert result['fresh'] is True and result['conversation_id'] == cid
    assert result['message']['source']['provider_message_id'] == NATIVE


def test_resolve_of_a_stored_but_dead_id_stays_refused(case):
    store, service, cid, _ = case
    connector = FakeConnector([page(message_ids=[3147], fetched=1, oldest_id=3147)])
    service.messenger_providers[REF] = provider_for(service, store, connector)
    api = Messenger(store, service, store.owner())
    with pytest.raises(ValueError, match='provider_source_not_live'):
        api.resolve(REF, PEER, NATIVE)
