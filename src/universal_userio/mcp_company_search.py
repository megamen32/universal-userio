"""Read-only company-knowledge adapter for permitted retained messages."""
from __future__ import annotations

import base64
import json
from typing import Any, Callable


def _encode(row: dict[str, Any]) -> str:
    data = [str(row.get(k) or '') for k in ('source', 'account_ref', 'conversation_id', 'message_id')]
    return base64.urlsafe_b64encode(json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()).decode().rstrip('=')


def _decode(value: Any) -> list[str]:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError('invalid result id')
    try:
        data = json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)))
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise ValueError('invalid result id') from exc
    if not isinstance(data, list) or len(data) != 4 or any(not isinstance(x, str) for x in data) or not all((data[0], data[2], data[3])):
        raise ValueError('invalid result id')
    return data


def dispatch(name: str, arguments: dict[str, Any], *, store: Any, principal: Any, call: Callable) -> dict[str, Any]:
    """Delegate authorization to the existing scoped search and apply auto-processing policy."""
    user_id = principal.user_id
    if name == 'search':
        query = arguments.get('query')
        if not isinstance(query, str) or not query.strip() or len(query) > 256:
            return {'ok': False, 'error': 'query is required'}
        result = call('userio.channels.search', {'query': query, 'scope': 'message', 'mode': 'text', 'limit': 100})
        if not result.get('ok'):
            return result
        output = []
        for row in result.get('results', []):
            try:
                allowed = store.evaluate_workspace_event(conversation_id=row['conversation_id'], source=row['source'], message_id=row['message_id'], user_id=user_id)['allowed']
            except (KeyError, ValueError):
                allowed = False
            if allowed:
                output.append({'id': _encode(row), 'title': str(row.get('display_name') or row.get('sender') or row['source']), 'url': row['deep_link']})
        return {'results': output}
    try:
        source, account, conversation, message = _decode(arguments.get('id'))
        matched = call('userio.channels.search', {'message_id': message, 'scope': 'message', 'account_ref': account, 'conversation_ids': [conversation]})
        rows = matched.get('results') or [] if matched.get('ok') else []
        row = next((r for r in rows if r.get('source') == source and r.get('conversation_id') == conversation and str(r.get('account_ref') or '') == account), None)
        if not row:
            return {'ok': False, 'error': 'not found'}
        with store._lock:
            if not store.evaluate_workspace_event(conversation_id=conversation, source=source, message_id=message, user_id=user_id)['allowed']:
                return {'ok': False, 'error': 'not found'}
            raw = store._connection.execute('SELECT body FROM messages WHERE user_id=? AND conversation_id=? AND source=? AND message_id=? AND deleted_at IS NULL', (user_id, conversation, source, message)).fetchone()
        if raw is None:
            return {'ok': False, 'error': 'not found'}
        return {'id': arguments['id'], 'title': str(row.get('display_name') or row.get('sender') or source), 'text': str(raw['body']), 'url': row['deep_link'], 'metadata': {'source': source, 'received_at': row.get('received_at')}}
    except (KeyError, TypeError, ValueError):
        return {'ok': False, 'error': 'not found'}
