"""Whole-chat export: authors, voice transcripts, media opt-in, file delivery."""
from pathlib import Path

import pytest

from universal_userio.chat_export import export_conversation, user_exports_dir
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
    user = store.default_user_id
    store.register_account(account_id='telegram:1', provider='telegram', display_name='Telegram',
                           enabled=True, can_read=True, can_reply=True, credential_ref='fixture',
                           user_id=user)
    policy = ConversationPolicy('fixture')
    conversation = 'conv_group'
    rows = [
        InboxMessage('telegram', 'telegram:1|101:1', 'Alice', 'привет из экспорта', 100.0,
                     peer_id='101', conversation_kind='group'),
        InboxMessage('telegram', 'telegram:1|101:2', 'Bob', '', 200.0,
                     peer_id='101', conversation_kind='group', attachments=({
                         'kind': 'voice', 'content_type': 'audio/ogg', 'filename': 'voice.ogg',
                         'transcript': 'это слова из голосового сообщения',
                         'transcription_status': 'completed', 'transcription_model': 'whisper',
                     },)),
        InboxMessage('telegram', 'telegram:1|101:3', 'Alice', '', 300.0,
                     peer_id='101', conversation_kind='group', attachments=({
                         'kind': 'voice', 'content_type': 'audio/ogg', 'filename': 'voice2.ogg',
                         'transcription_status': 'not_requested',
                     },)),
        InboxMessage('telegram', 'telegram:1|101:4', 'Bob', 'вот картинка', 400.0,
                     peer_id='101', conversation_kind='group', attachments=({
                         'kind': 'photo', 'content_type': 'image/jpeg', 'filename': 'photo.jpg',
                         'size': 2048,
                     },)),
    ]
    for row in rows:
        store.ingest(row, conversation_id=conversation, policy=policy, account_ref='telegram:1',
                     user_id=user)
    return store, service, conversation


def test_export_marks_authors_and_transcripts(tmp_path):
    store, service, conversation = fixture(tmp_path)
    result = export_conversation(store, conversation)
    markdown = result['markdown']
    assert 'привет из экспорта' in markdown
    assert '[Голосовое сообщение — расшифровка (whisper, completed)]' in markdown
    assert 'это слова из голосового сообщения' in markdown
    assert '[Голосовое сообщение — расшифровки нет]' in markdown
    assert 'Alice' in markdown and 'Bob' in markdown
    assert 'входящее' in markdown
    assert result['stats']['voice_messages'] == 2
    assert result['stats']['voice_without_transcript'] == 1


def test_media_is_opt_in(tmp_path):
    store, service, conversation = fixture(tmp_path)
    without = export_conversation(store, conversation)
    assert 'photo.jpg' not in without['markdown']
    assert without['stats']['media_messages'] == 1
    assert any('не включены' in warning for warning in without['warnings'])
    with_images = export_conversation(store, conversation, include_images=True)
    assert 'photo.jpg' in with_images['markdown']
    assert '/media/' in with_images['markdown'] and '/raw' in with_images['markdown']


def test_export_writes_a_file_next_to_the_data_root(tmp_path):
    store, service, conversation = fixture(tmp_path)
    result = export_conversation(store, conversation)
    path = Path(result['path'])
    assert path.is_file()
    assert path.parent == user_exports_dir(store, store.default_user_id)
    assert path.read_text(encoding='utf-8').startswith('# Чат:')
    assert result['download_url'] == f"/v1/exports/{result['name']}"


def test_export_respects_the_message_limit(tmp_path):
    store, service, conversation = fixture(tmp_path)
    result = export_conversation(store, conversation, max_messages=2)
    assert result['stats']['messages_exported'] == 2
    assert result['stats']['truncated'] is True
    assert any('экспортировано 2' in warning for warning in result['warnings'])


def test_export_window_selects_messages(tmp_path):
    store, service, conversation = fixture(tmp_path)
    result = export_conversation(store, conversation, since=250.0)
    assert result['stats']['messages_in_window'] == 2
    assert 'привет из экспорта' not in result['markdown']
    assert 'вот картинка' in result['markdown']


def test_tool_returns_path_preview_and_content(tmp_path):
    store, service, conversation = fixture(tmp_path)
    surface = UserIOMcpSurface(store, service)
    result = surface.dispatch('userio.channels.export',
                               {'conversation_id': conversation}, principal=store.owner())
    assert result['ok']
    assert result['path']
    assert result['stats']['messages_exported'] == 4
    assert result['preview'].startswith('# Чат:')
    assert 'привет из экспорта' in result['content']
    manifest = surface.tool_manifest(principal=store.owner())
    assert any(tool['name'] == 'userio.channels.export' for tool in manifest['tools'])


def test_tool_rejects_a_missing_conversation(tmp_path):
    store, service, conversation = fixture(tmp_path)
    surface = UserIOMcpSurface(store, service)
    missing = surface.dispatch('userio.channels.export', {}, principal=store.owner())
    assert not missing['ok']
    unknown = surface.dispatch('userio.channels.export',
                               {'conversation_id': 'conv_missing'}, principal=store.owner())
    assert not unknown['ok']


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


def test_scoped_token_only_exports_its_own_chats(tmp_path):
    store, service, principal = scoped_fixture(tmp_path)
    surface = UserIOMcpSurface(store, service)
    allowed = surface.dispatch('userio.channels.export',
                               {'conversation_id': 'chat1'}, principal=principal)
    assert allowed['ok']
    assert 'Привет' in allowed['preview']
    denied = surface.dispatch('userio.channels.export',
                              {'conversation_id': 'chat2'}, principal=principal)
    assert not denied['ok']


def test_read_only_token_can_export(tmp_path):
    store, service, conversation = fixture(tmp_path)
    created = PersonalTokens(store).create(store.default_user_id, 'reader', scope='read_only')
    principal = store.authenticate_token(created['token'])
    result = UserIOMcpSurface(store, service).dispatch(
        'userio.channels.export', {'conversation_id': conversation}, principal=principal)
    assert result['ok']
    assert Path(result['path']).is_file()


@pytest.mark.parametrize('arguments', [
    {'conversation_id': ''}, {'conversation_id': 'x' * 200},
])
def test_invalid_arguments_are_rejected(tmp_path, arguments):
    store, service, conversation = fixture(tmp_path)
    result = UserIOMcpSurface(store, service).dispatch(
        'userio.channels.export', arguments, principal=store.owner())
    assert not result['ok']


def test_exported_file_is_served_over_authenticated_http(tmp_path):
    import json
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    from universal_userio.http_api import handler

    store, service, conversation = fixture(tmp_path)
    exported = export_conversation(store, conversation)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="owner-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def fetch(path: str, bearer: str = "owner-token"):
        request = Request(base + path, headers={"Authorization": f"Bearer {bearer}"})
        try:
            with urlopen(request) as response:
                return response.status, response.read()
        except HTTPError as error:
            return error.code, error.read()

    try:
        status, body = fetch(exported["download_url"])
        assert status == 200
        assert body.decode("utf-8") == exported["markdown"]
        assert fetch("/v1/exports/" + exported["name"], bearer="")[0] == 401
        assert fetch("/v1/exports/../secret")[0] == 404
        assert fetch("/v1/exports/missing.md")[0] == 404
    finally:
        server.shutdown()
        server.server_close()
