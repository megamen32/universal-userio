"""First contact: resolve an identifier, open the chat, deliver the first message."""
from dataclasses import replace

import pytest

from universal_userio.contact_keys import peer_conversation_key
from universal_userio.contact_start import ContactStartError
from universal_userio.contracts import ConversationPolicy, InboxMessage
from universal_userio.mcp_surface import UserIOMcpSurface
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def propose(self, *_args, **_kwargs):
        return ""


class Outbox:
    def send(self, *_args, **_kwargs):
        return "noop"


class FakeGmailOutbox:
    def __init__(self):
        self.sent = []

    def send_reply(self, **kwargs):
        self.sent.append(dict(kwargs))
        return f"himalaya:{kwargs.get('account')}:{kwargs.get('draft_id')}"

    def send_message(self, **kwargs):
        self.sent.append(dict(kwargs))
        return f"himalaya:{kwargs.get('account')}:{kwargs.get('draft_id')}"


class FakeSmsGateway:
    def __init__(self):
        self.sent = []

    def send(self, **kwargs):
        self.sent.append(dict(kwargs))
        return "sms-receipt-1"


class FakeTelegramOutbox:
    def __init__(self):
        self.sent = []
        self.resolved = []

    def resolve_contact(self, identifier, *, account_ref):
        self.resolved.append((identifier, account_ref))
        return {"peer_id": "5045123456", "name": "Новый контакт", "username": identifier}

    def send_reply(self, **kwargs):
        self.sent.append(dict(kwargs))
        return "telegram:receipt"


def build(tmp_path, **kwargs):
    store = SQLiteUserIOStore(tmp_path / 'userio.sqlite3')
    service = UserIOService(store, Generator(), Outbox(), **kwargs)
    return store, service, store.default_user_id


def register_gmail(store, user, *, can_reply=True, credential_ref='himalaya:test'):
    store.register_account(account_id='gmail-test', provider='gmail', display_name='owner@example.com',
                           enabled=True, can_read=True, can_reply=can_reply, credential_ref=credential_ref,
                           user_id=user)


def register_sms(store, user, *, can_reply=True):
    store.register_account(account_id='sms:device', provider='sms', display_name='Телефон',
                           enabled=True, can_read=True, can_reply=can_reply,
                           credential_ref='sms-native:default', user_id=user)


def register_telegram(store, user, *, can_reply=True):
    store.register_account(account_id='telegram:5045123456', provider='telegram', display_name='Telegram',
                           enabled=True, can_read=True, can_reply=can_reply,
                           credential_ref='telegram-qr:account-5', user_id=user)


def test_mail_contact_opens_a_chat_and_sends_the_first_letter(tmp_path):
    gmail = FakeGmailOutbox()
    store, service, user = build(tmp_path, gmail_outbox=gmail)
    register_gmail(store, user)

    result = service.contact_starter.start(
        user_id=user, account_ref='gmail-test', identifier='New Person <person@example.com>',
        text='Привет, это первое письмо', request_id='contact-start-0001')

    assert result['ok'] is True
    assert result['channel'] == 'gmail:test'
    conversation = store.conversation(result['conversation_id'], user_id=user)
    assert conversation['account_ref'] == 'gmail-test'
    assert conversation['peer_id'] == 'person@example.com'
    assert len(gmail.sent) == 1
    sent = gmail.sent[0]
    assert sent['recipient'] == 'person@example.com'
    assert sent['in_reply_to'] == ''
    assert sent['body'] == 'Привет, это первое письмо'


def test_the_reply_of_a_new_mail_contact_stays_in_the_same_chat(tmp_path):
    gmail = FakeGmailOutbox()
    store, service, user = build(tmp_path, gmail_outbox=gmail)
    register_gmail(store, user)

    started = service.contact_starter.start(
        user_id=user, account_ref='gmail-test', identifier='person@example.com',
        text='Привет', request_id='contact-start-0002')
    conversation_id = started['conversation_id']

    policy = ConversationPolicy('gmail-read-only')
    service.receive(
        InboxMessage('gmail:test', 'gmail-1', 'New Person <person@example.com>', 'Ответ', 10.0,
                     peer_id='New Person <person@example.com>'),
        route_id='gmail-read-only', user_id=user, account_ref='gmail-test')

    reply = store.conversation(conversation_id, user_id=user)
    assert len(reply['messages']) == 1
    assert reply['messages'][0]['body'] == 'Ответ'


def test_a_known_mail_contact_still_answers_with_in_reply_to(tmp_path):
    gmail = FakeGmailOutbox()
    store, service, user = build(tmp_path, gmail_outbox=gmail)
    register_gmail(store, user)
    policy = ConversationPolicy('gmail-read-only')
    store.ingest(
        InboxMessage('gmail:test', 'gmail-1', 'person@example.com', 'вопрос', 10.0),
        conversation_id='conv_known', policy=policy, account_ref='gmail-test', user_id=user)

    service.send_manual_message('conv_known', body='ответ', request_id='contact-start-0003',
                                 user_id=user)

    assert gmail.sent[0]['message_id'] == 'gmail-1'


def test_sms_contact_opens_a_chat_with_the_native_address(tmp_path):
    sms = FakeSmsGateway()
    store, service, user = build(tmp_path, sms_gateway=sms, sms_user_id='user_owner')
    register_sms(store, user)

    result = service.contact_starter.start(
        user_id=user, account_ref='sms:device', identifier='+7 926 781-78-74',
        text='Привет', request_id='contact-start-0004')

    conversation = store.conversation(result['conversation_id'], user_id=user)
    assert store.conversation_id_for_key('sms:+79267817874', user_id=user) == result['conversation_id']
    assert sms.sent[0]['to'] == '+79267817874'


def test_telegram_contact_is_resolved_through_the_connector(tmp_path):
    telegram = FakeTelegramOutbox()
    store, service, user = build(tmp_path, telegram_outbox=telegram)
    register_telegram(store, user)

    result = service.contact_starter.start(
        user_id=user, account_ref='telegram:5045123456', identifier='@new_contact',
        text='Привет', request_id='contact-start-0005')

    assert telegram.resolved == [('new_contact', 'telegram:5045123456')]
    conversation = store.conversation(result['conversation_id'], user_id=user)
    assert store.conversation_id_for_key(
        'telegram:telegram:5045123456:5045123456', user_id=user) == result['conversation_id']
    assert conversation['peer_id'] == '5045123456'
    assert telegram.sent[0]['chat_id'] == '5045123456'
    assert telegram.sent[0]['body'] == 'Привет'


def test_the_same_request_id_does_not_send_twice(tmp_path):
    gmail = FakeGmailOutbox()
    store, service, user = build(tmp_path, gmail_outbox=gmail)
    register_gmail(store, user)

    first = service.contact_starter.start(
        user_id=user, account_ref='gmail-test', identifier='person@example.com',
        text='Привет', request_id='contact-start-0006')
    second = service.contact_starter.start(
        user_id=user, account_ref='gmail-test', identifier='person@example.com',
        text='Привет', request_id='contact-start-0006')

    assert first['conversation_id'] == second['conversation_id']
    assert len(gmail.sent) == 1


def test_an_account_without_reply_right_cannot_start_a_chat(tmp_path):
    gmail = FakeGmailOutbox()
    store, service, user = build(tmp_path, gmail_outbox=gmail)
    register_gmail(store, user, can_reply=False)

    with pytest.raises(ContactStartError) as error:
        service.contact_starter.start(
            user_id=user, account_ref='gmail-test', identifier='person@example.com',
            text='Привет', request_id='contact-start-0007')
    assert 'reply_capable' in str(error.value)


def test_an_unknown_account_is_refused(tmp_path):
    store, service, user = build(tmp_path)
    with pytest.raises(ContactStartError) as error:
        service.contact_starter.resolve(
            user_id=user, account_ref='gmail-nope', identifier='person@example.com')
    assert 'not_found' in str(error.value)


def test_a_provider_without_resolution_is_refused(tmp_path):
    store, service, user = build(tmp_path)
    store.register_account(account_id='max', provider='max', display_name='MAX', enabled=True,
                           can_read=True, can_reply=True, credential_ref='env:USERIO_MAX_TOKEN',
                           user_id=user)
    with pytest.raises(ContactStartError) as error:
        service.contact_starter.resolve(user_id=user, account_ref='max', identifier='79999999999')
    assert 'unsupported' in str(error.value)


def test_the_new_chat_uses_the_same_key_as_the_inbound_message(tmp_path):
    telegram = FakeTelegramOutbox()
    store, service, user = build(tmp_path, telegram_outbox=telegram)
    register_telegram(store, user)
    service.contact_starter.resolve(user_id=user, account_ref='telegram:5045123456',
                                   identifier='new_contact')

    conversation = store.ensure_contact_conversation(
        'telegram', 'Новый контакт', user_id=user, account_ref='telegram:5045123456',
        peer_id='5045123456', conversation_kind='direct', route_id='telegram')
    service.receive(
        InboxMessage('telegram', 'telegram:5045123456|5045123456:1', 'Новый контакт', 'привет',
                     10.0, peer_id='5045123456'),
        route_id='telegram', user_id=user, account_ref='telegram:5045123456')

    assert store.conversation_id_for_key(
        'telegram:telegram:5045123456:5045123456', user_id=user) == conversation['id']
    assert len(store.conversation(conversation['id'], user_id=user)['messages']) == 1


@pytest.mark.parametrize('source,account_ref,peer_id,sender,key', [
    ('telegram', 'telegram:1', '5045123456', 'Ivan', 'telegram:telegram:1:5045123456'),
    ('vk', 'vk:1', '100', 'Ivan', 'vk:vk:1:100'),
    ('max', 'max', '-79812007134', 'Ivan', 'max:max:-79812007134'),
    ('yandex', 'yandex:1', '0/22/abc', 'Ivan', 'yandex:yandex:1:0/22/abc'),
    ('gmail', 'gmail-test', 'person@example.com', 'person@example.com',
     'email:person@example.com'),
    ('gmail:careviolan', 'gmail-careviolan', 'person@example.com', 'person@example.com',
     'email:person@example.com'),
])
def test_the_ingress_key_matches_the_canonical_key(tmp_path, source, account_ref, peer_id,
                                                    sender, key):
    store, service, user = build(tmp_path)
    message = InboxMessage(source, f'{source}-1', sender, 'текст', 10.0, peer_id=peer_id)

    conversation_id, _ = service.receive(
        message, route_id='r', user_id=user, account_ref=account_ref)

    assert store.conversation_id_for_key(key, user_id=user) == conversation_id


def test_the_native_sms_ingress_key_matches_the_canonical_key(tmp_path):
    store, service, user = build(tmp_path)
    register_sms(store, user)
    message = InboxMessage('sms', 'sms-1', '+79267817874', 'текст', 10.0,
                           peer_id='+79267817874')

    conversation_id, _ = service.receive(
        message, route_id='sms', user_id=user, account_ref='sms:device')

    assert store.conversation_id_for_key('sms:+79267817874', user_id=user) == conversation_id


def test_a_scoped_token_may_not_open_a_new_contact(tmp_path):
    gmail = FakeGmailOutbox()
    store, service, user = build(tmp_path, gmail_outbox=gmail)
    register_gmail(store, user)
    principal = replace(store.owner(), messenger_grants=(
        {'account_ref': 'gmail-test', 'access': 'read_write', 'chats': []},))

    surface = UserIOMcpSurface(store, service, principal)
    result = surface.dispatch('userio.contacts.start', {
        'account_ref': 'gmail-test', 'identifier': 'person@example.com',
        'text': 'Привет', 'request_id': 'contact-start-0008',
    })
    assert result['ok'] is False
    assert result['error'] == 'account_scope_required_for_new_contact'
    assert gmail.sent == []
