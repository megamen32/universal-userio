"""Formatting is explicitly bound to the approved message intent."""

import pytest

from universal_userio.messenger import Messenger
from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


@pytest.mark.parametrize("action,payload", [
    ("reply", {"text": "**Готово**", "format": "telegram_markdown"}),
    ("edit", {"text": "**Готово**", "expected_text": "Жду", "format": "telegram_markdown"}),
])
def test_messenger_accepts_explicit_telegram_format(action, payload):
    assert Messenger._payload(action, payload) == payload


def test_messenger_rejects_unknown_format():
    with pytest.raises(ValueError):
        Messenger._payload("reply", {"text": "example", "format": "unknown"})


def test_approved_telegram_edit_binds_format_and_projects_wire_text(tmp_path):
    class Outbox:
        def supports_edit(self, account_ref, peer_id): return True
        def edit_message(self, **kwargs):
            assert kwargs['format'] == 'telegram_markdown'
            return {'ok': True, 'account_id': kwargs['account_ref'], 'peer_id': kwargs['chat_id'],
                    'message_id': '77', 'input_text': kwargs['text'], 'text': 'Готово',
                    'formatting_verified': True, 'edit_date': 20, 'out': True}

    store = SQLiteUserIOStore(tmp_path / 'userio.sqlite3')
    service = UserIOService(store, object(), object(), telegram_outbox=Outbox())
    account, peer = 'telegram:900000103', '-10042'
    store.register_account(account_id=account, provider='telegram', display_name='Owner',
                           can_read=True, can_reply=True, credential_ref='telegram-qr:account-1')
    conversation, _ = service.receive(InboxMessage('telegram', f'{peer}:77', '900000103', 'before', 10,
        direction='outgoing', peer_id=peer, conversation_kind='direct'), route_id='telegram', account_ref=account)
    messenger = Messenger(store, service, store.owner())
    proposal = messenger.propose(account, conversation, f'{account}|{peer}:77', 'edit',
        {'text': '**Готово**', 'expected_text': 'before', 'format': 'telegram_markdown'}, 'formatted-edit-test-0001')
    action = proposal
    assert action['envelope']['payload']['format'] == 'telegram_markdown'
    result = messenger.approve(action['id'], action['hash'], True)
    assert result['status'] == 'accepted'
    assert store.message(source='telegram', message_id=f'{account}|{peer}:77')['body'] == 'Готово'
    store.close()


def test_approved_telegram_reply_binds_format_and_projects_wire_text(tmp_path):
    class Outbox:
        def reply_message(self, **kwargs):
            assert kwargs['format'] == 'telegram_markdown'
            return {'ok': True, 'account_id': kwargs['account_ref'], 'peer_id': kwargs['chat_id'],
                    'message_id': '78', 'reply_to_id': kwargs['message_id'], 'thread_id': kwargs['thread_id'],
                    'input_text': kwargs['text'], 'text': 'Готово', 'formatting_verified': True,
                    'date': 123, 'out': True}

    store = SQLiteUserIOStore(tmp_path / 'userio.sqlite3')
    service = UserIOService(store, object(), object(), telegram_outbox=Outbox())
    account, peer = 'telegram:900000101', '900000300'
    store.register_account(account_id=account, provider='telegram', display_name='Owner',
                           can_read=True, can_reply=True, credential_ref='telegram-qr:account-1')
    conversation, _ = service.receive(InboxMessage('telegram', f'{account}|{peer}:77', peer, 'source', 10,
        direction='incoming', peer_id=peer, conversation_kind='direct'), route_id='telegram', account_ref=account)
    messenger = Messenger(store, service, store.owner())
    action = messenger.propose(account, conversation, f'{account}|{peer}:77', 'reply',
        {'text': '**Готово**', 'format': 'telegram_markdown'}, 'formatted-reply-test-0001')
    result = messenger.approve(action['id'], action['hash'], True)
    assert result['status'] == 'accepted'
    assert store.message(source='telegram', message_id=f'{account}|{peer}:78')['body'] == 'Готово'
    assert messenger.approve(action['id'], action['hash'], True)['receipt'] == result['receipt']
    store.close()
