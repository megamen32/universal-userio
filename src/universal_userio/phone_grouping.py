"""Exact CDR-backed phone conversation reconciliation; never originates a call."""
from __future__ import annotations
import csv
import hashlib
import re
import time
import uuid
from collections import defaultdict


def phone_peer_ref(account_ref: str, phone: str) -> str:
    digits = re.sub(r'\D', '', phone)
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    if not account_ref or not 8 <= len(digits) <= 15:
        raise ValueError('phone identity is incomplete')
    return 'phone:v1:' + hashlib.sha256((account_ref + '\0' + digits).encode()).hexdigest()


def cdr_peers(path: str, account_ref: str) -> tuple[dict[str, str], dict[str, str]]:
    """Use AudioSocket's exact UUID and actual outbound dst/inbound src only."""
    candidates = defaultdict(set)
    labels = {}
    with open(path, newline='') as stream:
        for row in csv.reader(stream):
            if len(row) < 9 or row[7] != 'AudioSocket':
                continue
            try:
                call = str(uuid.UUID(row[8].split(',', 1)[0]))
                number = row[2] if row[3] == 'gateway-outbound' else row[1] if row[3] == 'gateway-incoming' else ''
                peer = phone_peer_ref(account_ref, number)
            except ValueError:
                continue
            candidates[call].add(peer)
            digits = re.sub(r'\D', '', number)
            if len(digits) == 11 and digits.startswith('8'):
                digits = '7' + digits[1:]
            labels[peer] = 'Телефон · +' + digits
    # Conflicting CDR legs are not permission to choose one of the people.
    return {call: next(iter(peers)) for call, peers in candidates.items() if len(peers) == 1}, labels


def grouping_plan(connection, *, user_id: str, account_ref: str, call_peers: dict[str, str]) -> dict:
    if not account_ref or any(not re.fullmatch(r'phone:v1:[0-9a-f]{64}', p) for p in call_peers.values()):
        raise ValueError('invalid phone account/peer mapping')
    account = connection.execute("SELECT provider FROM provider_accounts WHERE user_id=? AND id=?", (user_id, account_ref)).fetchone()
    if account is None or account['provider'] != 'phone':
        raise ValueError('registered phone account is required')
    rows = connection.execute("""SELECT c.*,MIN(m.received_at) AS first_at
        FROM conversations c LEFT JOIN messages m ON m.user_id=c.user_id AND m.conversation_id=c.id
        WHERE c.user_id=? AND c.source='phone' AND c.account_ref IN ('',?)
          AND NOT EXISTS (SELECT 1 FROM phone_conversation_aliases a WHERE a.user_id=c.user_id AND a.old_id=c.id)
        GROUP BY c.user_id,c.id""", (user_id, account_ref)).fetchall()
    grouped = defaultdict(list); unknown = []
    for row in rows:
        calls = {r[0] for r in connection.execute("SELECT DISTINCT sender FROM messages WHERE user_id=? AND conversation_id=?", (user_id, row['id']))}
        peers = {call_peers[call] for call in calls if call in call_peers}
        if row['account_ref'] == account_ref and row['peer_id'].startswith('phone:v1:'):
            peers.add(row['peer_id'])
        if len(peers) != 1:
            unknown.append(row['id']); continue
        grouped[next(iter(peers))].append(dict(row))
    groups = []
    for peer, chats in grouped.items():
        key = f'phone:{account_ref}:{peer}'
        if len(chats) == 1 and chats[0]['conversation_key'] == key and chats[0]['account_ref'] == account_ref and chats[0]['peer_id'] == peer:
            continue
        chats.sort(key=lambda c: (c['conversation_key'] != key, c['first_at'] or c['updated_at'], c['id']))
        ids = [c['id'] for c in chats]; placeholders = ','.join('?' for _ in ids)
        if len({(c['route_id'], c['identity_id'], c['response_mode']) for c in chats}) != 1:
            raise ValueError('phone conversation policies conflict')
        for table in ('workspace_exclusions','workspace_chat_rules','conversation_secretary_jobs','conversation_summaries','draft_reply_origins'):
            count = connection.execute(f'SELECT COUNT(*) FROM {table} WHERE user_id=? AND conversation_id IN ({placeholders})', [user_id, *ids]).fetchone()[0]
            if count:
                raise ValueError(f'phone grouping requires reconciliation of {table}')
        active = connection.execute(f"""SELECT 1 FROM workspace_claims w JOIN workspace_events e
            ON e.user_id=w.user_id AND e.seq=w.event_seq WHERE e.user_id=?
            AND e.conversation_id IN ({placeholders}) AND w.status='claimed' AND w.lease_expires_at>? LIMIT 1""", [user_id, *ids, time.time()]).fetchone()
        sending = connection.execute(f"SELECT 1 FROM drafts WHERE user_id=? AND conversation_id IN ({placeholders}) AND status='sending' LIMIT 1", [user_id,*ids]).fetchone()
        if active or sending:
            raise ValueError('phone grouping has an active consumer')
        groups.append({'peer_ref': peer, 'conversation_key': key, 'canonical': ids[0], 'sources': ids,
                       'previous_conversations': chats})
    return {'user_id': user_id, 'account_ref': account_ref, 'groups': groups, 'unmatched': unknown}


def phone_label_changes(connection, plan: dict, labels: dict[str, str]) -> list[dict]:
    """Reveal only a CDR-backed number, keeping an operator's contact name."""
    changes = []
    for peer, label in labels.items():
        if not peer.startswith('phone:v1:'):
            # Operator-reviewed legacy diagnostics remain separate from real
            # people: changing the caption never invents a destination.
            row = connection.execute("SELECT 1 FROM conversations WHERE user_id=? AND source='phone' AND sender=? AND peer_id='' AND account_ref IN ('',?)", (plan['user_id'],peer,plan['account_ref'])).fetchone()
            if row is None:
                continue
            old = connection.execute("SELECT * FROM contact_names WHERE user_id=? AND source='phone' AND sender=?", (plan['user_id'],peer)).fetchone()
            if old is None or old['name'] != label:
                changes.append({'sender':peer,'before':dict(old) if old is not None else None,'name':label})
            continue
        if not re.fullmatch(r'Телефон · \+\d{8,15}', label):
            continue
        exists = connection.execute("SELECT 1 FROM conversations WHERE user_id=? AND source='phone' AND account_ref=? AND peer_id=?", (plan['user_id'], plan['account_ref'], peer)).fetchone()
        planned = any(group['peer_ref']==peer for group in plan['groups'])
        if exists is None and not planned:
            continue
        sender = f"chat:{plan['account_ref']}:{peer}"
        row = connection.execute("SELECT * FROM contact_names WHERE user_id=? AND source='phone' AND sender=?", (plan['user_id'], sender)).fetchone()
        if row is None and exists is None:
            continue  # A first grouping's inserted fallback has its own rollback.
        old_name = str(row['name']) if row is not None else ''
        number = label.removeprefix('Телефон · ')
        if old_name.endswith(number):
            continue
        new_name = label if not old_name or old_name.startswith('Телефон · ') else old_name + ' · ' + number
        changes.append({'sender': sender, 'before': dict(row) if row is not None else None, 'name': new_name})
    return changes


def apply_grouping(connection, plan: dict, labels: dict[str, str]) -> dict:
    """Caller owns one transaction. Move references, retain all old IDs as aliases."""
    user, account = plan['user_id'], plan['account_ref']
    moved = 0
    for group in plan['groups']:
        canonical, peer = group['canonical'], group['peer_ref']
        for old in group['sources']:
            if old == canonical:
                continue
            for table in ('messages', 'drafts', 'workspace_events'):
                connection.execute(f'UPDATE {table} SET conversation_id=? WHERE user_id=? AND conversation_id=?', (canonical,user,old))
            connection.execute('INSERT OR IGNORE INTO phone_conversation_aliases(user_id,old_id,canonical_id) VALUES (?,?,?)', (user,old,canonical))
            moved += 1
        connection.execute("UPDATE conversations SET conversation_key=?,account_ref=?,peer_id=?,updated_at=? WHERE user_id=? AND id=?", (group['conversation_key'],account,peer,max(c['updated_at'] for c in group['previous_conversations']),user,canonical))
        label = labels.get(peer)
        if label:
            # Keep a provider/operator name; reveal a proven number below.
            connection.execute('INSERT OR IGNORE INTO contact_names(user_id,source,sender,name,updated_at) VALUES (?,\'phone\',?,?,?)', (user,f'chat:{account}:{peer}',label,time.time()))
    changes = phone_label_changes(connection, plan, labels)
    for change in changes:
        connection.execute("INSERT OR REPLACE INTO contact_names(user_id,source,sender,name,updated_at) VALUES (?,'phone',?,?,?)", (user, change['sender'], change['name'], time.time()))
    return {'groups':len(plan['groups']), 'aliases_added':moved, 'unmatched':len(plan['unmatched']), 'labels_updated':len(changes)}
