"""Stored legacy Telegram keys remain readable without weakening source scope."""
from dataclasses import replace

import pytest

from universal_userio.contracts import InboxMessage
from universal_userio.mcp_dispatch import UserIOToolDispatcher
from universal_userio.mcp_surface import UserIOMcpSurface
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


@pytest.fixture
def stored_history(tmp_path):
    store = SQLiteUserIOStore(tmp_path / 'history.sqlite3')
    account = 'telegram:900000103'
    provider = type('Provider', (), {'operations': {'history'}})()
    service = UserIOService(store, object(), object())
    service.messenger_providers = {account: provider}
    store.register_account(account_id=account, provider='telegram', display_name='Test',
                           can_read=True, can_reply=False, credential_ref='relay:existing')
    message = InboxMessage('telegram', '4242:77', '4242', 'legacy text', 10,
                           peer_id='4242', conversation_kind='direct')
    cid, _ = service.receive(message, route_id='telegram', account_ref=account)
    # Model an existing pre-account-scope row, then replay through normal ingestion.
    with store._connection:
        store._connection.execute('UPDATE messages SET message_id=? WHERE conversation_id=?',
                                  ('4242:77', cid))
    service.receive(message, route_id='telegram', account_ref=account)
    service.receive(replace(message, message_id='4242:78', body='current text', received_at=11),
                    route_id='telegram', account_ref=account)
    yield store, service, account, cid
    store.close()


@pytest.mark.parametrize('legacy_surface', [False, True])
def test_mixed_legacy_scoped_history_retains_ids_and_pagination(stored_history, legacy_surface):
    store, service, account, cid = stored_history
    api = (UserIOMcpSurface if legacy_surface else UserIOToolDispatcher)(store, service)
    principal = replace(store.owner(), read_only=True)
    args = {'account_ref': account, 'conversation_id': cid, 'limit': 1, 'refresh': False}
    newest = api.dispatch('userio.messenger.history', args, principal=principal)
    assert newest['ok'], newest
    earlier = api.dispatch('userio.messenger.history',
                           {**args, 'cursor': newest['next_cursor']}, principal=principal)
    assert earlier['ok'], earlier
    assert newest['messages'][0]['source']['message_id'] == account + '|4242:78'
    source = earlier['messages'][0]['source']
    assert source['message_id'] == '4242:77'
    assert source['provider_message_id'] == '77'
    assert source['peer_id'] == '4242' and source['account_ref'] == account
    assert earlier['messages'][0]['text'] == 'legacy text'
    assert not earlier['fresh'] and earlier['mode'] == 'stored'


@pytest.mark.parametrize('bad_id', ['9999:77', 'telegram:9999|4242:77', '4242:0', '4242:77:88'])
def test_legacy_history_still_rejects_unverified_source_ids(stored_history, bad_id):
    store, service, account, cid = stored_history
    with store._connection:
        store._connection.execute('UPDATE messages SET message_id=? WHERE message_id=?',
                                  (bad_id, '4242:77'))
    result = UserIOToolDispatcher(store, service).dispatch('userio.messenger.history',
        {'account_ref': account, 'conversation_id': cid}, principal=store.owner())
    assert not result['ok'] and 'provider_source_id_unavailable' in str(result)
