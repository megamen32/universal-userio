"""Explicit, revocable cross-profile Telegram grants over the scoped MCP path."""
from __future__ import annotations

import json
import hashlib
import fcntl
from contextlib import contextmanager
import os
import time
import urllib.parse
import urllib.request
import uuid

from .contracts import UserPrincipal
from .scoped_permissions import enforce, dispatch_scoped

_ALLOWED = {'userio.channels.list', 'userio.channels.read', 'userio.messenger.history',
            'userio.messenger.get', 'userio.messenger.propose', 'userio.messenger.approve'}


def _full(principal):
    if (principal.service_account or principal.read_only or principal.meeting_recorder
            or principal.messenger_accounts is not None or principal.messenger_grants is not None):
        raise PermissionError('full_profile_required')


def _folder(account, folder_id):
    base = os.environ.get('USERIO_TELEGRAM_QR_URL', '').rstrip('/')
    token = os.environ.get('USERIO_TELEGRAM_QR_TOKEN') or os.environ.get('USERIO_API_TOKEN', '')
    if not base or not token:
        raise ValueError('folder_authority_unavailable')
    query = urllib.parse.urlencode({'account_id': account, 'folder_ids': folder_id})
    req = urllib.request.Request(base + '/folders?' + query, headers={'Authorization': 'Bearer ' + token})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.load(response)
        folders = data['folders']
        if data['account_id'] != account or len(folders) != 1 or folders[0]['id'] != folder_id:
            raise ValueError()
        f = folders[0]
        if any(f['categories'].values()) or any(f[k] for k in ('exclude_muted', 'exclude_read', 'exclude_archived')):
            raise ValueError()
        included = set(f['include_peer_ids']) | set(f['pinned_peer_ids'])
        return included - set(f['exclude_peer_ids'])
    except Exception as exc:
        raise ValueError('folder_authority_unavailable') from exc


class ProfileSharing:
    def __init__(self, store, service):
        self.store, self.service = store, service
        with store._lock, store._connection:
            store._connection.execute('''CREATE TABLE IF NOT EXISTS profile_shares
                (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, grantee_id TEXT NOT NULL,
                 account_ref TEXT NOT NULL, folder_id INTEGER NOT NULL, peers TEXT NOT NULL,
                 created_at REAL NOT NULL, revoked_at REAL)''')
            store._connection.execute('''CREATE TABLE IF NOT EXISTS profile_share_audit
                (id INTEGER PRIMARY KEY AUTOINCREMENT, share_id TEXT NOT NULL,
                 actor_id TEXT NOT NULL, operation TEXT NOT NULL, outcome TEXT NOT NULL,
                 action_id TEXT, at REAL NOT NULL)''')
            store._connection.execute('''CREATE TABLE IF NOT EXISTS profile_share_actions
                (share_id TEXT NOT NULL, action_id TEXT NOT NULL, PRIMARY KEY(share_id,action_id))''')

    @contextmanager
    def _fence(self):
        # Calls and revocations in separate server processes share this fence.
        # A revocation waits for an already started operation; after it returns,
        # subsequent calls re-read the revoked state before doing anything.
        fd = os.open(self.store._path + '.profile-sharing.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _audit(self, share, actor, operation, outcome, action=None):
        with self.store._connection:
            self.store._connection.execute('INSERT INTO profile_share_audit (share_id,actor_id,operation,outcome,action_id,at) VALUES (?,?,?,?,?,?)',
                                           (share, actor, operation, outcome, action, time.time()))

    def create(self, actor, grantee_id, account_ref, folder_id, peers):
        _full(actor)
        if (not isinstance(grantee_id, str) or grantee_id == actor.user_id or
            not isinstance(account_ref, str) or not account_ref.startswith('telegram:') or
            type(folder_id) is not int or folder_id < 2 or not isinstance(peers, list) or
            not 1 <= len(peers) <= 256 or any(not isinstance(p, str) or not p for p in peers) or len(set(peers)) != len(peers)):
            raise ValueError('invalid_share')
        with self.store._lock:
            recipient = self.store._connection.execute('SELECT id FROM users WHERE id=?', (grantee_id,)).fetchone()
            accounts = {a['id']: a for a in self.store.accounts(user_id=actor.user_id)}
            if not recipient or account_ref not in accounts or not accounts[account_ref]['enabled'] or accounts[account_ref]['provider'] != 'telegram':
                raise ValueError('invalid_share_identity')
            if not set(peers) <= _folder(account_ref, folder_id):
                raise ValueError('peer_outside_folder')
            for peer in peers:
                rows = self.store._connection.execute('SELECT id FROM conversations WHERE user_id=? AND account_ref=? AND peer_id=? AND source=?',
                                                      (actor.user_id, account_ref, peer, 'telegram')).fetchall()
                if not rows:
                    raise ValueError('peer_conversation_missing')
            share = 'share_' + uuid.uuid4().hex
            with self.store._connection:
                self.store._connection.execute('INSERT INTO profile_shares VALUES (?,?,?,?,?,?,?,NULL)',
                    (share, actor.user_id, grantee_id, account_ref, folder_id, json.dumps(peers), time.time()))
                self._audit(share, actor.user_id, 'create', 'ok')
            return {'ok': True, 'share_id': share}

    def revoke(self, actor, share_id):
        _full(actor)
        with self._fence(), self.store._lock, self.store._connection:
            changed = self.store._connection.execute('UPDATE profile_shares SET revoked_at=? WHERE id=? AND owner_id=? AND revoked_at IS NULL',
                (time.time(), share_id, actor.user_id)).rowcount
            if not changed:
                raise KeyError('share_not_found')
            self._audit(share_id, actor.user_id, 'revoke', 'ok')
        return {'ok': True, 'revoked': True}

    def list(self, actor):
        _full(actor)
        with self.store._lock:
            rows = self.store._connection.execute('SELECT id,owner_id,grantee_id,account_ref,folder_id,revoked_at FROM profile_shares WHERE owner_id=? OR grantee_id=? ORDER BY created_at', (actor.user_id, actor.user_id)).fetchall()
            return {'ok': True, 'shares': [dict(r) for r in rows]}

    def audit(self, actor, share_id):
        _full(actor)
        with self.store._lock:
            row = self.store._connection.execute('SELECT 1 FROM profile_shares WHERE id=? AND owner_id=?', (share_id, actor.user_id)).fetchone()
            if not row:
                raise KeyError('share_not_found')
            rows = self.store._connection.execute('SELECT actor_id,operation,outcome,action_id,at FROM profile_share_audit WHERE share_id=? ORDER BY id DESC LIMIT 200', (share_id,)).fetchall()
            return {'ok': True, 'events': [dict(r) for r in rows]}

    def call(self, actor, share_id, operation, arguments):
        _full(actor)
        if operation not in _ALLOWED or not isinstance(arguments, dict):
            raise PermissionError('share_operation_denied')
        required = 'send' if operation in {'userio.messenger.propose', 'userio.messenger.approve'} else 'read'
        if not self.store.capability_enabled(required, user_id=actor.user_id):
            raise PermissionError(required + '_capability_disabled')
        arguments = dict(arguments)
        if operation == 'userio.messenger.propose':
            request_id = arguments.get('request_id')
            if not isinstance(request_id, str) or not 16 <= len(request_id) <= 128:
                raise ValueError('request_id_required')
            arguments['request_id'] = 'share_' + hashlib.sha256((share_id + '\0' + request_id).encode()).hexdigest()
        # Recheck the live folder and immutable peer snapshot under the same
        # fence used by revoke; unavailable authority fails closed.
        with self._fence(), self.store._lock:
            row = self.store._connection.execute('SELECT * FROM profile_shares WHERE id=? AND grantee_id=? AND revoked_at IS NULL', (share_id, actor.user_id)).fetchone()
            if not row:
                raise PermissionError('share_not_found')
            account = row['account_ref']
            peers = set(json.loads(row['peers'])) & _folder(account, row['folder_id'])
            chats = []
            for peer in peers:
                matches = self.store._connection.execute('SELECT id FROM conversations WHERE user_id=? AND account_ref=? AND peer_id=? AND source=?', (row['owner_id'], account, peer, 'telegram')).fetchall()
                chats.extend({'conversation_id': match['id'], 'access': 'read_write'} for match in matches)
            if not chats:
                raise PermissionError('share_empty')
            if operation == 'userio.messenger.propose' and arguments.get('action') != 'reply':
                raise PermissionError('share_reply_only')
            action_id = arguments.get('action_id')
            if operation in {'userio.messenger.get', 'userio.messenger.approve'}:
                if not isinstance(action_id, str) or not self.store._connection.execute('SELECT 1 FROM profile_share_actions WHERE share_id=? AND action_id=?', (share_id, action_id)).fetchone():
                    raise PermissionError('share_action_not_found')
            grant = {'account_ref': account, 'access': 'read_write', 'chats': chats, 'telegram_admin': False}
            owner = self.store._connection.execute('SELECT username,role FROM users WHERE id=?', (row['owner_id'],)).fetchone()
            principal = UserPrincipal(row['owner_id'], owner['username'], owner['role'], messenger_accounts=(account,), messenger_grants=(grant,))
            try:
                enforce(self.store, principal, operation, arguments)
                result = dispatch_scoped(self.store, self.service, principal, operation, arguments)
                if operation == 'userio.messenger.propose' and result.get('ok'):
                    action_id = result['action']['id']
                    with self.store._connection:
                        self.store._connection.execute('INSERT OR IGNORE INTO profile_share_actions VALUES (?,?)', (share_id, action_id))
                self._audit(share_id, actor.user_id, operation, 'ok' if result.get('ok') else 'denied', action_id)
                return result
            except Exception:
                self._audit(share_id, actor.user_id, operation, 'denied', action_id)
                raise
