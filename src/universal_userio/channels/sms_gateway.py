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
    def send(self, *, to: str, body: str, device_id: str = '', idempotency_key: str = '') -> str:
        payload={'to':to,'body':body}
        if device_id: payload['device_id']=device_id
        result=self._request('POST','/v1/messages',payload,idempotency_key=idempotency_key); receipt=result.get('id')
        if not isinstance(receipt,str) or not receipt: raise RuntimeError('Android SMS Gateway returned no accepted-message receipt')
        if result.get('status') not in {'accepted_by_android','queued_for_device'}: raise RuntimeError('Android SMS Gateway did not accept the message')
        return receipt
    def _request(self, method: str, path: str, payload: Mapping[str,str] | None=None, *, idempotency_key: str = '') -> dict[str,Any]:
        headers={'Authorization':f'Bearer {self._token}','Content-Type':'application/json'}
        if idempotency_key: headers['Idempotency-Key']=idempotency_key
        req=urllib.request.Request(self._url+path,data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode(),headers=headers,method=method)
        try:
            with self._runner(req,timeout=8) as response: status,raw=int(response.status),response.read()
        except urllib.error.HTTPError as error: raise RuntimeError(f'Android SMS Gateway returned HTTP {error.code}') from error
        except urllib.error.URLError as error: raise AdapterNotSupported(f'Android SMS Gateway is unavailable: {error.reason}') from error
        if status not in {200,202}: raise RuntimeError(f'Android SMS Gateway returned HTTP {status}')
        result=json.loads(raw)
        if not isinstance(result,dict): raise RuntimeError('Android SMS Gateway returned invalid JSON')
        return result
