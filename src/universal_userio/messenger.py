"""Account-bound messenger operations shared by the HTTP and stdio MCP.

Providers own protocol/session access. This boundary owns source identity,
explicit approval, canonical projections and durable mutation outcomes.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time

from .contracts import DeliveryNotSubmittedError, ProviderAuthenticationRequiredError
from .source_projection import canonical_author, canonical_source

SCHEMA = 'userio.messenger.v1'
OPERATIONS = ('read', 'history', 'download', 'reply', 'edit', 'react', 'sticker', 'file', 'file_upload', 'typing')
READ_OPERATIONS = frozenset({'read', 'history', 'download'})
TELEGRAM_ADMIN_OPERATIONS = frozenset({'group_create', 'group_invite', 'group_remove',
                                      'channel_create', 'channel_delete'})


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


class Messenger:
    def __init__(self, store, service, principal):
        self.store, self.service, self.principal = store, service, principal
        self.user = principal.user_id

    def _account(self, account_ref):
        if (self.principal.messenger_accounts is not None
                and account_ref not in self.principal.messenger_accounts):
            raise PermissionError('account_outside_credential_scope')
        account = next((a for a in self.store.accounts(user_id=self.user)
                        if a['id'] == account_ref), None)
        if account is None:
            raise KeyError('account_not_found')
        return account

    def _provider(self, account_ref):
        provider = getattr(self.service, 'messenger_providers', {}).get(account_ref)
        account=self._account(account_ref)
        if (provider is None and account['provider']=='telegram' and account['enabled']
                and str(account['credential_ref']).startswith('telegram-qr:')):
            from .messenger_telegram_mirror import TelegramMirrorProvider
            provider=TelegramMirrorProvider(self.service,account_ref,self.user)
        if account['provider']=='max' and account['enabled'] and str(account['credential_ref']).startswith('connector:'):
            from .messenger_max import PersonalMaxMessengerProvider
            provider=PersonalMaxMessengerProvider(self.service,account_ref,self.user)
        if provider is None:
            account=self._account(account_ref)
            if (account['provider']=='vk' and account_ref.startswith('vk:')
                    and account['enabled'] and str(account['credential_ref']).startswith('connector:')):
                from .messenger_vk import VKMessengerProvider
                provider=VKMessengerProvider(self.service,account_ref,self.user)
        if provider is not None and getattr(provider,'authorized_user',self.user)!=self.user:
            return None
        return provider

    def capabilities(self, account_ref):
        account = self._account(account_ref)
        provider = self._provider(account_ref)
        ops = getattr(provider, 'operations', ()) if provider is not None else ()
        authentication_required = getattr(provider, 'authentication_required', False) is True
        result = {}
        for name in OPERATIONS:
            # All registered accounts have bounded stored reading; live
            # history/media and mutations require an actual configured route.
            supported = name in {'read', 'history'} or name in ops
            reason = ''
            if not supported: reason = 'provider_not_supported'
            elif not account['enabled']: reason = 'account_disabled'
            elif name not in READ_OPERATIONS and self.principal.read_only: reason = 'read_only_token'
            elif ('read' if name in READ_OPERATIONS else 'reply') not in account['capabilities']:
                reason = 'account_capability_disabled'
            elif not self.store.capability_enabled(
                    'download' if name == 'download' else 'read' if name in READ_OPERATIONS else 'send', user_id=self.user):
                reason = 'user_capability_disabled'
            elif authentication_required and name not in {'read', 'history'}:
                reason = 'provider_authentication_required'
            result[name] = {'supported': supported, 'allowed': supported and not reason, 'reason':reason}
            if name=='typing' and supported:
                result[name]['stop_mode']=getattr(provider,'typing_stop_mode','provider_command')
        admin_outbox=getattr(self.service,'telegram_outbox',None)
        admin_supported=(account['provider']=='telegram' and account['enabled']
            and str(account.get('credential_ref') or '').startswith('telegram-qr:')
            and callable(getattr(provider,'execute_telegram_admin',None))
            and callable(getattr(admin_outbox,'telegram_admin_prepare',None))
            and callable(getattr(admin_outbox,'telegram_admin_execute',None)))
        admin_reason=''
        if not admin_supported: admin_reason='provider_not_supported'
        elif self.principal.messenger_grants is not None:
            grant=next((g for g in self.principal.messenger_grants if g['account_ref']==account_ref),None)
            if grant is None: admin_reason='account_outside_credential_scope'
            elif not grant.get('telegram_admin',False): admin_reason='telegram_admin_permission_required'
        elif self.principal.messenger_accounts is not None:
            admin_reason='telegram_admin_permission_required'
        elif self.principal.read_only: admin_reason='read_only_token'
        elif 'reply' not in account['capabilities']: admin_reason='account_capability_disabled'
        elif not self.store.capability_enabled('send',user_id=self.user): admin_reason='user_capability_disabled'
        provider_upload_limit=self._upload_limit(provider)
        storage_upload_limit=self._storage_upload_limit()
        effective_upload_limit=(min(provider_upload_limit,storage_upload_limit)
            if type(provider_upload_limit) is int else None)
        return {'schema':SCHEMA, 'account_ref':account_ref, 'channel':account['provider'],
                'sender_id':getattr(provider, 'sender_id', ''),
                'history_mode':'provider' if 'history' in ops and not authentication_required else 'stored',
                'provider_status':'authentication_required' if authentication_required else 'configured' if provider is not None else 'unconfigured',
                'credential_scope':('scoped_messenger' if self.principal.messenger_grants is not None
                                    else 'messenger_actions' if self.principal.messenger_accounts is not None
                                    else 'read_only' if self.principal.read_only else 'full_access'),
                'operations':result,
                'telegram_admin':{'supported':admin_supported,'allowed':admin_supported and not admin_reason,
                    'reason':admin_reason,'actions':sorted(TELEGRAM_ADMIN_OPERATIONS)},
                **({'reaction_options':provider.reaction_options} if hasattr(provider,'reaction_options') else {}),
                'upload_limits':{'max_bytes':effective_upload_limit,
                    'provider_max_bytes':provider_upload_limit,
                    'storage_max_bytes':storage_upload_limit,'ttl_seconds':900}}

    def _storage_upload_limit(self):
        from .messenger_uploads import MAX_STORAGE_BYTES
        import sqlite3
        getlimit=getattr(self.store._connection,'getlimit',None)
        sqlite_limit=getlimit(getattr(sqlite3,'SQLITE_LIMIT_LENGTH',0)) if callable(getlimit) else MAX_STORAGE_BYTES
        return min(MAX_STORAGE_BYTES,sqlite_limit)

    @staticmethod
    def _upload_limit(provider):
        from .messenger_uploads import MAX_BYTES
        get_limit=getattr(provider,'upload_limit',None)
        if callable(get_limit):
            try:
                value=get_limit()
                if type(value) is int and value>0:return value
            except Exception:
                return None
            return None
        return MAX_BYTES

    def _allow(self, account_ref, operation):
        capability = self.capabilities(account_ref)['operations'][operation]
        if not capability['allowed']:
            if capability['reason'] == 'provider_authentication_required':
                raise ProviderAuthenticationRequiredError(self._account(account_ref)['provider'])
            raise PermissionError(capability['reason'])

    def _conversation(self, account_ref, conversation_id):
        conv = self.store.conversation(conversation_id, user_id=self.user)
        if conv and self._account(account_ref)['provider']=='gmail':
            provider=self._provider(account_ref)
            if callable(getattr(provider,'conversation',None)):
                return provider.conversation(conv)
        if not conv or conv['account_ref'] != account_ref:
            raise KeyError('conversation_not_found')
        if conv['source'] != self._account(account_ref)['provider']:
            raise ValueError('account_source_conflict')
        return conv

    def _message(self, conv, message_id):
        msg = self.store.message(message_id, source=conv['source'], user_id=self.user)
        if not msg or msg['conversation_id'] != conv['id']:
            raise KeyError('message_not_found')
        return msg

    def _project(self, conv, msg):
        account_ref, peer, mid = conv['account_ref'], str(conv['peer_id']), str(msg['message_id'])
        provider_id = ''
        if conv['source'] in {'telegram','vk','max'} and mid.startswith(account_ref+'|'+peer+':'):
            provider_id = mid[len(account_ref+'|'+peer+':'):]
        elif conv['source'] == 'max' and mid.startswith('max-'+peer+'-'):
            provider_id = mid[len('max-'+peer+'-'):]
        if conv['source'] not in {'telegram','max','vk'}: provider_id=mid
        if not provider_id or conv['source'] in {'telegram','max','vk'} and not re.fullmatch(r'[1-9][0-9]*', provider_id):
            raise ValueError('provider_source_id_unavailable')
        routing = {}
        media = []
        attachments = msg.get('attachments') or self.store.attachments_for_message(
            source=conv['source'], message_id=mid, user_id=self.user)
        for idx, a in enumerate(attachments):
            if a.get('kind') == 'telegram_routing':
                try: routing = json.loads(str(a.get('provider_ref') or '{}'))
                except (TypeError, ValueError): routing = {}
                if not isinstance(routing, dict): routing = {}
                continue
            media.append({k:a[k] for k in ('kind','filename','content_type','size','idx') if k in a} |
                         {'message_id':mid, 'attachment_index':idx,
                          'attachment_id':str(a.get('attachment_id') or '')})
        outgoing = msg['direction'] == 'outgoing'
        channel = self._account(account_ref)['provider']
        # One canonical rule for who wrote a message: the stored sender is the
        # chat counterpart, never the author of an outgoing message.
        author = canonical_author(
            channel=channel, account_ref=account_ref,
            conversation_kind=str(conv['conversation_kind']), peer_id=peer,
            direction=str(msg['direction']), sender=str(msg['sender']),
            sender_name=str(msg.get('sender_name') or ''), routing=routing,
            gmail_sender_id=(self._provider(account_ref).sender_id
                             if outgoing and channel == 'gmail' else ''),
        )
        source = canonical_source(
            account_ref=account_ref, conversation_id=conv['id'], peer_id=peer,
            conversation_kind=str(conv['conversation_kind']), channel=channel,
            message_id=mid, provider_message_id=provider_id,
            direction=str(msg['direction']), routing=routing,
            thread_id=str(msg.get('provider_thread_id') or ''),
            reply_to=str(msg.get('provider_reply_to') or ''))
        return {'source':source, 'author':author, 'text':str(msg['body']),
                'at':msg['received_at'], 'edited_at':msg.get('edited_at', 0), 'media':media}

    def resolve(self, account_ref, peer_id, provider_message_id):
        self._allow(account_ref,'read')
        channel=self._account(account_ref)['provider']
        if channel=='gmail':
            provider=self._provider(account_ref)
            if not callable(getattr(provider,'resolve',None)):raise ValueError('provider_resolve_unavailable')
            cid=provider.resolve(peer_id,provider_message_id)
            conv=self._conversation(account_ref,cid)
            return {'account_ref':account_ref,'conversation_id':cid,
                'message':self._project(conv,self._message(conv,provider_message_id)),
                'fresh':False,'mode':'stored','provider_read_acknowledged':False}
        if not isinstance(peer_id,str) or not (re.fullmatch(r'-?[1-9][0-9]*',peer_id)
                                              or peer_id=='0' and channel=='max'):
            raise ValueError('exact_peer_required')
        if not isinstance(provider_message_id,str) or not re.fullmatch(r'[1-9][0-9]*',provider_message_id):
            raise ValueError('exact_provider_message_id_required')
        provider=self._provider(account_ref)
        if getattr(provider, 'authentication_required', False) is True:
            raise ProviderAuthenticationRequiredError(channel)
        if not callable(getattr(provider,'message',None)) or not callable(getattr(provider,'import_projection',None)):
            raise ValueError('provider_resolve_unavailable')
        channel=self._account(account_ref)['provider']
        key=f'{channel}:{account_ref}:{peer_id}'
        conversation_id=self.store.conversation_id_for_key(key,user_id=self.user)
        conversation_id=conversation_id or 'conv_'+hashlib.sha256((self.user+'\0'+key).encode()).hexdigest()[:24]
        p=provider.message(peer_id=peer_id,provider_message_id=provider_message_id,conversation_id=conversation_id)
        if p['source']['provider_message_id']!=provider_message_id: raise ValueError('provider_source_mismatch')
        kind=p['source'].get('conversation_kind') or 'unknown'
        provider.import_projection(p,peer_id=peer_id,conversation_id=conversation_id,kind=kind)
        conv=self._conversation(account_ref,conversation_id)
        projected=self._project(conv,self._message(conv,p['source']['message_id']))
        if isinstance(p.get('reaction_info'),dict): projected['reaction_info']=p['reaction_info']
        if isinstance(p.get('reaction_readback'),dict): projected['reaction_readback']=p['reaction_readback']
        return {'account_ref':account_ref,'conversation_id':conversation_id,
                'message':projected,
                'fresh':True,'provider_read_acknowledged':False}

    def history(self, account_ref, conversation_id, *, limit=50, cursor='', refresh=False):
        self._allow(account_ref, 'history')
        conv = self._conversation(account_ref, conversation_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('limit_must_be_1_to_100')
        if type(refresh) is not bool: raise ValueError('refresh_must_be_boolean')
        if refresh:
            provider=self._provider(account_ref)
            if getattr(provider, 'authentication_required', False) is True:
                raise ProviderAuthenticationRequiredError(conv['source'])
            if not callable(getattr(provider,'history',None)) or not callable(getattr(provider,'import_projection',None)):
                raise ValueError('provider_history_unavailable')
            page=provider.history(peer_id=conv['peer_id'],conversation_id=conv['id'],limit=limit,cursor=cursor)
            for p in page['messages']:
                provider.import_projection(p,peer_id=conv['peer_id'],conversation_id=conv['id'],kind=conv['conversation_kind'])
            return {'schema':SCHEMA,'account_ref':account_ref,'conversation_id':conv['id'],
                    **page,'fresh':True,'mode':'provider','provider_read_acknowledged':False}
        values = [self.user, conv['id']]
        where = ''
        if cursor:
            if not isinstance(cursor,str) or len(cursor)>1024: raise ValueError('invalid_cursor')
            try:
                c = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if (c['account_ref'],c['conversation_id']) != (account_ref,conv['id']):
                    raise ValueError('cursor_scope_conflict')
                if type(c['rowid']) is not int or type(c['at']) not in (int,float):
                    raise ValueError('invalid_cursor')
            except (KeyError, TypeError, ValueError): raise ValueError('invalid_cursor') from None
            where = ' AND (received_at<? OR (received_at=? AND rowid<?))'
            values.extend([c['at'],c['at'],c['rowid']])
        with self.store._lock:
            rows = self.store._connection.execute(
                'SELECT rowid AS _rowid,* FROM messages WHERE user_id=? AND conversation_id=? '
                'AND deleted_at IS NULL'+where+' ORDER BY received_at DESC,rowid DESC LIMIT ?',
                [*values,limit+1]).fetchall()
        page = rows[:limit]
        next_cursor = ''
        if len(rows)>limit:
            last = page[-1]
            next_cursor = base64.urlsafe_b64encode(canonical({'account_ref':account_ref,
                'conversation_id':conv['id'], 'at':last['received_at'], 'rowid':last['_rowid']})).decode()
        return {'schema':SCHEMA,'account_ref':account_ref,'conversation_id':conv['id'],
                'messages':[self._project(conv,dict(row)) for row in reversed(page)],
                'next_cursor':next_cursor,'partial':True,'fresh':False,'mode':'stored',
                'more_stored':bool(next_cursor)}

    def events(self, account_ref, *, after=None, limit=50):
        """Account transport feed, without changing personal AI policy or read state.

        A new consumer starts at the current head. History/recovery rows never
        dispatch, and account filtering happens before pagination.
        """
        self._allow(account_ref, 'read')
        if after is not None and (type(after) is not int or not 0 <= after < 2**63):
            raise ValueError('invalid_event_cursor')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('limit_must_be_1_to_100')
        provider=self._provider(account_ref)
        mail_source=getattr(provider,'source','') if self._account(account_ref)['provider']=='gmail' else ''
        account_filter='c.source=?' if mail_source else 'c.account_ref=?'
        feed_ref=mail_source or account_ref
        with self.store._lock:
            head = self.store._connection.execute('''SELECT COALESCE(MAX(e.seq),0)
                FROM workspace_events e JOIN conversations c
                ON c.user_id=e.user_id AND c.id=e.conversation_id
                WHERE e.user_id=? AND '''+account_filter, (self.user, feed_ref)).fetchone()[0]
            if after is None:
                rows = []
            else:
                rows = self.store._connection.execute('''SELECT e.seq,e.eligible,e.message_id,e.conversation_id
                    FROM workspace_events e JOIN conversations c
                    ON c.user_id=e.user_id AND c.id=e.conversation_id
                    JOIN messages m ON m.user_id=e.user_id AND m.source=e.source AND m.message_id=e.message_id
                    WHERE e.user_id=? AND '''+account_filter+''' AND e.seq>? AND e.seq<=?
                    AND e.reconciled=0 AND m.direction='incoming' AND m.deleted_at IS NULL
                    ORDER BY e.seq LIMIT ?''', (self.user, feed_ref, after, head, limit+1)).fetchall()
            more = len(rows) > limit
            events = []
            for row in rows[:limit]:
                conv = self._conversation(account_ref, row['conversation_id'])
                events.append({'seq':row['seq'], 'analysis_eligible':bool(row['eligible']),
                    'message':self._project(conv, self._message(conv, row['message_id']))})
        return {'schema':SCHEMA, 'account_ref':account_ref, 'events':events,
                'head':head, 'next_cursor':events[-1]['seq'] if more else max(head, after or 0),
                'has_more':more, 'initialized':after is None, 'provider_read_acknowledged':False}

    def _init_actions(self):
        # Operation journal, not a second policy/credential system. The same
        # authenticated UserIO principal and account permissions govern it.
        with self.store._lock, self.store._connection:
            self.store._connection.execute('''CREATE TABLE IF NOT EXISTS messenger_actions (
                user_id TEXT NOT NULL,id TEXT NOT NULL,request_id TEXT NOT NULL,
                envelope TEXT NOT NULL,hash TEXT NOT NULL,status TEXT NOT NULL,
                created_at REAL NOT NULL,receipt TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(user_id,id),UNIQUE(user_id,request_id))''')

    @staticmethod
    def _payload(action, payload):
        if not isinstance(payload,dict): raise ValueError('payload_must_be_object')
        keys = {'reply':{'text'},'edit':{'text','expected_text'},'react':{'emoji'},
                'sticker':{'media_message_id','attachment_index'},'file':{'media_message_id','attachment_index','caption'},
                'typing':{'active'}}
        if action=='file' and 'upload_id' in payload:
            if set(payload)-{'upload_id','caption','force_document'} or not isinstance(payload['upload_id'],str) or not re.fullmatch(r'upload_[0-9a-f]{64}',payload['upload_id']):
                raise ValueError('invalid_upload_payload')
            if 'caption' in payload and (not isinstance(payload['caption'],str) or len(payload['caption'])>4096):
                raise ValueError('invalid_caption')
            if 'force_document' in payload and type(payload['force_document']) is not bool:
                raise ValueError('invalid_force_document')
            return dict(payload)
        if action not in keys or set(payload)-keys[action]: raise ValueError('invalid_action_payload')
        if action in {'reply','edit'}:
            if not isinstance(payload.get('text'),str) or not payload['text'].strip() or len(payload['text'])>65536:
                raise ValueError('text_required')
        if action=='edit' and not isinstance(payload.get('expected_text'),str): raise ValueError('expected_text_required')
        if action=='react' and (not isinstance(payload.get('emoji'),str) or len(payload['emoji'])>16
                               or payload['emoji'] and not payload['emoji'].strip()):
            raise ValueError('emoji_required')
        if action in {'file','sticker'}:
            if not isinstance(payload.get('media_message_id'),str) or type(payload.get('attachment_index')) is not int:
                raise ValueError('owned_media_reference_required')
            if not 0<=payload['attachment_index']<=7: raise ValueError('invalid_attachment_index')
            if action=='file' and ('caption' in payload and (not isinstance(payload['caption'],str) or len(payload['caption'])>4096)):
                raise ValueError('invalid_caption')
        if action=='typing' and type(payload.get('active')) is not bool: raise ValueError('active_boolean_required')
        return dict(payload)

    def _binding(self, conv, message_id):
        projected = self._project(conv,self._message(conv,message_id))
        source, author = projected['source'], projected['author']
        binding = {key:source[key] for key in ('account_ref','peer_id','provider_message_id',
                  'direction','thread_id','reply_to_provider_message_id')}
        binding.update(author_id=author['id'], edited_at=int(projected['edited_at'] or 0),
                       text_sha256=hashlib.sha256(projected['text'].encode('utf-8')).hexdigest())
        projected['binding'] = binding
        return projected, digest(binding)

    def media(self, account_ref, conversation_id, message_id, attachment_index=0):
        self._allow(account_ref,'download')
        conv=self._conversation(account_ref,conversation_id)
        msg=self._message(conv,message_id)
        projected=self._project(conv,msg)
        provider=self._provider(account_ref)
        reader=getattr(provider,'media',None)
        if not callable(reader): raise ValueError('provider_media_unavailable')
        if type(attachment_index) is not int or not 0<=attachment_index<=7: raise ValueError('invalid_attachment_index')
        return {'schema':SCHEMA,'account_ref':account_ref,'conversation_id':conv['id'],
                'source':projected['source'],'content':reader(projected,attachment_index=attachment_index),
                'provider_read_acknowledged':False}

    def _source_still_current(self, conv, envelope):
        _, source_hash = self._binding(conv,envelope['source_message_id'])
        if source_hash!=envelope['source_hash']: raise ValueError('source_snapshot_conflict')
        if envelope['media_hash']:
            media, _ = self._binding(conv,envelope['payload']['media_message_id'])
            if self._media_hash(media,envelope['payload']['attachment_index'])!=envelope['media_hash']:
                raise ValueError('media_snapshot_conflict')
        if 'upload_id' in envelope['payload']:
            self._allow(envelope['account_ref'],'file_upload')
            from .messenger_uploads import MessengerUploads
            MessengerUploads(self.store,self.user).data(envelope)

    def upload(self, account_ref, conversation_id, source_message_id, *, filename, mime_type, data_base64, sha256, request_id):
        self._allow(account_ref,'file_upload')
        self._allow(account_ref,'file')
        conv=self._conversation(account_ref,conversation_id)
        if not self.store.route_allowed(user_id=self.user,source=conv['source'],route_id=conv['route_id']):
            raise PermissionError('route_disabled')
        projected,source_hash=self._binding(conv,source_message_id)
        from .messenger_uploads import MessengerUploads
        binding={'account_ref':account_ref,'conversation_id':conv['id'],'source_message_id':source_message_id,
                 'source_hash':source_hash,'source_binding':projected['binding'],'thread_id':projected['source']['thread_id']}
        provider=self._provider(account_ref)
        return MessengerUploads(self.store,self.user).stage(binding,filename=filename,mime_type=mime_type,
            data_base64=data_base64,sha256=sha256,request_id=request_id,
            max_bytes=self._upload_limit(provider))

    @staticmethod
    def _media_hash(projected, index):
        attachment=next((a for a in projected['media'] if a['attachment_index']==index),None)
        if not attachment or not attachment['attachment_id']:
            raise ValueError('actual_provider_attachment_id_required')
        return digest({'source_binding':projected['binding'],
                       'attachment_id':attachment['attachment_id'],'attachment_index':index})

    def propose(self, account_ref, conversation_id, source_message_id, action, payload, request_id):
        if action not in OPERATIONS or action in READ_OPERATIONS: raise ValueError('invalid_action')
        self._allow(account_ref,action)
        if not isinstance(request_id,str) or not re.fullmatch(r'[a-zA-Z0-9:_-]{16,128}',request_id):
            raise ValueError('request_id_required')
        payload = self._payload(action,payload)
        conv = self._conversation(account_ref,conversation_id)
        supports_action=getattr(self._provider(account_ref),'supports_action',None)
        if callable(supports_action) and supports_action(action,conv) is False:
            raise PermissionError('provider_not_supported_for_conversation')
        if not self.store.route_allowed(user_id=self.user, source=conv['source'],route_id=conv['route_id']):
            raise PermissionError('route_disabled')
        projected, source_hash = self._binding(conv,source_message_id)
        if action=='edit' and (projected['source']['direction']!='outgoing' or projected['text']!=payload['expected_text']):
            raise ValueError('own_current_message_required')
        media_hash = ''
        if action in {'sticker','file'} and 'upload_id' not in payload:
            media, _ = self._binding(conv,payload['media_message_id'])
            media_hash = self._media_hash(media,payload['attachment_index'])
        envelope = {'schema':SCHEMA, 'user_id':self.user, 'account_ref':account_ref,
                    'conversation_id':conv['id'],'peer_id':str(conv['peer_id']), 'channel':self._account(account_ref)['provider'],
                    'source_message_id':source_message_id, 'source_provider_message_id':projected['source']['provider_message_id'],
                    'source_hash':source_hash,'source_binding':projected['binding'],
                    'thread_id':projected['source']['thread_id'],
                    'action':action,'payload':payload,'media_hash':media_hash,'request_id':request_id}
        upload=None
        if 'upload_id' in payload:
            self._allow(account_ref,'file_upload')
            from .messenger_uploads import MessengerUploads,upload_binding
            upload=MessengerUploads(self.store,self.user)
            envelope['payload']={**payload,**upload.metadata(payload['upload_id'],upload_binding(envelope))}
        action_id = 'action_'+hashlib.sha256((self.user+'\0'+request_id).encode()).hexdigest()
        envelope['action_id'] = action_id
        action_hash = digest(envelope)
        self._init_actions()
        with self.store._lock, self.store._connection:
            self.store._connection.execute('BEGIN IMMEDIATE')
            row = self.store._connection.execute('SELECT hash FROM messenger_actions WHERE user_id=? AND id=?',
                                                (self.user,action_id)).fetchone()
            if row and row['hash']!=action_hash: raise ValueError('idempotency_conflict')
            if not row:
                if upload is not None: upload.bind(payload['upload_id'],action_id)
                self.store._connection.execute('INSERT INTO messenger_actions '
                    '(user_id,id,request_id,envelope,hash,status,created_at) VALUES (?,?,?,?,?,?,?)',
                    (self.user,action_id,request_id,canonical(envelope).decode(),action_hash,'proposed',time.time()))
        return self.get(action_id)

    @staticmethod
    def _telegram_admin_payload(operation, payload):
        if not isinstance(payload,dict): raise ValueError('payload_must_be_object')
        schemas={
            'group_create':{'title','participant_ids'},
            'group_invite':{'conversation_id','participant_id'},
            'group_remove':{'conversation_id','participant_id','ban_on_supergroup'},
            'channel_create':{'title','about'},
            'channel_delete':{'conversation_id','confirmation_phrase'},
        }
        if operation not in schemas or set(payload)-schemas[operation]:
            raise ValueError('invalid_telegram_admin_payload')
        value=dict(payload)
        if operation in {'group_create','channel_create'}:
            title=value.get('title')
            if not isinstance(title,str) or not title.strip() or len(title)>128: raise ValueError('title_required')
            value['title']=title.strip()
        if operation=='group_create':
            users=value.get('participant_ids')
            if not isinstance(users,list) or not 1<=len(users)<=50: raise ValueError('participant_ids_required')
            if any(not isinstance(v,str) or not re.fullmatch(r'[1-9][0-9]*',v) for v in users):
                raise ValueError('exact_telegram_user_ids_required')
            if len(set(users))!=len(users): raise ValueError('duplicate_participant')
            value['participant_ids']=users
        if operation=='channel_create':
            about=value.get('about','')
            if not isinstance(about,str) or len(about)>255: raise ValueError('invalid_channel_about')
            value={'title':value['title'].strip(),'about':about,'private':True,'broadcast':True}
        if operation in {'group_invite','group_remove','channel_delete'}:
            cid=value.get('conversation_id')
            if not isinstance(cid,str) or not cid: raise ValueError('conversation_id_required')
        if operation in {'group_invite','group_remove'}:
            uid=value.get('participant_id')
            if not isinstance(uid,str) or not re.fullmatch(r'[1-9][0-9]*',uid):
                raise ValueError('exact_telegram_user_id_required')
        if operation=='group_remove':
            if type(value.get('ban_on_supergroup',False)) is not bool: raise ValueError('ban_flag_must_be_boolean')
        if operation=='channel_delete':
            if not isinstance(value.get('confirmation_phrase'),str) or not value['confirmation_phrase']:
                raise ValueError('channel_delete_phrase_required')
        return value

    def propose_telegram_admin(self, account_ref, operation, payload, request_id):
        if operation not in TELEGRAM_ADMIN_OPERATIONS: raise ValueError('invalid_telegram_admin_operation')
        account=self._account(account_ref)
        caps=self.capabilities(account_ref)['telegram_admin']
        if not caps['allowed']: raise PermissionError(caps['reason'])
        if account['provider']!='telegram' or not account['enabled']:
            raise PermissionError('telegram_account_required')
        if not isinstance(request_id,str) or not re.fullmatch(r'[a-zA-Z0-9:_-]{16,128}',request_id):
            raise ValueError('request_id_required')
        value=self._telegram_admin_payload(operation,payload)
        self._init_actions()
        with self.store._lock:
            existing=self.store._connection.execute('SELECT id,envelope FROM messenger_actions WHERE user_id=? AND request_id=?',(self.user,request_id)).fetchone()
        if existing:
            old=json.loads(existing['envelope'])
            old_payload={k:v for k,v in old.get('payload',{}).items() if k not in {'prepared','peer_id','conversation_kind'}}
            if old.get('account_ref')!=account_ref or old.get('operation')!=operation or old_payload!=value:
                raise ValueError('idempotency_conflict')
            return self.get(existing['id'])
        conversation=None
        if 'conversation_id' in value:
            conversation=self._conversation(account_ref,value['conversation_id'])
            if conversation.get('source')!='telegram': raise ValueError('telegram_conversation_required')
            value['peer_id']=str(conversation['peer_id'])
            value['conversation_kind']=str(conversation.get('conversation_kind') or 'unknown')
        outbox=getattr(self.service,'telegram_outbox',None)
        try:
            prepared=outbox.telegram_admin_prepare(account_ref=account_ref,operation=operation,payload=value)
        except DeliveryNotSubmittedError as error:
            raise ValueError('telegram_admin_preflight_refused') from error
        except (OSError,RuntimeError) as error:
            raise ValueError('telegram_admin_preflight_unavailable') from error
        if (not isinstance(prepared,dict) or prepared.get('account_id')!=account_ref
                or prepared.get('operation')!=operation):
            raise ValueError('telegram_admin_preflight_identity_conflict')
        value['prepared']=prepared.get('resolved',{})
        if operation=='channel_delete':
            peer=str(value.get('peer_id') or '')
            expected=f'DELETE CHANNEL {peer}'
            if value['confirmation_phrase']!=expected: raise ValueError('channel_delete_phrase_mismatch')
            if value.get('conversation_kind')!='channel': raise ValueError('broadcast_channel_required')
        if operation in {'group_invite','group_remove'} and value.get('conversation_kind') not in {'group','channel'}:
            raise ValueError('group_or_supergroup_required')
        envelope={'schema':SCHEMA,'user_id':self.user,'account_ref':account_ref,'channel':'telegram',
            'conversation_id':conversation['id'] if conversation else '', 'peer_id':str(value.get('peer_id') or ''),
            'action':'telegram_admin','operation':operation,'payload':value,'request_id':request_id}
        action_id='action_'+hashlib.sha256((self.user+'\0'+request_id).encode()).hexdigest()
        envelope['action_id']=action_id
        action_hash=digest(envelope)
        self._init_actions()
        with self.store._lock,self.store._connection:
            self.store._connection.execute('BEGIN IMMEDIATE')
            row=self.store._connection.execute('SELECT hash FROM messenger_actions WHERE user_id=? AND id=?',(self.user,action_id)).fetchone()
            if row and row['hash']!=action_hash: raise ValueError('idempotency_conflict')
            if not row:
                self.store._connection.execute('INSERT INTO messenger_actions (user_id,id,request_id,envelope,hash,status,created_at) VALUES (?,?,?,?,?,?,?)',
                    (self.user,action_id,request_id,canonical(envelope).decode(),action_hash,'proposed',time.time()))
        return self.get(action_id)

    def get(self, action_id):
        self._init_actions()
        with self.store._lock:
            row = self.store._connection.execute('SELECT * FROM messenger_actions WHERE user_id=? AND id=?',
                                                (self.user,action_id)).fetchone()
        if row is None: raise KeyError('action_not_found')
        e = json.loads(row['envelope'])
        self._allow(e['account_ref'],'read')
        return {'id':row['id'],'hash':row['hash'],'status':row['status'], 'created_at':row['created_at'],
                'envelope':e,'receipt':json.loads(row['receipt']) if row['receipt'] else None}

    def approve(self, action_id, expected_hash, confirm):
        if confirm is not True: raise ValueError('exact_confirmation_required')
        action = self.get(action_id); e = action['envelope']
        if expected_hash != action['hash']: raise ValueError('action_snapshot_conflict')
        if e.get('action')=='telegram_admin':
            if not self.capabilities(e['account_ref'])['telegram_admin']['allowed']:
                raise PermissionError(self.capabilities(e['account_ref'])['telegram_admin']['reason'])
        else:
            self._allow(e['account_ref'],e['action'])
        if action['status']=='accepted': return action
        admin_recovery=(action['status']=='sending' and e.get('action')=='telegram_admin')
        if action['status']=='sending' and not admin_recovery: raise ValueError('delivery_uncertain_do_not_retry')
        if e['action']=='telegram_admin':
            provider=self._provider(e['account_ref'])
            if provider is None or getattr(provider,'account_ref',None)!=e['account_ref']:
                raise ValueError('provider_account_conflict')
            if e.get('conversation_id'):
                conv=self._conversation(e['account_ref'],e['conversation_id'])
                if str(conv.get('peer_id'))!=e['peer_id'] or str(conv.get('conversation_kind'))!=e['payload'].get('conversation_kind'):
                    raise ValueError('telegram_admin_target_changed')
            if not admin_recovery:
                with self.store._lock,self.store._connection:
                    self.store._connection.execute('BEGIN IMMEDIATE')
                    changed=self.store._connection.execute("UPDATE messenger_actions SET status='sending' WHERE user_id=? AND id=? AND status='proposed'",(self.user,action_id)).rowcount
                    if changed!=1: raise ValueError('delivery_claim_conflict')
            try:
                result=provider.execute_telegram_admin(e)
                if (not isinstance(result,dict) or result.get('account_ref')!=e['account_ref']
                        or result.get('operation')!=e['operation'] or not result.get('receipt')
                        or result.get('status') not in {'complete','partial'}):
                    raise RuntimeError('telegram_admin_receipt_identity_conflict')
                with self.store._lock,self.store._connection:
                    self.store._connection.execute("UPDATE messenger_actions SET status='accepted',receipt=? WHERE user_id=? AND id=? AND status='sending'",(canonical(result).decode(),self.user,action_id))
            except DeliveryNotSubmittedError:
                if admin_recovery: raise
                with self.store._lock,self.store._connection:
                    self.store._connection.execute("UPDATE messenger_actions SET status='proposed' WHERE user_id=? AND id=? AND status='sending'",(self.user,action_id))
                raise
            projector=getattr(provider,'project_receipt',None)
            if callable(projector):
                try: projector(e,result)
                except Exception: pass
            return self.get(action_id)
        conv = self._conversation(e['account_ref'],e['conversation_id'])
        if str(conv['peer_id'])!=e['peer_id'] or not self.store.route_allowed(
                user_id=self.user,source=conv['source'],route_id=conv['route_id']):
            raise ValueError('route_or_peer_conflict')
        provider = self._provider(e['account_ref'])
        if provider is None or getattr(provider,'account_ref',None)!=e['account_ref']:
            raise ValueError('provider_account_conflict')
        with self.store._lock, self.store._connection:
            # Lock the writer before reading source/claiming: a concurrent
            # source update cannot slip between the snapshot and the claim.
            self.store._connection.execute('BEGIN IMMEDIATE')
            self._source_still_current(conv,e)
            changed = self.store._connection.execute("UPDATE messenger_actions SET status='sending' "
                "WHERE user_id=? AND id=? AND status='proposed'",(self.user,action_id)).rowcount
            if changed!=1: raise ValueError('delivery_claim_conflict')
        try:
            result = provider.execute(e)
            if (not isinstance(result,dict) or result.get('account_ref')!=e['account_ref']
                    or str(result.get('peer_id'))!=e['peer_id'] or not result.get('receipt')):
                raise RuntimeError('provider_receipt_identity_conflict')
            mid = result.get('provider_message_id')
            valid_id = isinstance(mid,str) and bool(re.fullmatch(r'[1-9][0-9]*',mid))
            if e['channel']=='gmail':
                from .messenger_email import mail_id
                valid_id = mail_id(mid) and '@' in mid
            if e['action'] in {'reply','edit','sticker','file'} and not valid_id:
                raise RuntimeError('provider_receipt_id_required')
            if e['action']=='edit' and mid!=e['source_provider_message_id']:
                raise RuntimeError('edit_receipt_id_conflict')
            with self.store._lock, self.store._connection:
                self.store._connection.execute("UPDATE messenger_actions SET status='accepted',receipt=? "
                    "WHERE user_id=? AND id=? AND status='sending'",(canonical(result).decode(),self.user,action_id))
                if 'upload_id' in e['payload']:
                    self.store._connection.execute('UPDATE messenger_uploads SET data=NULL WHERE user_id=? AND id=?',(self.user,e['payload']['upload_id']))
        except DeliveryNotSubmittedError:
            with self.store._lock, self.store._connection:
                self.store._connection.execute("UPDATE messenger_actions SET status='proposed' WHERE user_id=? AND id=? AND status='sending'",
                                               (self.user,action_id))
            raise
        projector = getattr(provider,'project_receipt',None)
        if callable(projector):
            try: projector(e,result)
            except Exception:
                # Acceptance is already durable. A mirror failure may be
                # reconciled by a read; it must never turn into a new write.
                pass
        # Other failures keep the durable sending state. Never infer rejection
        # from a timeout or retry a mutation that could have reached a provider.
        return self.get(action_id)


def dispatch(store, service, principal, name, arguments):
    api = Messenger(store,service,principal)
    op = name.removeprefix('userio.messenger.')
    try:
        if principal.read_only and op not in {'capabilities','history','get','resolve','media','events'}:
            raise PermissionError('read_only_token')
        if op == 'capabilities': return {'ok':True,**api.capabilities(**arguments)}
        if op == 'history': return {'ok':True,**api.history(**arguments)}
        if op == 'resolve': return {'ok':True,**api.resolve(**arguments)}
        if op == 'media': return {'ok':True,**api.media(**arguments)}
        if op == 'events': return {'ok':True,**api.events(**arguments)}
        if op == 'upload': return {'ok':True,'upload':api.upload(**arguments)}
        if op == 'propose': return {'ok':True,'action':api.propose(**arguments)}
        if op == 'telegram_admin.propose': return {'ok':True,'action':api.propose_telegram_admin(**arguments)}
        if op == 'get': return {'ok':True,'action':api.get(**arguments)}
        if op == 'approve': return {'ok':True,'action':api.approve(**arguments)}
        raise KeyError('unknown_messenger_operation')
    except ProviderAuthenticationRequiredError as error:
        return {'ok':False,'error':'provider_authentication_required',
                'message':error.public_message,'reconnect_required':True}
    except DeliveryNotSubmittedError:
        return {'ok':False,'error':'provider_not_submitted',
                'message':'Операция отклонена до отправки. Проверьте статус того же действия.',
                'action_status':'proposed'}
    except (KeyError, ValueError, PermissionError) as error:
        return {'ok':False,'error':str(error).strip("'")}
    except Exception as error:
        # Safe diagnostic location only: never log provider bodies, tokens or
        # signed URLs. Preserve the durable outcome rather than hiding where
        # an unexpected read/projection error occurred.
        import logging, os
        tb=error.__traceback__
        while tb and tb.tb_next: tb=tb.tb_next
        logging.getLogger(__name__).warning('Messenger %s failed at %s:%s (%s)',op,
            os.path.basename(tb.tb_frame.f_code.co_filename) if tb else 'unknown',
            tb.tb_lineno if tb else 0,type(error).__name__)
        return {'ok':False,'error':'provider_result_unknown_read_action_status'}
