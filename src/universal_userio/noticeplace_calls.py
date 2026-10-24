"""Notice Place voice delivery over existing owner-bound call managers.

Only a closed, known unanswered call permits escalation. An HTTP interruption
does not own the native call; another request retrieves its durable receipt.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
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



def _binding(value):
    fields = {'account_ref', 'room_id', 'target', 'participants'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('invalid Notice Place destination binding')
    for key, prefix in (('account_ref','matrix:'), ('room_id','!'), ('target','@')):
        item=value[key]
        if not isinstance(item,str) or not item.startswith(prefix) or len(item)>512 or any(c.isspace() for c in item):
            raise ValueError('invalid Notice Place destination binding')
    participants=value['participants']
    if (not isinstance(participants,list) or len(participants)!=2 or
            any(not isinstance(v,str) or not re.fullmatch(r'@[^\s:]+:[^\s]+',v) for v in participants) or
            len(set(participants))!=2 or value['target'] not in participants):
        raise ValueError('invalid Notice Place participants')
    return dict(value,participants=sorted(participants))


def _policy_destination(payload, actor_id, *, machine_authorized):
    binding=_binding(payload['destination'])
    name=os.environ.get('USERIO_MATRIX_NOTICE_ROUTES_FILE','/etc/userio-containers/noticeplace-matrix-routes.json')
    if not name:raise PermissionError('Notice Place policy route is not authorized')
    try:
        path=Path(name);metadata=path.stat()
        if metadata.st_uid not in {0,os.geteuid()} or metadata.st_mode&0o077 or metadata.st_size>65536:
            raise ValueError('private server route configuration required')
        config=json.loads(path.read_text())
        routes=config['routes']
        if not isinstance(routes,list) or not 1<=len(routes)<=16:raise ValueError('invalid routes')
        matches=[]
        for route in routes:
            if not isinstance(route,dict) or set(route)!={'user_id',*binding.keys()}:raise ValueError('invalid route')
            user_id=route['user_id']
            if not isinstance(user_id,str) or not user_id or len(user_id)>128:raise ValueError('invalid route owner')
            if _binding({k:route[k] for k in binding})==binding:matches.append(user_id)
    except (OSError,ValueError,KeyError,TypeError):
        raise PermissionError('Notice Place policy route is not authorized') from None
    if len(matches)!=1 or not machine_authorized and matches[0]!=actor_id:
        raise PermissionError('Notice Place policy route is not authorized')
    return binding,matches[0]


def noticeplace_call(service, user_id, payload, *, channel='matrix', machine_authorized=False):
    request_id, purpose, greeting = _request(payload)
    actor_id=user_id
    binding=None
    if 'destination' in payload:
        if channel!='matrix':raise ValueError('policy destination requires Matrix')
        binding,user_id=_policy_destination(payload,actor_id,machine_authorized=machine_authorized)
        if not service._store.capability_enabled('send',user_id=user_id):
            raise PermissionError('send capability disabled for configured account owner')
    if channel == 'matrix':
        from .matrix_calls import calls_for, _NOTICEPLACE_SCOPE_AUTHORITY
        destination = ({k:binding[k] for k in ('account_ref','room_id','target')} if binding else
                       dict(account_ref=os.environ['USERIO_MATRIX_NOTICE_ACCOUNT'],
                            room_id=os.environ['USERIO_MATRIX_NOTICE_ROOM'],
                            target=os.environ['USERIO_MATRIX_NOTICE_TARGET']))
    elif channel == 'telegram':
        from .telegram_calls import calls_for
        destination = dict(account_ref=os.environ['USERIO_TELEGRAM_NOTICE_ACCOUNT'],
                           target=os.environ['USERIO_TELEGRAM_NOTICE_TARGET'])
    else:
        raise ValueError('unsupported Notice Place call channel')
    calls = calls_for(service)
    identity = actor_id + '\0' + request_id if channel == 'matrix' else 'userio:telegram:' + user_id + ':' + request_id
    call_id = str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
    try:
        call = calls.get(call_id, user_id)
    except KeyError:
        scoped_options=({'participants':binding['participants'],'request_scope':actor_id,
                         '_scope_authority':_NOTICEPLACE_SCOPE_AUTHORITY} if binding else {})
        call = calls.start(user_id=user_id, **scoped_options, **destination, purpose=purpose, greeting=greeting,
                           request_id=request_id, max_seconds=120, confirm=True,
                           agent_context=({'incident_id':payload['incident_id'],
                               'event_type':'noticeplace.call','title':_text(payload['title'],512),
                               'body':_text(payload.get('body',''),1200)}
                               if not binding or user_id==service._store.default_user_id else None))
    else:
        frozen=dict(destination,**({'participants':binding['participants']} if binding else {}))
        if any(call.get(key) != value for key, value in frozen.items()):
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
        if binding:
            result.update(account_ref=call['account_ref'],room_id=call['room_id'],destination=binding,
                          request_id=request_id,actor=call['target'])
        if call.get('answered') is True:
            return dict(result, ok=True, answered=True)
        if ended and call['status'] == 'unanswered' and call.get('relay_reconciliation') not in {'pending', 'unavailable'}:
            return dict(result, ok=True, answered=False, safe_to_fallback=True)
        if call['status'] == 'unknown' or call['call_id'] not in calls.active or time.monotonic() >= deadline:
            return dict(result, ok=False, answered=None, uncertain=True, error='call_outcome_unknown')
        time.sleep(.25)
        call = calls.get(call['call_id'], user_id)
