"""Notice Place voice delivery over existing owner-bound call managers.

Only a closed, known unanswered call permits escalation. An HTTP interruption
does not own the native call; another request retrieves its durable receipt.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import unicodedata
import uuid


def _text(value, limit):
    value = ''.join(c for c in value if not unicodedata.category(c).startswith('C') or c.isspace())
    value = ' '.join(value.split())
    return value if len(value) <= limit else value[:limit - 1].rstrip() + '…'


def _request(payload):
    if not isinstance(payload, dict):
        raise ValueError('invalid Notice Place call')
    for key, limit, required in (('incident_id', 128, True), ('title', 1000, True),
                                 ('body', 12000, False), ('delivery_key', 128, False)):
        value = payload.get(key, '')
        if not isinstance(value, str) or len(value) > limit or required and not value.strip():
            raise ValueError('invalid Notice Place call')
    incident_id = payload['incident_id']
    delivery_key = payload.get('delivery_key', '')
    request_id = 'noticeplace:' + incident_id
    if delivery_key or not 16 <= len(request_id) <= 128:
        identity = json.dumps([incident_id, delivery_key], ensure_ascii=False)
        request_id = 'noticeplace:' + hashlib.sha256(identity.encode()).hexdigest()
    title, body = _text(payload['title'], 180), _text(payload.get('body', ''), 560)
    greeting = ('Здравствуйте. Это ИИ-помощник Notice Place. Сообщаю об инциденте: '
                + title + '. Текущая ситуация: ' + (body or 'требуется проверка сервиса')
                + '. Пожалуйста, проверьте сервис и выполните указанное действие. '
                'Я могу пояснить уведомление.')
    purpose = _text('Инцидент: ' + title + '. Текущая ситуация: ' + (body or 'проверьте сервис'), 500)
    return request_id, purpose, _text(greeting, 1000)


def noticeplace_call(service, user_id, payload, *, channel='matrix'):
    request_id, purpose, greeting = _request(payload)
    if channel == 'matrix':
        from .matrix_calls import calls_for
        destination = dict(account_ref=os.environ['USERIO_MATRIX_NOTICE_ACCOUNT'],
                           room_id=os.environ['USERIO_MATRIX_NOTICE_ROOM'],
                           target=os.environ['USERIO_MATRIX_NOTICE_TARGET'])
    elif channel == 'telegram':
        from .telegram_calls import calls_for
        destination = dict(account_ref=os.environ['USERIO_TELEGRAM_NOTICE_ACCOUNT'],
                           target=os.environ['USERIO_TELEGRAM_NOTICE_TARGET'])
    else:
        raise ValueError('unsupported Notice Place call channel')
    calls = calls_for(service)
    identity = user_id + '\0' + request_id if channel == 'matrix' else 'userio:telegram:' + user_id + ':' + request_id
    call_id = str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
    try:
        call = calls.get(call_id, user_id)
    except KeyError:
        call = calls.start(user_id=user_id, **destination, purpose=purpose, greeting=greeting,
                           request_id=request_id, max_seconds=120, confirm=True,
                           agent_context={'incident_id':payload['incident_id'],
                               'event_type':'noticeplace.call','title':_text(payload['title'],512),
                               'body':_text(payload.get('body',''),1200)})
    else:
        if any(call.get(key) != value for key, value in destination.items()):
            raise ValueError('Notice Place call destination changed')
    # Telegram allows120s ringing +60s connection before the talk budget starts.
    # Cover its max_seconds+340 deadline and70s teardown; Matrix keeps its bound.
    # Stop waiting at a finite bound, retaining uncertainty rather than redialling.
    deadline = time.monotonic() + (535 if channel == 'telegram' else 435)
    while True:
        ended = call['call_id'] not in calls.active and call['status'] in {'completed', 'unanswered'}
        result = dict(call_id=call['call_id'], target=call['target'], channel=channel,
                      status=call['status'], ended=ended,
                      acknowledged=call.get('acknowledged') is True, safe_to_fallback=False)
        if call.get('answered') is True:
            return dict(result, ok=True, answered=True)
        if ended and call['status'] == 'unanswered' and call.get('relay_reconciliation') not in {'pending', 'unavailable'}:
            return dict(result, ok=True, answered=False, safe_to_fallback=True)
        if call['status'] == 'unknown' or call['call_id'] not in calls.active or time.monotonic() >= deadline:
            return dict(result, ok=False, answered=None, uncertain=True, error='call_outcome_unknown')
        time.sleep(.25)
        call = calls.get(call['call_id'], user_id)
