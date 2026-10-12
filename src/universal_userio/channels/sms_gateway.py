"""Low-level Android SMS Gateway client with no UserIO runtime dependencies."""
from __future__ import annotations
import json
import urllib.error
import urllib.request
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from userio_adapter_sdk import AdapterNotSupported

@dataclass(frozen=True, slots=True)
class SmsInboundMessage:
    message_id: str
    sender: str
    body: str
    received_at: float
    device_id: str = ''
    direction: str = 'incoming'
    provider_read: bool | None = None
    outbox_receipt: str = ''


def sms_address(value: str) -> str:
    """Keep short codes and sender names; normalize Russian phone formatting."""
    value = value.strip()
    digits = ''.join(c for c in value if c.isdigit())
    if len(digits) == 11 and digits[0] in '78' and all(c.isdigit() or c in '+ ()-' for c in value):
        return '+7' + digits[1:]
    return value

class AndroidSmsGatewayClient:
    def __init__(self, url: str, token: str, *, runner: Any = urllib.request.urlopen) -> None:
        self._url, self._token, self._runner = url.rstrip('/'), token, runner
    def inbound(self, *, device_id: str = '') -> list[SmsInboundMessage]:
        result=self._request('GET','/v1/inbound'+('?device_id='+urllib.parse.quote(device_id,safe='') if device_id else '')); messages=result.get('messages')
        if not isinstance(messages,list): raise RuntimeError('Android SMS Gateway returned invalid inbound messages')
        out=[]
        for item in messages:
            if not isinstance(item,Mapping): continue
            mid,sender,body,received=item.get('id'),item.get('from'),item.get('body'),item.get('receivedAt')
            if not all(isinstance(v,str) and v.strip() for v in (mid,sender,body,received)): continue
            try: ts=datetime.fromisoformat(received.replace('Z','+00:00')).timestamp()
            except ValueError: continue
            row_device=str(item.get('device_id') or '')
            if device_id and row_device != device_id: continue
            out.append(SmsInboundMessage(mid,sender,body,ts,row_device))
        return out
    def history(self, *, after_id: int = 0, snapshot_id: int = 0, limit: int = 100,
                pending_ids: list[int] | None = None) -> dict[str, Any]:
        pending_ids = pending_ids or []
        if len(pending_ids)>100 or any(type(i) is not int or i<=0 for i in pending_ids):
            raise ValueError('Invalid pending native SMS IDs')
        params={'after_id':after_id,'snapshot_id':snapshot_id,'limit':limit}
        if pending_ids:
            params['pending_ids']=','.join(map(str,pending_ids))
        query=urllib.parse.urlencode(params)
        # Snapshot/page/pending recheck share one bounded native request.
        result=self._request('GET','/v1/history?'+query,timeout=65)
        pending=result.get('pending_ids',[])
        if not isinstance(pending,list) or len(pending)>100 or any(type(i) is not int or i<=0 for i in pending):
            raise RuntimeError('Invalid pending native SMS response')
        result['pending_ids']=pending
        messages=[]
        for item in result.get('messages',[]):
            if item.get('direction') not in ('incoming','outgoing'):
                continue  # Failed/outbox drafts are not delivered conversations.
            mid=str(item.get('id') or '')
            address=sms_address(str(item.get('contact_number') or ''))
            body=item.get('body')
            if not mid.startswith('sms-native:') or not address or not isinstance(body,str):
                raise RuntimeError('Invalid native SMS history identity')
            received=datetime.fromisoformat(str(item['receivedAt']).replace('Z','+00:00')).timestamp()
            messages.append(SmsInboundMessage(mid,address,body,received,direction=item['direction'],provider_read=item.get('provider_read'),outbox_receipt=str(item.get('receipt_id') or '')))
        result['messages']=messages
        return result
    def send(self, *, to: str, body: str, device_id: str = '', idempotency_key: str = '') -> str:
        payload={'to':to,'body':body}
        if device_id: payload['device_id']=device_id
        result=self._request('POST','/v1/messages',payload,idempotency_key=idempotency_key); receipt=result.get('id')
        if not isinstance(receipt,str) or not receipt: raise RuntimeError('Android SMS Gateway returned no accepted-message receipt')
        if result.get('status') not in {'accepted_by_android','queued_for_device'}: raise RuntimeError('Android SMS Gateway did not accept the message')
        return receipt
    def enroll(self, *, user_id: str, device_id: str, account_id: str) -> dict[str, Any]:
        return self._request('POST','/v1/agent/enroll',{'user_id':user_id,'device_id':device_id,'account_id':account_id},success_statuses=(200,201,202))
    def _request(self, method: str, path: str, payload: Mapping[str,str] | None=None, *, idempotency_key: str = '', timeout: float = 8, success_statuses: tuple[int,...] = (200,202)) -> dict[str,Any]:
        headers={'Authorization':f'Bearer {self._token}','Content-Type':'application/json'}
        if idempotency_key: headers['Idempotency-Key']=idempotency_key
        req=urllib.request.Request(self._url+path,data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode(),headers=headers,method=method)
        try:
            with self._runner(req,timeout=timeout) as response: status,raw=int(response.status),response.read()
        except urllib.error.HTTPError as error: raise RuntimeError(f'Android SMS Gateway returned HTTP {error.code}') from error
        except urllib.error.URLError as error: raise AdapterNotSupported(f'Android SMS Gateway is unavailable: {error.reason}') from error
        if status not in success_statuses: raise RuntimeError(f'Android SMS Gateway returned HTTP {status}')
        result=json.loads(raw)
        if not isinstance(result,dict): raise RuntimeError('Android SMS Gateway returned invalid JSON')
        return result
