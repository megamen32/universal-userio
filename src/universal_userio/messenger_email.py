"""Generic mail operations on the existing inbox mirror and Himalaya SMTP."""
import re
import time
from email.utils import parseaddr

from .contracts import InboxMessage
from .delivery_receipt import read_receipt
from .messenger import Messenger


def mail_id(value):
    return isinstance(value, str) and 0 < len(value) <= 512 and not re.search(r'[\s<>\x00-\x1f]', value)


def mail_address(value):
    return isinstance(value, str) and parseaddr(value)[1] == value and bool(re.fullmatch(r'[^\s@<>]+@[^\s@<>]+', value))


class GmailMessengerProvider:
    operations = frozenset({'reply'})  # Inbox history is the maintained mirror, not live IMAP refresh.

    def __init__(self, service, account, user_id):
        self.service, self.authorized_user = service, user_id
        self.account_ref = account['id']
        self.alias = account['credential_ref'].removeprefix('himalaya:')
        self.source = 'gmail' if self.alias == 'gmail' else 'gmail:'+self.alias
        self.sender_id = parseaddr(account['display_name'])[1]
        if not mail_address(self.sender_id):raise ValueError('configured mailbox address required')

    def conversation(self, value):
        if (value['source'] != self.source or
                value['account_ref'] not in {self.account_ref, self.alias}):
            raise KeyError('conversation_not_found')
        peer = value.get('peer_id') or value['sender']
        if not mail_address(peer):raise ValueError('actual_mail_author_required')
        return dict(value, account_ref=self.account_ref, peer_id=peer)

    def resolve(self, peer_id, provider_message_id):
        if not mail_address(peer_id) or not mail_id(provider_message_id):raise ValueError('exact_mail_source_required')
        store = self.service._store
        message = store.message(provider_message_id, source=self.source, user_id=self.authorized_user)
        if not message:raise KeyError('message_not_found')
        conv = self.conversation(store.conversation(message['conversation_id'], user_id=self.authorized_user))
        if conv['peer_id'] != peer_id:raise ValueError('mail_peer_conflict')
        return conv['id']

    def execute(self, envelope):
        if envelope['action'] != 'reply':raise ValueError('mail_operation_not_supported')
        if envelope['user_id'] != self.authorized_user:raise ValueError('mail_user_conflict')
        api = Messenger(self.service._store, self.service, self.service._store.user(self.authorized_user))
        conv = api._conversation(self.account_ref, envelope['conversation_id'])
        projected, current = api._binding(conv, envelope['source_message_id'])
        if (current != envelope['source_hash'] or projected['binding'] != envelope['source_binding']
                or envelope['peer_id'] != conv['peer_id'] or envelope['account_ref'] != self.account_ref
                or envelope['channel'] != 'gmail'):
            raise ValueError('mail_source_snapshot_conflict')
        source_id = projected['source']['provider_message_id']
        if not mail_id(source_id) or '@' not in source_id:
            raise ValueError('actual_rfc_message_id_required')
        subject = next((line.removeprefix('Тема: ').strip() for line in projected['text'].splitlines()
                        if line.startswith('Тема: ')), 'UserIO reply')
        subject = subject if subject.lower().startswith('re:') else 'Re: '+subject
        outbox = self.service.gmail_outbox
        receipt = outbox.send_message(account=self.alias, sender=self.sender_id,
            recipient=conv['peer_id'], message_id=source_id, body=envelope['payload']['text'],
            draft_id=envelope['action_id'], subject=subject[:998])
        delivery = read_receipt(receipt)
        return {'account_ref':self.account_ref, 'peer_id':conv['peer_id'], 'receipt':receipt,
            'provider_message_id':outbox.outgoing_message_id(self.alias, envelope['action_id']),
            'sent_copy':delivery.get('sent_copy') or {},
            'submitted_at':time.time(), 'delivery_status':'smtp_accepted'}

    def project_receipt(self, envelope, result):
        # An accepted outgoing mirror is reconciliation; it never becomes AI input.
        self.service.receive(InboxMessage(source=self.source, message_id=result['provider_message_id'],sender=envelope['peer_id'],
            body=envelope['payload']['text'], received_at=result['submitted_at'], direction='outgoing',
            reconciliation=True, peer_id=envelope['peer_id'], conversation_kind='direct',
            provider_reply_to=envelope['source_provider_message_id'], provider_thread_id=envelope['thread_id']),
            route_id='gmail-read-only', user_id=self.authorized_user, account_ref=self.account_ref)
