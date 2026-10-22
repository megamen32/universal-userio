"""The agent-facing search tool: free text, exact conversation id, exact message id.

One core, three ways in. The text paths are the web search; the identifier paths
are what an agent cannot get from a text index.
"""
import pytest

from universal_userio.contracts import ConversationPolicy, InboxMessage
from universal_userio.mcp_surface import UserIOMcpSurface
from universal_userio.personal_tokens import PersonalTokens
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def propose(self, *_args, **_kwargs):
        return ""


class Outbox:
    def send(self, *_args, **_kwargs):
        return "noop"


def fixture(tmp_path):
    store = SQLiteUserIOStore(tmp_path / 'userio.sqlite3')
    service = UserIOService(store, Generator(), Outbox())
    first, _ = service.receive(
        InboxMessage('telegram', 'm-needle', 'Alice', 'архипелаг в телеграме', 1.0),
        route_id='telegram')
    second, _ = service.receive(
        InboxMessage('telegram', 'm-other', 'Bob', 'совсем другой текст', 2.0),
        route_id='telegram')
    return store, service, first, second


def search(store, service, principal, arguments):
    return UserIOMcpSurface(store, service).dispatch(
        'userio.channels.search', arguments, principal=principal)


def test_search_by_message_text_returns_the_chat(tmp_path):
    store, service, first, second = fixture(tmp_path)
    result = search(store, service, store.owner(), {'query': 'архипелаг'})
    assert result['ok']
    assert [row['id'] for row in result['results']] == [first]


def test_search_by_contact_name_returns_the_chat(tmp_path):
    store, service, first, second = fixture(tmp_path)
    result = search(store, service, store.owner(), {'query': 'Bob'})
    assert result['ok']
    assert [row['id'] for row in result['results']] == [second]


def test_search_by_conversation_id(tmp_path):
    store, service, first, second = fixture(tmp_path)
    result = search(store, service, store.owner(), {'conversation_id': first})
    assert result['ok']
    assert result['mode_used'] == 'exact_id'
    assert len(result['results']) == 1
    assert result['results'][0]['id'] == first
    assert result['results'][0]['match_field'] == 'id'


def test_search_by_message_id(tmp_path):
    store, service, first, second = fixture(tmp_path)
    result = search(store, service, store.owner(),
                    {'message_id': 'm-needle', 'scope': 'message'})
    assert result['ok']
    assert result['mode_used'] == 'exact_id'
    assert len(result['results']) == 1
    assert result['results'][0]['message_id'] == 'm-needle'
    assert result['results'][0]['conversation_id'] == first
    assert first in result['results'][0]['deep_link']


def test_unknown_identifiers_return_no_rows(tmp_path):
    store, service, first, second = fixture(tmp_path)
    missing = search(store, service, store.owner(), {'conversation_id': 'conv_missing'})
    assert missing['ok'] and missing['results'] == []
    absent = search(store, service, store.owner(),
                    {'message_id': 'm-missing', 'scope': 'message'})
    assert absent['ok'] and absent['results'] == []


def test_search_requires_a_target(tmp_path):
    store, service, first, second = fixture(tmp_path)
    result = search(store, service, store.owner(), {})
    assert not result['ok']
    assert 'search requires' in result['error']


def test_read_only_token_can_search(tmp_path):
    store, service, first, second = fixture(tmp_path)
    created = PersonalTokens(store).create(store.default_user_id, 'reader', scope='read_only')
    principal = store.authenticate_token(created['token'])
    result = search(store, service, principal, {'query': 'архипелаг'})
    assert result['ok']
    assert [row['id'] for row in result['results']] == [first]
    manifest = UserIOMcpSurface(store, service).tool_manifest(principal=principal)
    assert any(tool['name'] == 'userio.channels.search' for tool in manifest['tools'])


def scoped_fixture(tmp_path):
    store = SQLiteUserIOStore(tmp_path / 'state.sqlite3')
    user = store.default_user_id
    store.register_account(account_id='telegram:1', provider='telegram', display_name='Telegram',
                           enabled=True, can_read=True, can_reply=True, credential_ref='fixture',
                           user_id=user)
    for cid, peer in [('chat1', '101'), ('chat2', '102')]:
        store.ingest(
            InboxMessage('telegram', 'telegram:1|' + peer + ':1', 'Катя-' + peer, 'Привет', 100,
                         peer_id=peer, conversation_kind='direct'),
            conversation_id=cid, policy=ConversationPolicy('fixture'), account_ref='telegram:1',
            user_id=user)
    created = PersonalTokens(store).create(user, 'Fixture', scope='scoped_messenger', grants=[
        {'account_ref': 'telegram:1', 'access': 'read_only',
         'chats': [{'conversation_id': 'chat1', 'access': 'read_only'}]}])
    principal = store.authenticate_token(created['token'])
    service = UserIOService(store, Generator(), Outbox())
    return store, service, principal


def test_scoped_token_only_sees_its_own_chats(tmp_path):
    store, service, principal = scoped_fixture(tmp_path)
    allowed = search(store, service, principal, {'conversation_id': 'chat1'})
    assert allowed['ok'] and len(allowed['results']) == 1
    denied = search(store, service, principal, {'conversation_id': 'chat2'})
    assert denied['ok'] and denied['results'] == []
    text = search(store, service, principal, {'query': 'Привет'})
    assert text['ok']
    assert [row['id'] for row in text['results']] == ['chat1']


@pytest.mark.parametrize('arguments', [
    {'scope': 'chat'}, {'mode': 'magic'}, {'match_type': 'vibes'},
    {'conversation_ids': 'not-a-list'}, {'conversation_id': 'x' * 129},
    {'message_id': 'x' * 257}, {'limit': 0}, {'limit': 10 ** 9},
])
def test_invalid_arguments_are_rejected(tmp_path, arguments):
    store, service, first, second = fixture(tmp_path)
    result = search(store, service, store.owner(), arguments)
    assert not result['ok']
