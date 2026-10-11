#!/usr/bin/env python3
"""Reconcile exact CDR call IDs, preserving transcripts and old conversation links.

Focused integration checks: tests/test_phone_grouping.py; expected <30s, max60s.
Neither planning nor application originates a call or changes automation policy.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from universal_userio.store import SQLiteUserIOStore
from universal_userio.phone_grouping import cdr_peers, grouping_plan, apply_grouping

REFERENCE_KEYS = {'messages': ('source', 'message_id'), 'drafts': ('id',), 'workspace_events': ('seq',)}
CHAT_FIELDS = ('conversation_key', 'account_ref', 'peer_id', 'updated_at')


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def rows(connection, table, user, ids):
    q = ','.join('?' for _ in ids)
    return [dict(row) for row in connection.execute(
        f'SELECT * FROM {table} WHERE user_id=? AND conversation_id IN ({q})', [user, *ids])]


def indexed(items, keys):
    return {tuple(item[k] for k in keys): item for item in items}


def apply_with_backup(store, plan, labels, state, call_peers):
    if not plan['groups']:
        return {'groups': 0, 'aliases_added': 0, 'unmatched': len(plan['unmatched']), 'changed': False}
    stamp = str(time.time_ns())
    backup = state / ('backup-' + stamp + '.sqlite3')
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    dest = sqlite3.connect(backup)
    try:
        with store._lock:
            store._connection.backup(dest, pages=128, sleep=0.01)
        if dest.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('backup is not consistent')
        dest.row_factory = sqlite3.Row
        receipt = state / ('receipt-' + stamp + '.json')
        actual_labels = {g['peer_ref']: labels[g['peer_ref']] for g in plan['groups'] if g['peer_ref'] in labels}
        save(receipt, {'backup': str(backup), 'backup_sha256': digest(backup), 'plan': plan,
                       'labels': actual_labels, 'at': time.time()})
        with store._lock, store._connection:
            connection = store._connection
            connection.execute('BEGIN IMMEDIATE')
            # Compare the live exact references with the consistent snapshot.
            # An intervening call/draft/event is retained and refuses this plan.
            actual = grouping_plan(connection, user_id=plan['user_id'], account_ref=plan['account_ref'], call_peers=call_peers)
            if actual['groups'] != plan['groups']:
                raise ValueError('phone conversation changed after backup; apply refused')
            for group in plan['groups']:
                for table, keys in REFERENCE_KEYS.items():
                    if indexed(rows(dest, table, plan['user_id'], group['sources']), keys) != indexed(rows(connection, table, plan['user_id'], group['sources']), keys):
                        raise ValueError('phone history changed after backup; apply refused')
            result = apply_grouping(connection, plan, labels)
        return {**result, 'changed': True, 'receipt': str(receipt), 'backup_consistent': True,
                'backup_sha256': digest(backup)}
    finally:
        dest.close()


def rollback(store, receipt):
    record = json.loads(Path(receipt).read_text())
    backup = Path(record['backup'])
    if digest(backup) != record['backup_sha256']:
        raise ValueError('backup checksum mismatch')
    prior = sqlite3.connect(backup.as_uri() + '?mode=ro', uri=True)
    prior.row_factory = sqlite3.Row
    plan, connection = record['plan'], store._connection
    user = plan['user_id']
    try:
        with store._lock, connection:
            connection.execute('BEGIN IMMEDIATE')
            for group in plan['groups']:
                canonical, ids = group['canonical'], group['sources']
                placeholders = ','.join('?' for _ in ids)
                for table in ('workspace_exclusions', 'workspace_chat_rules', 'conversation_secretary_jobs', 'conversation_summaries', 'draft_reply_origins'):
                    if connection.execute(f'SELECT 1 FROM {table} WHERE user_id=? AND conversation_id IN ({placeholders}) LIMIT 1', [user,*ids]).fetchone():
                        raise ValueError('phone policy or cached context changed; scoped rollback must be reviewed')
                active = connection.execute(f"SELECT 1 FROM workspace_claims w JOIN workspace_events e ON e.user_id=w.user_id AND e.seq=w.event_seq WHERE e.user_id=? AND e.conversation_id IN ({placeholders}) AND w.status='claimed' AND w.lease_expires_at>? LIMIT 1", [user,*ids,time.time()]).fetchone()
                sending = connection.execute(f"SELECT 1 FROM drafts WHERE user_id=? AND conversation_id IN ({placeholders}) AND status='sending' LIMIT 1", [user,*ids]).fetchone()
                if active or sending:
                    raise ValueError('phone grouping has an active consumer; scoped rollback refused')
                current = connection.execute('SELECT * FROM conversations WHERE user_id=? AND id=?', (user, canonical)).fetchone()
                expected = (group['conversation_key'], plan['account_ref'], group['peer_ref'], max(chat['updated_at'] for chat in group['previous_conversations']))
                if current is None or tuple(current[k] for k in CHAT_FIELDS) != expected:
                    raise ValueError('phone conversation changed; scoped rollback must be reviewed')
                for old in ids:
                    if old != canonical:
                        row = connection.execute('SELECT canonical_id FROM phone_conversation_aliases WHERE user_id=? AND old_id=?', (user, old)).fetchone()
                        if row is None or row[0] != canonical:
                            raise ValueError('phone alias changed; scoped rollback must be reviewed')
                for table, keys in REFERENCE_KEYS.items():
                    before = rows(prior, table, user, ids)
                    now = rows(connection, table, user, [canonical])
                    if set(indexed(before, keys)) != set(indexed(now, keys)):
                        raise ValueError('phone history changed; scoped rollback must be reviewed')
                    for item in before:
                        where = ' AND '.join(k + '=?' for k in keys)
                        connection.execute(f'UPDATE {table} SET conversation_id=? WHERE user_id=? AND {where}',
                                           [item['conversation_id'], user, *(item[k] for k in keys)])
                for chat in group['previous_conversations']:
                    connection.execute('UPDATE conversations SET ' + ','.join(k + '=?' for k in CHAT_FIELDS) + ' WHERE user_id=? AND id=?',
                                       [*(chat[k] for k in CHAT_FIELDS), user, chat['id']])
                for old in ids:
                    if old != canonical:
                        connection.execute('DELETE FROM phone_conversation_aliases WHERE user_id=? AND old_id=? AND canonical_id=?', (user, old, canonical))
                contact_key = (user, 'phone', f"chat:{plan['account_ref']}:{group['peer_ref']}")
                original = prior.execute('SELECT * FROM contact_names WHERE user_id=? AND source=? AND sender=?', contact_key).fetchone()
                if original is None and group['peer_ref'] in record['labels']:
                    current_name = connection.execute('SELECT name FROM contact_names WHERE user_id=? AND source=? AND sender=?', contact_key).fetchone()
                    if current_name is None or current_name[0] != record['labels'][group['peer_ref']]:
                        raise ValueError('phone contact changed; scoped rollback must be reviewed')
                    connection.execute('DELETE FROM contact_names WHERE user_id=? AND source=? AND sender=?', contact_key)
        return {'restored_groups': len(plan['groups'])}
    finally:
        prior.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', default='/etc/universal-userio.env')
    parser.add_argument('--cdr', default='/var/log/asterisk/cdr-csv/Master.csv')
    parser.add_argument('--account-ref', default='phone100')
    parser.add_argument('--user-id', default='user_owner')
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--rollback')
    args = parser.parse_args()
    db = ''
    for line in Path(args.env_file).read_text().splitlines():
        if line.startswith('USERIO_DB_PATH='):
            db = shlex.split(line.split('=', 1)[1])[0]
    if not db:
        raise ValueError('canonical USERIO_DB_PATH is missing')
    state = Path(args.state_dir).resolve()
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if state.stat().st_mode & 0o077:
        raise ValueError('state directory must be private')
    store = SQLiteUserIOStore(db)
    try:
        if args.rollback:
            print(json.dumps(rollback(store, args.rollback)))
            return
        peers, labels = cdr_peers(args.cdr, args.account_ref)
        plan = store.group_phone_conversations(account_ref=args.account_ref, call_peers=peers, user_id=args.user_id, dry_run=True)
        plan_path = state / ('plan-' + str(time.time_ns()) + '.json')
        save(plan_path, plan)
        summary = {'groups': len(plan['groups']), 'mapped_chats': sum(len(g['sources']) for g in plan['groups']),
                   'unmatched': len(plan['unmatched']), 'plan': str(plan_path)}
        if args.apply:
            summary.update(apply_with_backup(store, plan, labels, state, peers))
        print(json.dumps(summary))
    finally:
        store.close()


if __name__ == '__main__':
    main()
