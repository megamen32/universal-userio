"""Generic provider HTTP route on a currently authorized client, no raw client."""
from __future__ import annotations

import hashlib
import base64
import hmac
import json
import time
import urllib.request
import urllib.error
from pathlib import Path

from .messenger import canonical, digest, OPERATIONS
from .contracts import InboxMessage, DeliveryNotSubmittedError

APPROVAL_SCHEMA = 'userio.messenger.approved-action.v1'


class RelayMessengerProvider:
    def __init__(self, *, service, base_url, token_file, approval_key,
                 account_ref, authorized_user, opener=None):
        if not account_ref.startswith('telegram:') or not account_ref.split(':')[-1].isdigit():
            raise ValueError('exact relay account required')
        self.service=service; self.base_url=base_url.rstrip('/'); self.token_file=token_file
        self.key=approval_key.encode(); self.account_ref=account_ref
        self.authorized_user=authorized_user
        self.opener=opener or urllib.request.build_opener(urllib.request.ProxyHandler({})).open
        self._caps={}; self._caps_at=0.0

    def _request(self, method, path, payload=None):
        token=Path(self.token_file).read_text().strip()
        if not token: raise ValueError('relay credential unavailable')
        req=urllib.request.Request(self.base_url+path,method=method,
            data=None if payload is None else canonical(payload),
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        try:
            with self.opener(req,timeout=10) as response:
                raw=response.read(3_500_001)
                if len(raw)>3_500_000: raise ValueError('provider response exceeds limit')
                result=json.loads(raw)
        except urllib.error.HTTPError as error:
            if path=='/userio/messenger/execute':
                try: refusal=json.loads(error.read(4096))
                except (ValueError,OSError): refusal={}
                if (error.code in {400,401,403,404,413}
                        or error.code==409 and refusal.get('error')=='source_or_action_conflict'):
                    raise DeliveryNotSubmittedError('provider refused before mutation') from None
            raise
        if not isinstance(result,dict) or result.get('ok') is False or result.get('error'):
            raise ValueError('provider refused operation')
        return result

    def _capabilities(self):
        if time.monotonic()-self._caps_at<10: return self._caps
        result=self._request('GET','/userio/messenger/capabilities')
        if result.get('account_ref')!=self.account_ref or str(result.get('sender_id'))!=self.account_ref.split(':')[-1]:
            raise ValueError('relay sender identity conflict')
        if not isinstance(result.get('operations'),list) or any(op not in OPERATIONS for op in result['operations']):
            raise ValueError('invalid provider capabilities')
        self._caps=result; self._caps_at=time.monotonic()
        return result

    @property
    def operations(self):
        try: return frozenset(self._capabilities()['operations'])
        except (OSError,ValueError,KeyError): return frozenset()

    @property
    def sender_id(self): return str(self._caps.get('sender_id') or '')

    def message(self, *, peer_id, provider_message_id, conversation_id, read_media=False, attachment_index=0):
        result=self._request('POST','/userio/messenger/message',{'account_ref':self.account_ref,
            'peer_id':peer_id,'provider_message_id':provider_message_id,'conversation_id':conversation_id,
            'read_media':read_media,'attachment_index':attachment_index})
        p=self._validated_projection(result.get('message'),peer_id,conversation_id)
        if p['source']['provider_message_id']!=provider_message_id:
            raise ValueError('provider message identity conflict')
        return p

    def history(self, *, peer_id, conversation_id, limit=50, cursor=''):
        result=self._request('POST','/userio/messenger/history',{'account_ref':self.account_ref,
            'peer_id':peer_id,'conversation_id':conversation_id,'limit':limit,'cursor':cursor})
        rows=result.get('messages')
        if not isinstance(rows,list) or len(rows)>limit: raise ValueError('invalid provider history')
        return {'messages':[self._validated_projection(m,peer_id,conversation_id) for m in rows],
                'next_cursor':str(result.get('next_cursor') or ''),'partial':result.get('partial') is not False}

    def _validated_projection(self, projected, peer_id, conversation_id):
        if not isinstance(projected,dict): raise ValueError('actual provider source missing')
        s=projected.get('source') or {};a=projected.get('author') or {}
        if (s.get('account_ref'),s.get('peer_id'),s.get('conversation_id'))!=(self.account_ref,peer_id,conversation_id):
            raise ValueError('provider projection scope conflict')
        raw_id=s.get('provider_message_id')
        if (not isinstance(raw_id,str) or not raw_id.isdigit()
                or s.get('message_id')!=self.account_ref+'|'+peer_id+':'+raw_id
                or s.get('direction') not in {'incoming','outgoing'} or not a.get('id')
                or not isinstance(projected.get('text'),str)
                or type(projected.get('at')) not in (int,float) or projected['at']<=0):
            raise ValueError('invalid provider projection origin')
        return projected

    def import_projection(self, projected, *, peer_id, conversation_id, kind='group'):
        p=self._validated_projection(projected,peer_id,conversation_id);s=p['source'];a=p['author']
        routing={ 'author_id':str(a['id']), 'author_name':str(a.get('name') or ''),
                 'author_username':str(a.get('username') or ''),
                 'author_first_name':str(a.get('first_name') or ''),'author_last_name':str(a.get('last_name') or '')}
        invocation=s.get('invocation') if isinstance(s.get('invocation'),dict) else {}
        routing.update(is_exact_account_mention=invocation.get('mention') is True,
                       is_reply_to_account=invocation.get('reply') is True)
        attachments=[]
        for item in p.get('media') or []:
            # Only source-bound media references, never tokens or provider URL.
            attachments.append({k:item[k] for k in ('kind','filename','content_type','size','attachment_id','idx') if k in item})
        # Attachment storage is keyed by idx. Routing metadata must follow the
        # native media slots rather than overwriting the default slot zero.
        routing_idx=max((int(item.get('idx') or 0) for item in attachments),default=-1)+1
        attachments.append({'kind':'telegram_routing','idx':routing_idx,
                            'provider_ref':json.dumps(routing)})
        msg=InboxMessage(source='telegram',message_id=s['message_id'],sender=str(a['id']),
            body=p['text'],received_at=p['at'],direction=s['direction'],peer_id=peer_id,
            conversation_kind=kind,reconciliation=True,attachments=tuple(attachments),
            edited_at=p.get('edited_at') or 0,provider_reply_to=s.get('reply_to_provider_message_id') or '',
            provider_thread_id=s.get('thread_id') or '')
        cid,_=self.service.receive(msg,route_id='telegram',user_id=self.authorized_user,account_ref=self.account_ref)
        if cid!=conversation_id: raise ValueError('canonical conversation identity conflict')
        return cid

    def execute(self, envelope):
        if envelope['account_ref']!=self.account_ref or envelope['user_id']!=self.authorized_user:
            raise PermissionError('relay account/user conflict')
        action=self.service._store._connection.execute('SELECT hash,status FROM messenger_actions WHERE user_id=? AND id=?',
            (self.authorized_user,envelope['action_id'])).fetchone()
        if not action or action['status']!='sending' or action['hash']!=digest(envelope):
            raise ValueError('authoritative durable approval missing')
        now=int(time.time())
        approval={'schema':APPROVAL_SCHEMA,'account_ref':self.account_ref,'peer_id':envelope['peer_id'],
            'action_id':envelope['action_id'],'user_id':self.authorized_user,'approved_by':self.authorized_user,
            'conversation_id':envelope['conversation_id'],'source_provider_message_id':envelope['source_provider_message_id'],
            'thread_id':envelope['thread_id'],'action':envelope['action'],
            'payload_sha256':digest(envelope['payload']),'source_sha256':envelope['source_hash'],
            'envelope_sha256':digest(envelope),
            'idempotency_key':'userio:'+self.authorized_user+':'+envelope['action_id'],
            'issued_at':now,'expires_at':now+300}
        proof=hmac.new(self.key,(APPROVAL_SCHEMA+'\n').encode()+canonical(approval),hashlib.sha256).hexdigest()
        packet={'envelope':envelope,'approval':approval,'proof':proof}
        if 'upload_id' in envelope['payload']:
            from .messenger_uploads import MessengerUploads
            data=MessengerUploads(self.service._store,envelope['user_id']).data(envelope)
            packet['upload']={'data_base64':base64.b64encode(data).decode()}
        return self._request('POST','/userio/messenger/execute',packet)

    def media(self, projected, *, attachment_index=0):
        s=projected['source']
        p=self.message(peer_id=s['peer_id'],provider_message_id=s['provider_message_id'],
                       conversation_id=s['conversation_id'],read_media=True,attachment_index=attachment_index)
        if p['source']['provider_message_id']!=s['provider_message_id']:
            raise ValueError('provider media source conflict')
        content=p.get('content')
        if not isinstance(content,dict): raise ValueError('provider media unavailable')
        image=content.get('image')
        if image is not None:
            if not isinstance(image,dict) or not isinstance(image.get('dataBase64'),str) or len(image['dataBase64'])>2_800_000:
                raise ValueError('provider image exceeds bounded limit')
            data=base64.b64decode(image['dataBase64'],validate=True)
            if not data or len(data)>2*1024*1024 or image.get('mimeType') not in {'image/png','image/jpeg','image/gif','image/webp'}:
                raise ValueError('invalid provider image')
            if content.get('sha256') and content['sha256']!=hashlib.sha256(data).hexdigest():
                raise ValueError('provider media digest conflict')
        transcript=content.get('transcript')
        if transcript is not None and (not isinstance(transcript,str) or len(transcript)>32000):
            raise ValueError('provider transcript exceeds bounded limit')
        return {k:content[k] for k in ('kind','status','text','nextTextOffset','image','transcript','mimeType','bytes','sha256') if k in content}

    def project_receipt(self,e,result):
        if e['action'] not in {'reply','edit','sticker','file'}: return
        p=self.message(peer_id=e['peer_id'],provider_message_id=result['provider_message_id'],conversation_id=e['conversation_id'])
        conv=self.service._store.conversation(e['conversation_id'],user_id=self.authorized_user)
        self.import_projection(p,peer_id=e['peer_id'],conversation_id=e['conversation_id'],kind=conv['conversation_kind'])
