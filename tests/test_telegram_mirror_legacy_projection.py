"""Native-confirmed mirror reads preserve legacy row identities after hydration."""
import io
import json

import pytest

from universal_userio.contracts import InboxMessage
from universal_userio.messenger import Messenger
from universal_userio.messenger_telegram_mirror import TelegramMirrorProvider
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore

REF, PEER, NATIVE = 'telegram:900000103', '4242', '77'


@pytest.fixture
def legacy_mirror(tmp_path, monkeypatch):
    monkeypatch.setenv('USERIO_API_TOKEN', 'fixture')
    store = SQLiteUserIOStore(tmp_path / 'mirror.sqlite3')
    service = UserIOService(store, object(), object())
    store.register_account(account_id=REF, provider='telegram', display_name='Test',
                           can_read=True, can_reply=True, credential_ref='telegram-qr:test')
    source = InboxMessage('telegram', PEER + ':' + NATIVE, PEER, 'retained body', 100,
                          peer_id=PEER, conversation_kind='direct', reconciliation=True)
    cid, _ = service.receive(source, route_id='telegram', account_ref=REF)
    with store._connection:
        store._connection.execute('UPDATE messages SET message_id=? WHERE conversation_id=?',
                                  (PEER + ':' + NATIVE, cid))
    service.receive(source, route_id='telegram', account_ref=REF)
    requests = []

    def opener(request, timeout=None):
        requests.append(json.loads(request.data))
        return io.BytesIO(json.dumps({'account_id': REF, 'peer_id': PEER,
            'message_ids': [77], 'fetched': 1, 'oldest_id': 77, 'has_more': False,
            'provider_read_acknowledged': False, 'scan': True}).encode())

    provider = TelegramMirrorProvider(service, REF, store.default_user_id, opener=opener)
    service.messenger_providers[REF] = provider
    yield store, service, provider, cid, requests
    store.close()


@pytest.mark.parametrize('operation', ['history', 'resolve'])
def test_fresh_native_confirmed_read_keeps_legacy_source(legacy_mirror, operation):
    store, service, provider, cid, requests = legacy_mirror
    api = Messenger(store, service, store.owner())
    result = (api.history(REF, cid, refresh=True) if operation == 'history'
              else api.resolve(REF, PEER, NATIVE))
    assert result['fresh'] is True and result['provider_read_acknowledged'] is False
    value = result['messages'][0] if operation == 'history' else result['message']
    assert value['source']['message_id'] == PEER + ':' + NATIVE
    assert value['source']['provider_message_id'] == NATIVE
    assert value['source']['conversation_id'] == cid
    assert value['text'] == 'retained body'
    assert len(requests) == 1 and requests[0]['scan'] is True
    assert store._connection.execute('SELECT count(*) FROM messages').fetchone()[0] == 1


@pytest.mark.parametrize('operation', ['history', 'message'])
def test_legacy_lookup_rejects_row_in_another_conversation(legacy_mirror, operation):
    store, _, provider, cid, _ = legacy_mirror
    with store._connection:
        store._connection.execute('UPDATE messages SET conversation_id=?', ('different-conversation',))
    kwargs = {'peer_id': PEER, 'conversation_id': cid}
    if operation == 'message':
        kwargs['provider_message_id'] = NATIVE
    with pytest.raises(ValueError, match='fresh Telegram projection missing'):
        getattr(provider, operation)(**kwargs)
