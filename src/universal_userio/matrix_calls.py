"""User-owned MatrixRTC voice calls, with durable admission and separate audio tracks.

The installed AgentCall speech/recording seam is reused without changing its
runtime. LiveKit is optional: importing UserIO never loads its native library.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
import stat
import threading
import time
from urllib.parse import quote
import urllib.request
import uuid

from .account_connections import ConnectorSecrets, _NoRedirect
from .contracts import InboxMessage

ROOT = Path('/var/lib/universal-userio/matrix-calls')
MEMBER = 'org.matrix.msc3401.call.member'
RING = 'org.matrix.msc4075.rtc.notification'
# In-process authority; HTTP/MCP JSON cannot supply a server admission namespace.
_NOTICEPLACE_SCOPE_AUTHORITY = object()
CONTROL_CONFIG = Path('/etc/userio-containers/matrix-call-control.json')


def control_route(*, required=False):
    """Resolve private primary IPC; invalid configured routes never use local voice."""
    try:
        metadata=CONTROL_CONFIG.stat()
    except FileNotFoundError:
        if not required:return None
        raise PermissionError('primary Matrix voice control is not configured') from None
    except OSError:
        raise PermissionError('primary Matrix voice control is not authorized') from None
    try:
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid not in {0,os.geteuid()}
                or metadata.st_mode&0o077 or metadata.st_size>4096):
            raise ValueError('private control configuration required')
        value=json.loads(CONTROL_CONFIG.read_text())
        if (not isinstance(value,dict) or set(value)!={'socket_path','execution_host'}
                or value['execution_host']!='roomhacker-server-88'
                or value['socket_path']!='/run/agentcall/primary88/control.sock'):
            raise ValueError('primary88 private control route required')
        return value
    except (OSError,ValueError,TypeError):
        raise PermissionError('primary Matrix voice control is not authorized') from None



def settle_disconnected_call(record):
    if record.get('status') in {'accepted', 'ringing', 'active'}:
        if record.get('stop_requested') is True and record.get('answered') is True and not record.get('error'):
            record['status'] = 'completed'
            return record
        raise RuntimeError('voice transport outcome unknown; do not replay')
    return record


def request_json(url, token='', payload=None, method='GET'):
    request = urllib.request.Request(url, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Content-Type': 'application/json', **({'Authorization': 'Bearer '+token} if token else {})})
    with urllib.request.build_opener(_NoRedirect).open(request, timeout=12) as response:
        data = response.read(2*1024*1024+1)
    if len(data)>2*1024*1024: raise ValueError('Matrix response exceeds limit')
    return json.loads(data)


def fresh_answer(events, target, started, prior, rtc_url):
    return any(e.get('type') == MEMBER and e.get('sender') == target
        and e.get('event_id') not in prior and e.get('origin_server_ts', 0) >= started
        and e.get('content', {}).get('application') == 'm.call'
        and e.get('content', {}).get('expires', 0) > 0
        and any(f.get('type')=='livekit' and f.get('livekit_service_url','').rstrip('/')==rtc_url.rstrip('/')
                for f in e.get('content',{}).get('foci_preferred',[]) if isinstance(f,dict)) for e in events)


class MatrixCalls:
    def __init__(self, service, root=None):
        self.service = service
        self.root = Path(root or ROOT)
        self.lock = threading.Lock()
        self.active = {}

    def get(self, call_id, user_id):
        call_id = str(uuid.UUID(call_id))
        try: record = json.loads((self.root/call_id/'call.json').read_text())
        except FileNotFoundError: raise KeyError('call not found') from None
        if record['user_id'] != user_id: raise KeyError('call not found')
        if record['status'] in {'accepted','ringing','active'} and call_id not in self.active:
            record['status'] = 'unknown'
        return record

    def stop(self, call_id, user_id):
        record = self.get(call_id, user_id)
        with self.lock:
            stop = self.active.get(record['call_id'])
            if stop:
                record['stop_requested'] = True
                self._save(record)
                stop.set()
        return record

    def start(self, *, user_id, account_ref, room_id, target, purpose, request_id,
              greeting='', max_seconds=120, confirm=False, agent_context=None,
              participants=None, request_scope=None, _scope_authority=None):
        if request_scope is not None and _scope_authority is not _NOTICEPLACE_SCOPE_AUTHORITY:
            raise PermissionError('call request scope is server-controlled')
        route=control_route(required=request_scope is not None)
        if confirm is not True: raise ValueError('explicit call confirmation required')
        if not self.service._store.capability_enabled('send', user_id=user_id):
            raise PermissionError('send capability disabled')
        if user_id != self.service._store.default_user_id and agent_context:
            raise PermissionError('Matrix voice context is server-controlled')
        if not (room_id.startswith('!') and target.startswith('@') and purpose.strip()):
            raise ValueError('Matrix room, recipient and call purpose required')
        if not (16 <= len(request_id) <= 128 and len(purpose) <= 12000 and len(greeting) <= 1000
                and type(max_seconds) is int and 30 <= max_seconds <= 600):
            raise ValueError('invalid call bounds')
        account = next((a for a in self.service._store.accounts(user_id=user_id)
                        if a['id'] == account_ref and a['provider'] == 'matrix' and a['enabled']), None)
        if not account: raise KeyError('Matrix account not found')
        secret = ConnectorSecrets().load(account['credential_ref'], user_id=user_id, provider='matrix')
        identity = request_json(secret['homeserver']+'/_matrix/client/v3/account/whoami', secret['access_token'])
        if identity.get('user_id') != secret['matrix_user_id']: raise ValueError('Matrix identity changed')
        if participants is not None:
            if (not isinstance(participants,list) or len(participants)!=2 or len(set(participants))!=2
                    or set(participants)!={identity.get('user_id'),target}):
                raise ValueError('Matrix policy participants mismatch')
            members=request_json(secret['homeserver']+'/_matrix/client/v3/rooms/'+quote(room_id,safe='')+'/joined_members',secret['access_token'])
            if not isinstance(members.get('joined'),dict) or set(members['joined'])!=set(participants):
                raise PermissionError('Matrix policy room membership mismatch')
        display_account = next((a['id'] for a in self.service._store.accounts(user_id=user_id)
                                if a['provider']=='matrix' and a['enabled'] and a['display_name']==target), account_ref)
        ai_settings = self.service._store.ai_settings(user_id=user_id)
        effective_agent_context = (
            {'call_mode':'incoming_guest'} if user_id != self.service._store.default_user_id
            else dict(agent_context) if agent_context is not None
            else {'call_mode':'incoming'}
        )
        config = {'user_id': user_id, 'account_ref': account_ref, 'history_account_ref':display_account, 'room_id': room_id,
                  'target': target, 'purpose': purpose, 'greeting': greeting, 'max_seconds': max_seconds,
                  **({'voice_model': ai_settings['model']} if isinstance(ai_settings, dict) and ai_settings.get('model') else {}),
                  **({'agent_context':effective_agent_context} if effective_agent_context else {})}
        if route is not None:config['control_route']=route
        if participants is not None:config['participants']=sorted(participants)
        if request_scope is not None:
            if participants is None or not isinstance(request_scope,str) or not request_scope:
                raise ValueError('invalid server call request scope')
            config['request_scope']=request_scope
        call_id = str(uuid.uuid5(uuid.NAMESPACE_URL, (request_scope or user_id)+'\0'+request_id))
        fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        # Same request after a restart returns its receipt; it never rings twice.
        with self.lock:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            folder = self.root/call_id
            if (folder/'call.json').exists():
                try:
                    record = self.get(call_id, user_id)
                except KeyError:
                    raise ValueError('call request conflict') from None
                if record['fingerprint'] != fingerprint: raise ValueError('call request conflict')
                return record
            if self.active: raise ValueError('another Matrix voice call is active')
            folder.mkdir(mode=0o700)
            record = dict(config, call_id=call_id, fingerprint=fingerprint, status='accepted',
                          answered=False, acknowledged=False, received_audio_bytes=0, created_at=time.time())
            self._save(record)
            stop = threading.Event()
            self.active[call_id] = stop
            voice_ai = ({'endpoint': ai_settings['endpoint'], 'model': ai_settings['model'],
                         'token': ai_settings['token']} if isinstance(ai_settings, dict) else None)
            threading.Thread(target=self._run, args=(record, secret, identity, stop, voice_ai), daemon=True).start()
        return record.copy()

    def _save(self, record):
        folder = self.root/record['call_id']
        temporary = folder/('.call-'+uuid.uuid4().hex)
        with temporary.open('x') as output:
            os.chmod(temporary, 0o600)
            json.dump(record, output, ensure_ascii=False); output.flush(); os.fsync(output.fileno())
        temporary.replace(folder/'call.json')

    def _emit(self, record, body, direction='system', suffix='status'):
        label = next((a['display_name'] for a in self.service._store.accounts(user_id=record['user_id'])
                      if a['id']==record['account_ref']), record['target'])
        return self.service.receive(InboxMessage(
            record['history_account_ref'], 'matrixcall-'+record['call_id']+'-'+suffix,
            record.get('voice_call_id',record['call_id']), body, time.time(), peer_id=record['room_id'],
            sender_name=label, conversation_kind='direct', direction=direction, reconciliation=True),
            route_id='matrix-read-only', user_id=record['user_id'], account_ref=record['history_account_ref'])

    def _run(self, record, secret, identity, stop, voice_ai=None):
        try: asyncio.run(self._call(record, secret, identity, stop, voice_ai))
        except Exception as error:
            # Provider responses may contain credentials; retain only the error class.
            record.update(status='failed', error=record.get('error') or type(error).__name__)
            self._emit(record, 'Не удалось завершить Matrix-звонок. Запись сохранена, если звук поступал.', suffix='error')
        finally:
            self._save(record)
            with self.lock: self.active.pop(record['call_id'], None)

    async def _call(self, record, secret, identity, stop, voice_ai=None):
        # The existing voice service launches the fixed worker in its own
        # finite budget and effective environment. No voice settings live here.
        reader, writer = await asyncio.open_unix_connection(record.get('control_route',{}).get('socket_path','/run/agentcall/control.sock'), limit=65536)
        request = {'action':'matrix_transport','call':record,'account':secret,'identity':identity,
                   'rtc_auth_url':os.environ['USERIO_MATRIX_RTC_AUTH_URL']}
        if voice_ai is not None:
            request['voice_ai'] = voice_ai
        writer.write(json.dumps(request,ensure_ascii=False).encode()+b'\n')
        await writer.drain()
        try:
            transcripts = {'caller':[], 'assistant':[]}
            async def consume():
                while True:
                    line = await reader.readline()
                    if not line: break
                    event = json.loads(line)
                    if event.get('ok') is False: raise RuntimeError('voice service refused Matrix transport')
                    if event.get('callId'): record['voice_call_id'] = str(uuid.UUID(event['callId']))
                    kind = event.get('event')
                    if kind in {'ringing','active'}:
                        record['status'] = kind
                        record['answered'] = kind == 'active' or record['answered']
                        self._emit(record,'ИИ-звонок через Matrix: '+('разговор начат' if kind=='active' else 'ожидаю ответа'),suffix=kind)
                    elif kind == 'transcript_final':
                        remote = event.get('speaker') == 'remote'
                        text = str(event.get('text') or '').strip()
                        if text:
                            if remote and any(phrase in text.lower() for phrase in ('я услышал','да, слышу','да слышу','я тебя слышу','я вас слышу','подтверждаю','принял уведомление')):
                                record['acknowledged'] = True
                            transcripts['caller' if remote else 'assistant'].append(text)
                            self._emit(record,('Собеседник: ' if remote else 'ИИ: ')+text,
                                'incoming' if remote else 'outgoing',suffix='turn-'+str(sum(map(len,transcripts.values()))))
                    elif kind == 'acknowledged': record['acknowledged'] = True
                    elif kind == 'ended':
                        record['status'] = 'completed' if record['answered'] else 'unanswered'
                    elif kind == 'error':
                        reason = str(event.get('reason') or '')
                        record['warning' if event.get('recoverable') is True else 'error'] = reason if reason.isidentifier() and len(reason)<80 else 'voice_transport_error'
                    self._save(record)
            consumer = asyncio.create_task(consume())
            deadline = time.monotonic()+record['max_seconds']+240
            while not consumer.done() and not stop.is_set() and time.monotonic()<deadline:
                await asyncio.sleep(.25)
            if not consumer.done():
                writer.close(); await writer.wait_closed()
                try: await asyncio.wait_for(consumer,timeout=70)
                except asyncio.TimeoutError: consumer.cancel(); await asyncio.gather(consumer,return_exceptions=True)
            else: await consumer
            settle_disconnected_call(record)
            if record.get('control_route'):
                # Media remains on the execution host; never infer remote audio from a local archive.
                record['recording_host']=record['control_route']['execution_host']
            elif record.get('voice_call_id'):
                from .call_audio import recorded_track_bytes
                from .call_audio import ARCHIVE
                folder = ARCHIVE/record['voice_call_id']
                record['received_audio_bytes'] = recorded_track_bytes(record['voice_call_id'], 'caller')
                path = folder/'live-transcript.json'
                if folder.is_dir():
                    path.write_text(json.dumps({k:'\n'.join(v) for k,v in transcripts.items()},ensure_ascii=False))
                    os.chmod(path,0o600)
            self._emit(record,'Звонок завершён через Matrix',suffix='ended')
        finally:
            writer.close()
            await writer.wait_closed()



def calls_for(service):
    # One manager per service, independent of HTTP/MCP request lifetime.
    with service._manual_send_lock:
        manager = getattr(service,'_matrix_calls',None)
        if manager is None:
            manager = service._matrix_calls = MatrixCalls(service)
    return manager


def noticeplace_call(service, user_id, payload):
    from .noticeplace_calls import noticeplace_call as deliver
    return deliver(service, user_id, payload, channel='matrix')
