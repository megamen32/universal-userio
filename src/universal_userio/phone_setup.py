"""Connect an APK phone to its authenticated UserIO owner."""
from __future__ import annotations

import hashlib
import re
from typing import Any

_DEVICE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")


def phone_device_alias(user_id: str, device_id: str) -> str:
    if not _DEVICE_ID.fullmatch(device_id):
        raise ValueError("Введите ID устройства из приложения, например android-1234abcd")
    return f"user-{hashlib.sha256(user_id.encode()).hexdigest()[:16]}-{device_id}"


def connect_phone(service: Any, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    device_id = str(payload.get('device_id') or '').strip()
    alias = phone_device_alias(user_id, device_id)
    account_id = 'sms:' + alias
    label = str(payload.get('display_name') or 'Мой Android').strip()[:80]
    if not label:
        raise ValueError('Укажите название телефона')
    if service.sms_gateway is None:
        raise ValueError('Подключение Android временно недоступно')
    # The gateway issues a phone-only credential, never a UserIO/operator token.
    issued = service.sms_gateway.enroll(user_id=user_id, device_id=device_id, account_id=account_id)
    if issued.get('gateway_device_id') != alias or issued.get('account_id') != account_id:
        raise ValueError('Шлюз вернул другую привязку телефона')
    service._store.register_account(account_id=account_id, provider='sms', display_name='SMS · '+label,
        can_read=True, can_reply=True, credential_ref='sms-agent:'+alias, enabled=True, user_id=user_id)
    service._store.bind_channel_route(user_id=user_id, source='sms', route_id='sms')
    return {'account_id':account_id, 'device_id':device_id, 'device_token':issued['device_token'],
            'token_returned_once':True, 'display_name':'SMS · '+label}


def phone_binding(service: Any, user_id: str, account_id: str) -> dict[str, bool]:
    account = next((a for a in service._store.accounts(user_id=user_id) if a['id']==account_id
                    and a['provider']=='sms' and a['enabled']
                    and str(a.get('credential_ref') or '').startswith('sms-agent:user-')), None)
    return {'registered':account is not None,
            'can_read':bool(account and 'read' in account['capabilities'] and service._store.capability_enabled('read',user_id=user_id)),
            'can_reply':bool(account and 'reply' in account['capabilities'] and service._store.capability_enabled('send',user_id=user_id))}
