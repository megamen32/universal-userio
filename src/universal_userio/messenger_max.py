"""Generic actions on the existing authorized MAX client; no new session."""
from __future__ import annotations

import hashlib
import base64
import time

from .adapters import inbox_message_from_envelope
from .max_ingress import message_envelope, _own_id
from .messenger import digest
from .contracts import DeliveryNotSubmittedError


class MaxMessengerProvider:
    account_ref = 'max'
    operations = frozenset({'reply','edit','react','sticker','typing','download'})

    def __init__(self, service):
        self.service=service; self.client=service.max_client
        self.authorized_user=service.max_user_id

    @property
    def sender_id(self): return _own_id(self.client)

    def _native_binding(self, e, row):
        envelope=message_envelope(row,chat_id=e['peer_id'],own_id=self.sender_id,
                                  reconciliation=True)
        if envelope is None: raise ValueError('provider_source_not_found')
        return {'account_ref':self.account_ref,'peer_id':e['peer_id'],
                'provider_message_id':str(row['id']),'author_id':str(row['sender']),
                'direction':envelope['direction'], 'text_sha256':hashlib.sha256(str(row.get('text') or '').encode()).hexdigest(),
                'thread_id':'','reply_to_provider_message_id':envelope['provider_reply_to'],
                'edited_at':int(envelope.get('edited_at') or 0)}

    def media(self, projected, *, attachment_index=0):
        from .adapters import MaxChannelAdapter
        file=MaxChannelAdapter(self.service._store,self.service,self.authorized_user).download_for_ai(
            file_ref=projected['source']['message_id'],max_bytes=2*1024*1024,attachment_idx=attachment_index)
        return {'image':{'mimeType':file.content_type,'dataBase64':base64.b64encode(file.data).decode()},
                'sha256':hashlib.sha256(file.data).hexdigest(),'bytes':len(file.data),
                'mimeType':file.content_type,'kind':'sticker','status':'ready','partial':False}

    def execute(self, e):
        if e['account_ref']!=self.account_ref or e['user_id']!=self.service.max_user_id:
            raise PermissionError('MAX account route mismatch')
        try:
            row=self.client.get_message(chat_id=e['peer_id'],message_id=e['source_provider_message_id'])
            if not row or not self.sender_id or digest(self._native_binding(e,row))!=e['source_hash']:
                raise ValueError('MAX source changed before dispatch')
        except Exception:
            raise DeliveryNotSubmittedError('MAX source could not be verified; no mutation submitted') from None
        p,action=e['payload'],e['action']
        raw_id=e['source_provider_message_id']
        cid=int(hashlib.sha256(e['action_id'].encode()).hexdigest()[:13],16)
        if action=='edit':
            edited=self.client.edit(chat_id=e['peer_id'],message_id=raw_id,
                text=p['text'],expected_text=p['expected_text'])
            result={'provider_message_id':str(edited['id']),'receipt':'max-ws-edit-accepted:'+e['action_id']}
        elif action=='reply':
            sent=self.client.send_bound(chat_id=e['peer_id'],text=p['text'],reply_to=raw_id,cid=cid)
            result={'provider_message_id':sent['message_id'],'receipt':sent['receipt']+':'+e['action_id']}
        elif action=='react':
            self.client.react(chat_id=e['peer_id'],message_id=raw_id,emoji=p['emoji'])
            result={'provider_message_id':raw_id,'receipt':'max-ws-reaction-accepted:'+e['action_id']}
        elif action=='typing':
            self.client.typing(chat_id=e['peer_id'],active=p['active'])
            result={'provider_message_id':raw_id,'receipt':'max-ws-typing:'+e['action_id']}
        elif action=='sticker':
            conv=self.service._store.conversation(e['conversation_id'],user_id=e['user_id'])
            stored=self.service._store.message(p['media_message_id'],source='max',user_id=e['user_id'])
            prefix='max-'+e['peer_id']+'-'
            if not stored or stored['conversation_id']!=conv['id'] or not p['media_message_id'].startswith(prefix):
                raise ValueError('owned sticker source required')
            media=self.client.get_message(chat_id=e['peer_id'],message_id=p['media_message_id'][len(prefix):])
            attaches=media.get('attaches') or []
            idx=p['attachment_index']
            if idx>=len(attaches) or attaches[idx].get('_type')!='STICKER':
                raise ValueError('MAX sticker source missing')
            att=attaches[idx]
            if not str(att.get('stickerId') or '').isdigit() or not str(att.get('setId') or '').isdigit():
                raise ValueError('MAX sticker identifier unavailable')
            media_binding={'source_binding':self._native_binding(e,media),
                           'attachment_id':str(att['stickerId']),'attachment_index':idx}
            if digest(media_binding)!=e['media_hash']:
                raise DeliveryNotSubmittedError('MAX actual sticker changed before dispatch')
            sent=self.client.send_bound(chat_id=e['peer_id'],text='',reply_to=raw_id,cid=cid,sticker=att)
            result={'provider_message_id':sent['message_id'],'receipt':sent['receipt']+':'+e['action_id']}
        else: raise ValueError('MAX operation unsupported')
        return {'account_ref':self.account_ref,'peer_id':e['peer_id'],**result}

    def project_receipt(self,e,result):
        if e['action'] not in {'reply','edit','sticker'}: return
        row=self.client.get_message(chat_id=e['peer_id'],message_id=result['provider_message_id'])
        conv=self.service._store.conversation(e['conversation_id'],user_id=e['user_id'])
        envelope=message_envelope(row,chat_id=e['peer_id'],own_id=self.sender_id,
                                  kind=conv['conversation_kind'],reconciliation=True)
        if envelope is None: raise ValueError('accepted MAX projection unavailable')
        self.service.receive(inbox_message_from_envelope(envelope,received_at=time.time()),
            route_id=conv['route_id'],user_id=e['user_id'],account_ref=self.account_ref)
