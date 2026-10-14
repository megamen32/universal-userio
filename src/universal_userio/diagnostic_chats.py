"""Owned virtual chats in the NORMAL UserIO conversation/message store.

No provider, credentials, AI loop or account impersonation. actor_ref is an
opaque test subject for the consuming app to verify, NEVER authentication.
"""
from __future__ import annotations
import hashlib
import json
import re
import time
from .contracts import ConversationPolicy, InboxMessage


class DiagnosticChats:
    def __init__(self, store):
        self.store = store
        with store._lock, store._connection:
            store._connection.execute("""CREATE TABLE IF NOT EXISTS diagnostic_chats(
              user_id TEXT NOT NULL, conversation_id TEXT NOT NULL, request_id TEXT NOT NULL,
              actor_ref TEXT NOT NULL, title TEXT NOT NULL, create_hash TEXT NOT NULL,
              PRIMARY KEY(user_id,conversation_id), UNIQUE(user_id,request_id))""")
            store._connection.execute("""CREATE TABLE IF NOT EXISTS diagnostic_chat_requests(
              user_id TEXT NOT NULL, conversation_id TEXT NOT NULL, request_id TEXT NOT NULL,
              input_hash TEXT NOT NULL, message_id TEXT NOT NULL,
              PRIMARY KEY(user_id,conversation_id,request_id))""")

    @staticmethod
    def request_id(value):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', value):
            raise ValueError('stable diagnostic request_id required')
        return value

    @staticmethod
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def create(self, *, user_id, request_id, title='Диагностический чат', actor_ref=''):
        self.request_id(request_id)
        if not isinstance(title, str) or not title.strip() or len(title) > 160:
            raise ValueError('bounded title required')
        if not isinstance(actor_ref, str) or len(actor_ref) > 160:
            raise ValueError('bounded opaque actor_ref required')
        digest = self.digest([title, actor_ref])
        with self.store._lock, self.store._connection:
            prior = self.store._connection.execute(
                'SELECT conversation_id,create_hash FROM diagnostic_chats WHERE user_id=? AND request_id=?',
                (user_id, request_id)).fetchone()
            if prior:
                if prior['create_hash'] != digest: raise ValueError('request_id_conflict')
                return self.get(user_id=user_id, conversation_id=prior['conversation_id'])
            # An actual UserIO conversation, not an independent chat table.
            conv = self.store.create_conversation('diagnostic', 'test-'+self.digest([user_id, request_id])[:24], user_id=user_id)
            self.store._connection.execute('INSERT INTO diagnostic_chats VALUES(?,?,?,?,?,?)',
                (user_id, conv['id'], request_id, actor_ref, title, digest))
            self.store._connection.execute("UPDATE conversations SET account_ref='diagnostic',peer_id=?,conversation_kind='unknown' WHERE user_id=? AND id=?",
                (conv['id'], user_id, conv['id']))
        return self.get(user_id=user_id, conversation_id=conv['id'])

    def get(self, *, user_id, conversation_id):
        with self.store._lock:
            row = self.store._connection.execute(
                'SELECT title,actor_ref FROM diagnostic_chats WHERE user_id=? AND conversation_id=?',
                (user_id, conversation_id)).fetchone()
            if row is None: raise KeyError('diagnostic_chat_not_found')
            conv = self.store.conversation(conversation_id, user_id=user_id)
            messages = self.store._connection.execute(
                'SELECT * FROM messages WHERE user_id=? AND conversation_id=? AND deleted_at IS NULL ORDER BY received_at DESC,rowid DESC LIMIT 201',
                (user_id, conversation_id)).fetchall()
            # One bounded chronological tail. The normal conversation projection
            # already includes history; do not return it twice.
            conv.pop('messages', None)
        return dict(ok=True, diagnostic=True, actor_ref_is_authority=False,
                    title=row['title'], actor_ref=row['actor_ref'], conversation=conv,
                    messages=[dict(m) for m in reversed(messages[:200])], truncated=len(messages)>200,
                    external_actions=0)

    def message(self, *, user_id, conversation_id, request_id, body, direction='incoming'):
        self.request_id(request_id)
        if direction not in {'incoming', 'outgoing'} or not isinstance(body, str) or not body.strip() or len(body.encode()) > 16384:
            raise ValueError('bounded diagnostic body and direction required')
        chat = self.get(user_id=user_id, conversation_id=conversation_id)
        digest = self.digest([body, direction])
        message_id = 'diagmsg_'+self.digest([user_id, conversation_id, request_id])
        with self.store._lock, self.store._connection:
            prior = self.store._connection.execute(
                'SELECT input_hash FROM diagnostic_chat_requests WHERE user_id=? AND conversation_id=? AND request_id=?',
                (user_id, conversation_id, request_id)).fetchone()
            if prior and prior['input_hash'] != digest: raise ValueError('request_id_conflict')
            if not prior:
                stored = self.store._connection.execute(
                    "SELECT body,direction FROM messages WHERE user_id=? AND source='diagnostic' AND message_id=?",
                    (user_id, message_id)).fetchone()
                if stored and (stored['body'] != body or stored['direction'] != direction):
                    raise ValueError('request_id_conflict')
                # reconciliation keeps this TEST input out of production
                # automatic claims. It remains a normal persisted message.
                self.store.ingest(InboxMessage(source='diagnostic', message_id=message_id,
                    sender=chat['conversation']['sender'], sender_name=chat['title'], body=body,
                    received_at=time.time(), direction=direction, peer_id=conversation_id,
                    conversation_kind='unknown', reconciliation=True),
                    conversation_id=conversation_id, user_id=user_id, account_ref='diagnostic',
                    conversation_key='diagnostic:'+chat['conversation']['sender'], policy=ConversationPolicy('diagnostic', 'approve'))
                self.store._connection.execute('INSERT INTO diagnostic_chat_requests VALUES(?,?,?,?,?)',
                    (user_id, conversation_id, request_id, digest, message_id))
        return dict(ok=True, diagnostic=True, conversation_id=conversation_id, message_id=message_id,
                    duplicate=bool(prior), external_actions=0)


def dispatch(store, principal, name, args):
    if principal.read_only or principal.messenger_accounts is not None or principal.meeting_recorder:
        raise PermissionError('diagnostic_requires_full_user_scope')
    capability = 'read' if name == 'userio.diagnostic.get' else 'send'
    if not store.capability_enabled(capability, user_id=principal.user_id):
        raise PermissionError(capability+'_capability_disabled')
    chats = DiagnosticChats(store)
    operations = {'userio.diagnostic.create': chats.create,
                  'userio.diagnostic.get': chats.get, 'userio.diagnostic.message': chats.message}
    if name not in operations: raise ValueError('unknown diagnostic operation')
    return operations[name](user_id=principal.user_id, **args)
