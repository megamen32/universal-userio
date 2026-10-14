"""Per-token account and chat grants; enforced before any provider operation."""
from __future__ import annotations
import json

READ_TOOLS = {'capabilities', 'history', 'get', 'resolve', 'media', 'events'}


def validate_grants(store, user_id, grants):
    if not isinstance(grants, list) or not 1 <= len(grants) <= 64:
        raise ValueError('Выберите хотя бы один аккаунт (не более 64).')
    owned = {a['id']: a for a in store.accounts(user_id=user_id)}
    result, seen = [], set()
    for grant in grants:
        if not isinstance(grant, dict) or set(grant) != {'account_ref', 'access', 'chats'}:
            raise ValueError('Неверный формат прав аккаунта.')
        ref, access, chats = grant['account_ref'], grant['access'], grant['chats']
        if not isinstance(ref, str) or ref in seen or ref not in owned or not owned[ref]['enabled']:
            raise ValueError('Аккаунт должен быть подключён к вашему UserIO и выбран один раз.')
        if access not in {'read_only', 'read_write'}:
            raise ValueError('Выберите чтение либо чтение и запись.')
        if chats is not None:
            if not isinstance(chats, list) or not 1 <= len(chats) <= 256:
                raise ValueError('Выберите чаты либо разрешите все чаты аккаунта.')
            chat_ids = set()
            for chat in chats:
                if not isinstance(chat, dict) or set(chat) != {'conversation_id', 'access'}:
                    raise ValueError('Неверный формат прав чата.')
                cid = chat['conversation_id']
                if not isinstance(cid, str) or cid in chat_ids:
                    raise ValueError('Чат должен быть выбран один раз.')
                conv = store.conversation(cid, user_id=user_id)
                if not conv or conv['account_ref'] != ref:
                    raise ValueError('Чат не принадлежит выбранному аккаунту.')
                if chat['access'] not in {'read_only','read_write'} or access == 'read_only' and chat['access'] != 'read_only':
                    raise ValueError('Права чата не могут превышать права аккаунта.')
                chat_ids.add(cid)
        seen.add(ref)
        result.append({'account_ref':ref, 'access':access, 'chats':chats})
    return result


def grant_for(principal, account_ref, conversation_id=None):
    for grant in principal.messenger_grants or ():
        if grant['account_ref'] == account_ref:
            if conversation_id is None or grant['chats'] is None:
                return grant['access']
            return next((c['access'] for c in grant['chats'] if c['conversation_id'] == conversation_id), None)
    return None


def enforce(store, principal, name, args):
    if principal.messenger_grants is None:
        return
    if name in {'tools/list','tools/call','userio.accounts.list'}:
        return
    if not name.startswith('userio.messenger.'):
        raise PermissionError('scoped_messenger_only')
    op = name.removeprefix('userio.messenger.')
    ref, cid = args.get('account_ref'), args.get('conversation_id')
    if op in {'get','approve'}:
        with store._lock:
            if not store._table_exists('messenger_actions'):
                raise KeyError('action_not_found')
            row = store._connection.execute('SELECT envelope FROM messenger_actions WHERE user_id=? AND id=?',
                (principal.user_id,args.get('action_id'))).fetchone()
        if row is None:
            raise KeyError('action_not_found')
        envelope = json.loads(row['envelope'])
        ref, cid = envelope['account_ref'], envelope['conversation_id']
    elif op == 'resolve':
        with store._lock:
            row = store._connection.execute('SELECT id FROM conversations WHERE user_id=? AND account_ref=? AND peer_id=?',
                (principal.user_id,ref,str(args.get('peer_id','')))).fetchone()
        cid = row['id'] if row else '__unknown_chat__'
    access = grant_for(principal, ref, cid)
    if access is None:
        raise PermissionError('chat_or_account_outside_token_scope')
    if op not in READ_TOOLS and access != 'read_write':
        raise PermissionError('chat_read_only_token')
    # Operations bound to a message must always supply its chat; get/approve use
    # the durable action envelope. Account capabilities/events are filtered below.
    if op not in {'capabilities','events'} and not cid:
        raise PermissionError('exact_chat_required')


def dispatch_scoped(store, service, principal, name, arguments):
    from .messenger import Messenger, dispatch
    enforce(store, principal, name, arguments)
    op = name.removeprefix('userio.messenger.')
    if op != 'events':
        result = dispatch(store,service,principal,name,arguments)
        if op == 'capabilities' and result.get('ok') and grant_for(principal, arguments.get('account_ref')) == 'read_only':
            for operation, value in result['operations'].items():
                if operation not in {'read','history','download'}:
                    value.update(allowed=False, reason='chat_read_only_token')
        return result
    api = Messenger(store,service,principal)
    ref, after, limit = arguments['account_ref'], arguments.get('after'), arguments.get('limit',50)
    api._allow(ref,'read')
    if after is not None and (type(after) is not int or not 0 <= after < 2**63):
        raise ValueError('invalid_event_cursor')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('limit_must_be_1_to_100')
    grant = next(g for g in principal.messenger_grants if g['account_ref']==ref)
    chats = None if grant['chats'] is None else [c['conversation_id'] for c in grant['chats']]
    clause = '' if chats is None else ' AND e.conversation_id IN ('+','.join('?' for _ in chats)+')'
    params = [principal.user_id,ref,*(chats or [])]
    with store._lock:
        head = store._connection.execute('''SELECT COALESCE(MAX(e.seq),0) FROM workspace_events e
            JOIN conversations c ON c.user_id=e.user_id AND c.id=e.conversation_id
            WHERE e.user_id=? AND c.account_ref=?'''+clause,params).fetchone()[0]
        rows = [] if after is None else store._connection.execute('''SELECT e.seq,e.eligible,e.message_id,e.conversation_id
            FROM workspace_events e JOIN conversations c ON c.user_id=e.user_id AND c.id=e.conversation_id
            JOIN messages m ON m.user_id=e.user_id AND m.source=e.source AND m.message_id=e.message_id
            WHERE e.user_id=? AND c.account_ref=?'''+clause+''' AND e.seq>? AND e.reconciled=0
            AND m.direction='incoming' AND m.deleted_at IS NULL ORDER BY e.seq LIMIT ?''',[*params,after,limit+1]).fetchall()
    events = [{'seq':r['seq'], 'analysis_eligible':bool(r['eligible']),
        'message':api._project(api._conversation(ref,r['conversation_id']),api._message(api._conversation(ref,r['conversation_id']),r['message_id']))}
        for r in rows[:limit]]
    more = len(rows)>limit
    return {'ok':True,'schema':'userio.messenger.v1','account_ref':ref,'events':events,'head':head,
        'next_cursor':events[-1]['seq'] if more else max(head,after or 0),'has_more':more,
        'initialized':after is None,'acknowledged':False}
