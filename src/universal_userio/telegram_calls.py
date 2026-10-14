"""Durable UserIO Telegram calls through the existing Secretary relay client."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import quote
import uuid

from .contracts import InboxMessage

ROOT = Path('/var/lib/universal-userio/telegram-calls')
PREFIX = '/userio/telegram/calls/'


def record_call_error(record, event):
    if event.get('recoverable') is True:
        record['warning'] = str(event.get('reason') or 'voice_recognition_incomplete')[:80]
    else:
        record['error'] = str(event.get('reason') or 'voice_transport_error')[:80]
        if record['status'] != 'unknown':
            record['status'] = 'failed'


def relay_request_id(worker_id):
    # Existing relay ledger requires8..128 safe characters; preserve the local
    # short counter separately for the worker's pending Future.
    if not isinstance(worker_id,str) or not re.fullmatch(r'[1-9][0-9]{0,18}',worker_id):
        raise ValueError('invalid worker request counter')
    return 'rpc-'+worker_id.zfill(8)


class TelegramCalls:
    def __init__(self, service, root=None):
        self.service, self.root = service, Path(root or ROOT)
        self.lock = threading.Lock(); self.active = {}

    def _save(self, record):
        folder = self.root/record['call_id']; folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = folder/('.call-'+uuid.uuid4().hex)
        with temporary.open('x') as output:
            os.chmod(temporary,0o600); json.dump(record,output,ensure_ascii=False)
            output.flush(); os.fsync(output.fileno())
        os.replace(temporary,folder/'call.json')

    def get(self, call_id, user_id):
        call_id=str(uuid.UUID(call_id))
        try: record=json.loads((self.root/call_id/'call.json').read_text())
        except FileNotFoundError: raise KeyError('call not found') from None
        if record['owner_user_id'] != user_id: raise KeyError('call not found')
        if record['status'] in {'accepted','ringing','active'} and call_id not in self.active:
            record['status']='unknown'
        return record

    def stop(self, call_id, user_id):
        record=self.get(call_id,user_id)
        with self.lock:
            if stop:=self.active.get(record['call_id']): stop.set()
        return record

    def start(self, *, user_id, account_ref, target, purpose, request_id,
              greeting='', max_seconds=120, confirm=False, agent_context=None):
        agent_context = dict(agent_context or {})
        if confirm is not True: raise ValueError('explicit call confirmation required')
        if user_id != self.service._store.default_user_id:
            raise PermissionError('voice gateway is bound to its owner')
        if not self.service._store.capability_enabled('send',user_id=user_id):
            raise PermissionError('send capability disabled')
        target=str(target)
        if (not re.fullmatch(r'[1-9][0-9]{0,18}',target)
            or not re.fullmatch(r'telegram:[1-9][0-9]{0,18}',account_ref)
            or account_ref.split(':',1)[1]==target or not purpose.strip()
            or not 16<=len(request_id)<=128 or len(purpose)>500 or len(greeting)>1000
            or type(max_seconds) is not int or not 30<=max_seconds<=600):
            raise ValueError('invalid Telegram call bounds')
        account=next((a for a in self.service._store.accounts(user_id=user_id)
                      if a['id']==account_ref and a['provider']=='telegram' and a['enabled']),None)
        provider=self.service.messenger_providers.get(account_ref)
        if not account or not provider or provider.authorized_user!=user_id:
            raise PermissionError('existing account-bound Telegram relay required')
        call_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'userio:telegram:'+user_id+':'+request_id))
        with self.lock:
            if (self.root/call_id/'call.json').exists():
                previous=self.get(call_id,user_id)
                if (previous['account_ref'],previous['target'],previous['purpose'],previous['greeting'],previous['max_seconds']) != (account_ref,target,purpose,greeting,max_seconds):
                    raise ValueError('request ID already belongs to another call')
                if previous.get('agent_context', {}) != agent_context:
                    raise ValueError('request ID already belongs to another context')
                return previous
            if self.active: raise RuntimeError('a Telegram call is already active')
            # A fresh read verifies the existing sender; it never creates a client.
            provider._capabilities()
            if provider.account_ref!=account_ref or provider.sender_id!=account_ref.split(':',1)[1]:
                raise ValueError('relay caller identity changed')
            record={'call_id':call_id,'admission_id':call_id,'owner_user_id':user_id,
                    'agent_context':agent_context,
                    'account_ref':account_ref,'target':target,'purpose':purpose,'greeting':greeting,
                    'max_seconds':max_seconds,'status':'accepted','answered':False,'started_at':time.time()}
            self._save(record)
            stop=threading.Event(); self.active[call_id]=stop
            threading.Thread(target=self._run,args=(record,provider,stop),daemon=True,
                             name='userio-telegram-call').start()
            return record.copy()

    def _emit(self, record, body, direction='system', suffix='status'):
        return self.service.receive(InboxMessage(source='telegram',
            message_id='telegramcall-'+record['call_id']+'-'+suffix,
            sender=record.get('voice_call_id',record['call_id']),body=body,received_at=time.time(),
            peer_id=record['target'],sender_name='ИИ-секретарь',conversation_kind='direct',
            direction=direction,reconciliation=True),route_id='telegram',
            user_id=record['owner_user_id'],account_ref=record['account_ref'])

    def _run(self, record, provider, stop):
        try: asyncio.run(self._call(record,provider,stop))
        except Exception as error:
            if record['status'] not in {'completed','unanswered','unknown'}:
                record['status']='failed'
            record['error']=type(error).__name__
            self._emit(record,'Не удалось завершить Telegram-звонок. Повторный звонок автоматически не выполняется.',suffix='error')
        finally:
            self._save(record)
            with self.lock: self.active.pop(record['call_id'],None)

    async def _call(self, record, provider, stop):
        path=PREFIX+quote(record['admission_id'],safe='')
        async def relay(method,suffix,payload=None):
            return await asyncio.to_thread(provider._request,method,path+suffix,payload)
        reader=writer=None; consumer=updates=None; transcripts={'caller':[],'assistant':[]}
        rpc_attempted=rpc_uncertain=teardown_confirmed=hangup_sent=False; turns=0
        try:
            # Claim the existing client's durable admission before any worker RPC.
            await asyncio.to_thread(provider._request,'POST',PREFIX+'start',{
                'admission_id':record['admission_id'],'account_id':record['account_ref'],
                'recipient_id':record['target'],'purpose':record['purpose']})
            reader,writer=await asyncio.open_unix_connection('/run/agentcall/control.sock',limit=65536)
            call={'admission_id':record['admission_id'],'user_id':record['target'],
                  'agent_context':record.get('agent_context', {}),
                  'history_account_ref':record['account_ref'],'peer_label':'Собеседник',
                  'purpose':record['purpose'],'greeting':record['greeting'],'max_seconds':record['max_seconds']}
            async def send(frame):
                writer.write(json.dumps(frame,ensure_ascii=False).encode()+b'\n'); await writer.drain()
            await send({'action':'telegram_transport','call':call})
            async def consume():
                nonlocal rpc_attempted,rpc_uncertain,teardown_confirmed,turns
                while True:
                    line=await reader.readline()
                    if not line:
                        teardown_confirmed=True
                        break
                    if len(line)>65536: raise ValueError('voice event exceeds limit')
                    event=json.loads(line); kind=event.get('event')
                    if event.get('ok') is False: raise RuntimeError('voice gateway refused Telegram transport')
                    if kind=='telegram_rpc':
                        if event['method']=='phone.requestCall' and (hangup_sent or stop.is_set()):
                            await send({'type':'rpc_result','request_id':event['request_id'],'error':True})
                            continue
                        if event['method']=='phone.requestCall': rpc_attempted=True
                        try:
                            result=await relay('POST','/rpc',{'request_id':relay_request_id(event['request_id']),
                                'method':event['method'],'params':event['params']})
                            await send({'type':'rpc_result','request_id':event['request_id'],'result':result['result']})
                        except Exception:
                            if rpc_attempted:
                                rpc_uncertain=True
                                record['status']='unknown'
                            await send({'type':'rpc_result','request_id':event['request_id'],'error':True})
                        continue
                    if event.get('callId'): record['voice_call_id']=str(uuid.UUID(event['callId']))
                    if kind=='prepared': record['status']='ringing'; self._emit(record,'ИИ-секретарь звонит через Telegram',suffix='ringing')
                    elif kind in {'connected','active'}:
                        record['status']='active'; record['answered']=True
                    elif kind=='transcript_final':
                        text=str(event.get('text') or '').strip(); remote=event.get('speaker')=='remote'
                        if text:
                            transcripts['caller' if remote else 'assistant'].append(text); turns+=1
                            self._emit(record,('Собеседник: ' if remote else 'ИИ: ')+text,
                                       'incoming' if remote else 'outgoing',suffix='turn-'+str(turns))
                    elif kind=='ended': record['status']='completed' if record['answered'] else 'unanswered'
                    elif kind=='error':
                        record_call_error(record, event)
                    self._save(record)
            async def poll():
                cursor=0
                while True:
                    result=await relay('GET','/updates?after='+str(cursor))
                    events=result.get('events',[]); next_cursor=result.get('next_cursor',cursor)
                    if not isinstance(events,list) or len(events)>64 or type(next_cursor) is not int or next_cursor<cursor:
                        raise ValueError('invalid relay update page')
                    for item in events:
                        update=item.get('update',item)
                        await send({'type':'telegram_update','update':update})
                    cursor=next_cursor
                    await asyncio.sleep(.25)
            consumer,updates=asyncio.create_task(consume()),asyncio.create_task(poll())
            deadline=time.monotonic()+record['max_seconds']+240
            while not consumer.done() and not updates.done() and not stop.is_set() and time.monotonic()<deadline:
                await asyncio.sleep(.1)
            poll_error=None
            if updates.done():
                try: await updates
                except Exception as error: poll_error=error
            if not consumer.done():
                # Keep RPC replies flowing until the worker has discarded/stopped.
                # A lost update page must not tear down its final RPC channel.
                hangup_sent=True
                await send({'type':'hangup'})
                await asyncio.wait_for(consumer,timeout=70)
            else: await consumer
            if record['status'] in {'accepted','ringing','active'}:
                record['status']='unknown' if rpc_attempted else 'failed'
            if record.get('voice_call_id'):
                from .call_audio import ARCHIVE
                folder=ARCHIVE/record['voice_call_id']
                if folder.is_dir():
                    transcript=folder/'live-transcript.json'
                    transcript.write_text(json.dumps({k:'\n'.join(v) for k,v in transcripts.items()},ensure_ascii=False))
                    os.chmod(transcript,0o600)
                    self._emit(record,'Звонок завершён через Telegram',suffix='ended')
            if record['status']=='unanswered':
                self._emit(record,'Звонок через Telegram завершён без ответа.',suffix='ended')
            if poll_error: raise poll_error
        finally:
            for task in (consumer,updates):
                if task and not task.done(): task.cancel()
            await asyncio.gather(*(t for t in (consumer,updates) if t),return_exceptions=True)
            if writer:
                writer.close()
                try: await writer.wait_closed()
                except OSError: pass
            # Local status reconciliation only. Never retry a provider RPC or dial.
            try:
                status=await relay('GET','/status')
                record['relay_status']=str(status.get('state') or '')[:80]
                uncertain=(status.get('state') in {'CLAIMED','UNKNOWN'} and status.get('provider_attempted') is True)
                if rpc_uncertain or uncertain or rpc_attempted and not teardown_confirmed:
                    record['status']='unknown'
                    record['relay_reconciliation']='pending'
                else: await relay('POST','/close',{})
            except Exception:
                record['relay_reconciliation']='unavailable'
                if rpc_attempted and (rpc_uncertain or not teardown_confirmed): record['status']='unknown'


def calls_for(service):
    with service._manual_send_lock:
        manager=getattr(service,'_telegram_calls',None)
        if manager is None: manager=service._telegram_calls=TelegramCalls(service)
    return manager
