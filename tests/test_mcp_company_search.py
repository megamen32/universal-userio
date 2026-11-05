from universal_userio.contracts import InboxMessage
from universal_userio.mcp_surface import UserIOMcpSurface
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore

class Generator:
    def propose(self, *_args, **_kwargs): return ''
class Outbox:
    def send(self, *_args, **_kwargs): return 'noop'

def test_company_search_fetch_uses_existing_history_without_marking_seen(tmp_path):
    store = SQLiteUserIOStore(tmp_path / 'test.sqlite3')
    service = UserIOService(store, Generator(), Outbox())
    cid, _ = service.receive(InboxMessage('telegram', 'company-msg-1', 'Alice', 'Уникальное сообщение для поиска', 1.0, conversation_kind='direct'), route_id='telegram')
    surface = UserIOMcpSurface(store, service)
    manifest = {x['name']: x for x in surface.tool_manifest()['tools']}
    assert manifest['search']['annotations']['readOnlyHint'] is True
    assert manifest['fetch']['annotations']['readOnlyHint'] is True
    search = surface.dispatch('search', {'query': 'Уникальное'})
    assert len(search['results']) == 1
    result = search['results'][0]
    fetched = surface.dispatch('fetch', {'id': result['id']})
    assert fetched['id'] == result['id']
    assert 'Уникальное сообщение для поиска' in fetched['text']
    assert fetched['url'] == result['url']
    assert store.message('company-msg-1', source='telegram')['seen_at'] is None
    store.set_workspace_chat_rule(conversation_id=cid, action='ignore', user_id=store.default_user_id)
    assert surface.dispatch('search', {'query': 'Уникальное'})['results'] == []
    assert not surface.dispatch('fetch', {'id': result['id']}).get('text')

def test_fetch_respects_read_only_and_user_isolation(tmp_path):
    from universal_userio.personal_tokens import PersonalTokens
    store = SQLiteUserIOStore(tmp_path / 'isolated.sqlite3')
    service = UserIOService(store, Generator(), Outbox())
    service.receive(InboxMessage('telegram', 'secret-1', 'Alice', 'distinctive cobalt message', 3.0, conversation_kind='direct'), route_id='telegram')
    surface = UserIOMcpSurface(store, service)
    token = PersonalTokens(store).create(store.default_user_id, 'reader', scope='read_only')
    principal = store.authenticate_token(token['token'])
    results = surface.dispatch('search', {'query':'cobalt'}, principal=principal)['results']
    assert len(results) == 1
    assert 'distinctive cobalt message' in surface.dispatch('fetch', {'id':results[0]['id']}, principal=principal)['text']
    assert not surface.dispatch('fetch', {'id':'invalid'}, principal=principal).get('text')

def test_scoped_token_cannot_fetch_other_chat(tmp_path):
    from universal_userio.contracts import ConversationPolicy
    from universal_userio.personal_tokens import PersonalTokens
    store = SQLiteUserIOStore(tmp_path / 'scoped.sqlite3')
    uid = store.default_user_id
    store.register_account(account_id='telegram:1', provider='telegram', display_name='Telegram', enabled=True, can_read=True, can_reply=True, credential_ref='fixture', user_id=uid)
    for cid, peer in [('conv_chat1', '101'), ('conv_chat2', '102')]:
        store.ingest(InboxMessage('telegram', f'telegram:1|{peer}:1', f'Alice-{peer}', 'blueberry scoped message', 100, peer_id=peer, conversation_kind='direct'), conversation_id=cid, policy=ConversationPolicy('fixture'), account_ref='telegram:1', user_id=uid)
    token = PersonalTokens(store).create(uid, 'scoped', scope='scoped_messenger', grants=[{'account_ref':'telegram:1','access':'read_only','chats':[{'conversation_id':'conv_chat1','access':'read_only'}]}])
    surface = UserIOMcpSurface(store, UserIOService(store, Generator(), Outbox()))
    owner_results = surface.dispatch('search', {'query':'blueberry'})['results']
    principal = store.authenticate_token(token['token'])
    scoped = surface.dispatch('search', {'query':'blueberry'}, principal=principal)['results']
    assert len(owner_results) == 2 and len(scoped) == 1
    denied = next(r for r in owner_results if r['id'] != scoped[0]['id'])
    assert not surface.dispatch('fetch', {'id':denied['id']}, principal=principal).get('text')
    assert 'blueberry' in surface.dispatch('fetch', {'id':scoped[0]['id']}, principal=principal)['text']

def test_json_rpc_returns_standard_structured_content(tmp_path):
    from universal_userio.mcp_transport import json_rpc_response
    store = SQLiteUserIOStore(tmp_path / 'rpc.sqlite3')
    service = UserIOService(store, Generator(), Outbox())
    service.receive(InboxMessage('telegram', 'rpc-1', 'Alice', 'amber real message', 1.0, conversation_kind='direct'), route_id='telegram')
    surface = UserIOMcpSurface(store, service)
    def invoke(name, arguments):
        return json_rpc_response(surface, {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':name,'arguments':arguments}}, principal=store.owner())['result']
    result = invoke('search', {'query':'amber'})
    assert result['isError'] is False
    row = result['structuredContent']['results'][0]
    fetched = invoke('fetch', {'id':row['id']})
    assert fetched['isError'] is False
    assert fetched['structuredContent']['text'] == 'amber real message'
    import json
    assert json.loads(fetched['content'][0]['text']) == fetched['structuredContent']

def test_disabled_read_capability_hides_and_blocks_adapter(tmp_path):
    store = SQLiteUserIOStore(tmp_path / 'disabled.sqlite3')
    service = UserIOService(store, Generator(), Outbox())
    service.receive(InboxMessage('telegram', 'disabled-1', 'Alice', 'zircon example', 1.0, conversation_kind='direct'), route_id='telegram')
    surface = UserIOMcpSurface(store, service)
    found = surface.dispatch('search', {'query':'zircon'})['results'][0]
    store.set_user_capability('read', False)
    assert not {'search', 'fetch'} & {x['name'] for x in surface.tool_manifest()['tools']}
    assert not surface.dispatch('search', {'query':'zircon'}).get('results')
    assert not surface.dispatch('fetch', {'id':found['id']}).get('text')
