"""Personal MAX enrollment and clients on the existing UserIO account registry."""
from __future__ import annotations
import base64, io, os, re, secrets, threading, time
from .account_connections import ConnectorSecrets
from .channels.max_client import MaxClient,MaxTransport
from .max_qr_login import MaxQrLogin
from .max_ingress import message_envelope
from .adapters import inbox_message_from_envelope


def native_client(token,device_id):
    proxy=os.environ.get('USERIO_MAX_SOCKS','').strip()
    host,_,port=proxy.rpartition(':') if proxy else ('','','0')
    return MaxClient(token,device_id,app_version='26.10.3',
        transport=MaxTransport(socks_host=host,socks_port=int(port or 0),web_auth=True))


def owned_client(service,user_id,account_ref):
    account=next((a for a in service._store.accounts(user_id=user_id) if a['id']==account_ref
                  and a['provider']=='max' and a['enabled'] and 'read' in a['capabilities']),None)
    if not account:raise ValueError('MAX account not assigned to this user')
    if str(account['credential_ref']).startswith('connector:'):
        record=ConnectorSecrets().load(account['credential_ref'],user_id=user_id,provider='max')
        if record.get('account_id')!=account_ref:raise ValueError('MAX account binding conflict')
        if not record.get('max_user_id') or (account_ref!='max' and account_ref!='max:'+record['max_user_id']):
            raise ValueError('MAX personal identity binding invalid')
        key=(user_id,account_ref)
        with service._max_owned_lock:
            cache=service._max_owned_clients
            old=cache.get(key)
            if old and old[0]==account['credential_ref']:return old[1]
            if old:old[1].close();del cache[key]
            if len(cache)>=32:raise ValueError('MAX connection capacity reached')
            client=native_client(record['token'],record['device_id'])
            client.expected_user_id=record['max_user_id']
            cache[key]=(account['credential_ref'],client)
            return client
    if account_ref=='max' and user_id==service.max_user_id and service.max_client is not None:
        return service.max_client
    raise ValueError('MAX personal credential unavailable')


def _own_id_from(profile):return str((profile.get('profile') or {}).get('contact',{}).get('id') or '')


def envelope_for(account_ref,row,peer,own,*,reconciliation=True,kind='unknown',title=''):
    env=message_envelope(row,chat_id=peer,own_id=own,reconciliation=reconciliation,kind=kind,title=title)
    if env and account_ref!='max':env['message_id']=account_ref+'|'+peer+':'+str(row['id'])
    return env


class MaxAccounts:
    def __init__(self,service,*,flow_factory=MaxQrLogin,client_factory=native_client,clock=time.time):
        self.service=service;self.flow_factory=flow_factory;self.client_factory=client_factory
        self.clock=clock;self.pending={};self.lock=threading.RLock()

    def _prune(self):
        for key,row in list(self.pending.items()):
            if row['expires']<=self.clock():row['flow'].close();del self.pending[key]

    def expire(self):
        # Called by the existing HTTP server maintenance loop, never another
        # thread per QR challenge. Do not block serving during provider I/O.
        if self.lock.acquire(blocking=False):
            try:self._prune()
            finally:self.lock.release()

    def close(self):
        with self.lock:
            for row in self.pending.values():row['flow'].close()
            self.pending.clear()

    def begin(self,user_id):
        with self.lock:
            self._prune()
            if len(self.pending)>=8 or any(r['user']==user_id for r in self.pending.values()):
                raise ValueError('У вас уже есть начатый вход. Завершите или отмените его.')
            proxy=os.environ.get('USERIO_MAX_SOCKS','').strip()
            host,_,port=proxy.rpartition(':') if proxy else ('','','0')
            flow=self.flow_factory('',socks_host=host,socks_port=int(port or 0))
            try:
                p=flow.begin();import qrcode
                if not re.fullmatch(r'https://max\.ru/:auth/[0-9a-fA-F-]{36}',p['qrLink']):
                    raise ValueError('MAX official account link unavailable')
                output=io.BytesIO();qrcode.make(p['qrLink']).save(output,format='PNG')
                key='max-login-'+secrets.token_hex(24);expires=self.clock()+min(p['ttl']/1000,180)
                self.pending[key]={'flow':flow,'user':user_id,'expires':expires}
                return {'login_id':key,'qr_link':p['qrLink'],'qr_image':'data:image/png;base64,'+base64.b64encode(output.getvalue()).decode(),
                        'expires_at':expires,'poll_interval_ms':max(p['pollingInterval'],1000),'status':'waiting'}
            except Exception:flow.close();raise

    def cancel(self,user_id,login_id):
        with self.lock:
            row=self.pending.get(login_id)
            if not row or row['user']!=user_id:raise ValueError('Ваш вход MAX не найден')
            row['flow'].close();del self.pending[login_id]
            return {'cancelled':True}

    def poll(self,user_id,login_id):
        with self.lock:
            self._prune();row=self.pending.get(login_id)
            if not row or row['user']!=user_id:raise ValueError('Вход MAX истёк или принадлежит другому пользователю')
            flow=row['flow']
            if not flow.poll():return {'status':'waiting','expires_at':row['expires']}
            # AuthByTrack is an external login effect: consume before finishing,
            # then never repeat that same challenge after an unknown result.
            del self.pending[login_id]
            client=None
            try:
                credential=flow.finish();actual=credential['user_id']
                client=self.client_factory(credential['token'],credential['device_id'])
                profile=client.connect()
                if _own_id_from(profile)!=actual:raise ValueError('MAX returned a different account')
                account_ref='max:'+actual
                if user_id==self.service.max_user_id and self.service.max_client is not None:
                    try:legacy=self.service.max_client.connect()
                    except Exception:legacy={}
                    if _own_id_from(legacy)==actual:account_ref='max'
                contact=(profile.get('profile') or {}).get('contact') or {}
                label=' '.join(str(contact.get(k) or '') for k in ('firstName','lastName')).strip() or 'MAX '+actual
                old=next((a for a in self.service._store.accounts(user_id=user_id) if a['id']==account_ref),None)
                vault=ConnectorSecrets();ref=vault.save({'provider':'max','kind':'personal','user_id':user_id,
                    'account_id':account_ref,'max_user_id':actual,'token':credential['token'],'device_id':credential['device_id']})
                try:
                    self.service._store.register_account(account_id=account_ref,provider='max',display_name=label,
                        can_read=True,can_reply=True,credential_ref=ref,user_id=user_id)
                except Exception:
                    vault.delete(ref,user_id=user_id,provider='max')
                    raise
                close_owned_client(self.service,user_id,account_ref)
                self.service._store.bind_channel_route(user_id=user_id,source='max',route_id='max')
                if old and str(old['credential_ref']).startswith('connector:'):
                    try:vault.delete(old['credential_ref'],user_id=user_id,provider='max')
                    except (OSError,ValueError,PermissionError):pass
                return {'status':'connected','id':account_ref,'display_name':label,'sender_id':actual}
            finally:
                flow.close()
                if client:client.close()


def close_owned_client(service, user_id, account_ref):
    with service._max_owned_lock:
        previous = service._max_owned_clients.pop((user_id, account_ref), None)
        if previous:
            previous[1].close()


def poll_personal_max(service, record):
    """Import recent history and keep syncing without marking messages read.

    A catch-up page that cannot reach the previous cursor keeps that cursor;
    accepted rows remain deduplicated by their account-specific provider IDs.
    """
    user, ref, own = record['user_id'], record['account_id'], record['max_user_id']
    client = native_client(record['token'], record['device_id'])
    started = time.monotonic()
    try:
        if _own_id_from(client.connect()) != own:
            raise ValueError('MAX poll identity conflict')
        for chat in client.chats(count=100):
            if time.monotonic() - started > 25:
                break
            peer = str(chat['id'])
            key = 'max-personal:' + ref + ':' + peer
            previous = service._store.user_preference(key, user_id=user)
            cursor = int(previous or 0)
            rows = client.history(peer, backward=100)
            # Initial recent-history import is bounded. Later sweeps catch up
            # to the saved cursor before advancing it, including equal times.
            reached = previous is None or not rows
            for _ in range(7):
                if not rows or min(int(r.get('time') or 0) for r in rows) <= cursor:
                    reached = True
                    break
                if previous is None or time.monotonic() - started > 25:
                    break
                before = min(int(r.get('time') or 0) for r in rows) - 1
                page = client.history(peer, backward=100, before=before)
                if not page:
                    reached = True
                    break
                rows.extend(page)
            for item in sorted(rows, key=lambda r: int(r.get('time') or 0)):
                env = envelope_for(ref, item, peer, own, reconciliation=True,
                    kind='group' if chat.get('type') in {'CHAT', 'GROUP'} else 'direct',
                    title=str(chat.get('title') or ''))
                if not env:
                    continue
                stored = service._store.message(env['message_id'], source='max', user_id=user)
                # Store old edits/outgoing messages too, but never redispatch
                # imported history or an already accepted live message.
                env['reconciliation'] = previous is None or stored is not None or int(item.get('time') or 0) < cursor
                service.receive(inbox_message_from_envelope(env, received_at=time.time()),
                    route_id='max', user_id=user, account_ref=ref)
            if rows and reached:
                service._store.set_user_preference(key,
                    str(max(cursor, max(int(r.get('time') or 0) for r in rows))), user_id=user)
    finally:
        client.close()
