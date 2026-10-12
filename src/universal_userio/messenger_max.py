"""Generic actions on the existing authorized MAX client; no new session."""
from __future__ import annotations

import hashlib
import base64
import time
import json

from .adapters import inbox_message_from_envelope
from .max_ingress import message_envelope, _own_id
from .messenger import digest
from .contracts import DeliveryNotSubmittedError


class MaxMessengerProvider:
    typing_stop_mode = 'expires_automatically'
    account_ref = 'max'
    operations = frozenset({'reply','edit','react','sticker','file','file_upload','typing','download','history'})

    def __init__(self, service):
        self.service=service; self.client=service.max_client
        self.authorized_user=service.max_user_id

    @property
    def sender_id(self): return _own_id(self.client)

    @property
    def authentication_required(self):
        return getattr(self.client, 'authentication_required', False) is True

    def _projection(self, row, peer_id, conversation_id, kind='unknown'):
        env=message_envelope(row,chat_id=peer_id,own_id=self.sender_id,kind=kind,reconciliation=True)
        if env is None: raise ValueError('actual MAX source unavailable')
        return {'source':{'account_ref':self.account_ref,'peer_id':peer_id,'conversation_id':conversation_id,
                'message_id':env['message_id'],'provider_message_id':str(row['id']),
                'channel':'max','direction':env['direction'],'conversation_kind':kind,
                'thread_id':'','reply_to_provider_message_id':env['provider_reply_to'],
                'invocation':{'mention':False,'reply':False}},
                'author':{'id':env['sender'],'name':'','username':'','role':'owner' if env['direction']=='outgoing' else 'interlocutor'},
                'text':env['body'],'at':env['received_at'],'edited_at':env['edited_at'],
                'media':[dict(a,message_id=env['message_id'],attachment_index=i,
                              attachment_id=str(a.get('attachment_id') or '')) for i,a in enumerate(env['attachments'])]}

    def message(self, *, peer_id, provider_message_id, conversation_id):
        row=self.client.get_message(chat_id=peer_id,message_id=provider_message_id)
        if not row or str(row.get('id'))!=provider_message_id:raise ValueError('actual MAX source unavailable')
        chat=self.client.get_chat(peer_id)
        kind='group' if chat.get('type') in {'CHAT','GROUP'} else 'channel' if chat.get('type')=='CHANNEL' else 'direct' if chat.get('type')=='DIALOG' else 'unknown'
        projected=self._projection(row,peer_id,conversation_id,kind)
        try:
            projected['reaction_info']=self.client.reactions(chat_id=peer_id,message_id=provider_message_id)
            projected['reaction_readback']={'status':'ready'}
        except Exception:
            # An auxiliary reaction read must not make normal text/media
            # inaccessible. Its unavailable status must not become a pass.
            projected['reaction_readback']={'status':'unavailable',
                                           'reason':'provider_reaction_readback_unavailable'}
        return projected

    def import_projection(self, projected, *, peer_id, conversation_id, kind='unknown'):
        s=projected['source'];a=projected['author']
        if (s['account_ref'],s['peer_id'],s['conversation_id'])!=(self.account_ref,peer_id,conversation_id):
            raise ValueError('MAX projection scope conflict')
        msg=inbox_message_from_envelope({'schema':'universal.inbox.message.v1','source':'max',
            'message_id':s['message_id'],'sender':a['id'],'sender_name':a.get('name') or '',
            'body':projected['text'],'received_at':projected['at'],'direction':s['direction'],
            'edited_at':projected['edited_at'],'peer_id':peer_id,'conversation_kind':kind,
            'provider_reply_to':s['reply_to_provider_message_id'],'attachments':projected['media'],
            'reconciliation':True},received_at=time.time())
        cid,_=self.service.receive(msg,route_id='max',user_id=self.authorized_user,account_ref=self.account_ref)
        if cid!=conversation_id:raise ValueError('MAX conversation identity conflict')

    def history(self, *, peer_id, conversation_id, limit=50, cursor=''):
        before=None
        if cursor:
            try:
                c=json.loads(base64.urlsafe_b64decode(cursor))
                if (c['account_ref'],c['conversation_id'],c['peer_id'])!=(self.account_ref,conversation_id,peer_id):
                    raise ValueError('MAX cursor scope conflict')
                before=c['before']
                if type(before) is not int or before<=0:raise ValueError('invalid MAX cursor')
            except (ValueError,TypeError,KeyError):raise ValueError('invalid MAX cursor') from None
        # Native `from` is inclusive: on continuation its boundary record
        # consumes one slot before we exclude it. Keep another slot for the
        # lookahead, including requests at the native 100-record ceiling.
        page_limit=min(limit,98)
        rows=self.client.history(peer_id,backward=page_limit+2,before=before)
        rows=sorted((r for r in rows if before is None or r.get('time',0)<before),key=lambda r:r.get('time',0),reverse=True)
        page=rows[:page_limit];next_cursor=''
        if len(rows)>page_limit and page:
            oldest=page[-1]['time']
            # Timestamp-only native pagination cannot certify tied timestamps.
            if rows[page_limit].get('time')!=oldest:
                next_cursor=base64.urlsafe_b64encode(json.dumps({'account_ref':self.account_ref,'conversation_id':conversation_id,
                    'peer_id':peer_id,'before':oldest}).encode()).decode()
        return {'messages':[self._projection(r,peer_id,conversation_id) for r in reversed(page)],
                'next_cursor':next_cursor,'partial':True,'more_stored':False,'page_limit':page_limit}

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
        source=projected['source']
        row=self.client.get_message(chat_id=source['peer_id'],message_id=source['provider_message_id'])
        native=self._native_binding({'peer_id':source['peer_id']},row)
        expected={k:source[k] for k in ('account_ref','peer_id','provider_message_id','direction','thread_id','reply_to_provider_message_id')}
        expected.update(author_id=projected['author']['id'],edited_at=int(projected.get('edited_at') or 0),
                        text_sha256=hashlib.sha256(projected['text'].encode()).hexdigest())
        attaches=row.get('attaches') or []
        if digest(native)!=digest(expected) or attachment_index>=len(attaches):
            raise ValueError('MAX actual media source changed')
        if attaches[attachment_index].get('_type')=='STICKER':
            file=MaxChannelAdapter(self.service._store,self.service,self.authorized_user).download_for_ai(
                file_ref=source['message_id'],max_bytes=2*1024*1024,attachment_idx=attachment_index)
            return {'image':{'mimeType':file.content_type,'dataBase64':base64.b64encode(file.data).decode()},
                    'sha256':hashlib.sha256(file.data).hexdigest(),'bytes':len(file.data),
                    'mimeType':file.content_type,'kind':'sticker','status':'ready','partial':False}
        from .messenger_max_media import read_attachment
        return read_attachment(self.client,peer=source['peer_id'],message_id=source['provider_message_id'],
                               attachment=attaches[attachment_index])

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
        elif action=='file' and 'upload_id' in p:
            from .messenger_uploads import MessengerUploads
            # Verification before any provider upload. Failures after this
            # point remain UNKNOWN in the ordinary action journal.
            try: data=MessengerUploads(self.service._store,e['user_id']).data(e)
            except Exception: raise DeliveryNotSubmittedError('MAX staged bytes unavailable before upload') from None
            attachment=self.client.upload_file(data=data,filename=p['filename'],mime_type=p['mime_type'])
            try:
                sent=self.client.send_bound(chat_id=e['peer_id'],reply_to=raw_id,cid=cid,
                    text=p.get('caption',''),attaches=[attachment])
            except DeliveryNotSubmittedError:
                # A fresh write connection may fail before SEND, but the
                # preceding upload has already reached the provider.
                raise RuntimeError('MAX upload accepted, send outcome incomplete; do not retry') from None
            result={'provider_message_id':sent['message_id'],'receipt':sent['receipt']+':'+e['action_id'],
                    'upload_sha256':p['sha256'],'upload_size':p['size']}
        elif action in {'sticker','file'}:
            try:
                conv=self.service._store.conversation(e['conversation_id'],user_id=e['user_id'])
                stored=self.service._store.message(p['media_message_id'],source='max',user_id=e['user_id'])
                prefix='max-'+e['peer_id']+'-'
                if not stored or stored['conversation_id']!=conv['id'] or not p['media_message_id'].startswith(prefix):
                    raise ValueError('owned media source required')
                media=self.client.get_message(chat_id=e['peer_id'],message_id=p['media_message_id'][len(prefix):])
                attaches=media.get('attaches') or []
                idx=p['attachment_index']
                if idx>=len(attaches): raise ValueError('MAX media source missing')
                att=attaches[idx];typ=att.get('_type')
                native_id=att.get('stickerId') if typ=='STICKER' else att.get('photoId') if typ=='PHOTO' else att.get('fileId') if typ=='FILE' else None
                if native_id is None: raise ValueError('MAX native media reference unsupported')
                media_binding={'source_binding':self._native_binding(e,media),
                               'attachment_id':str(native_id),'attachment_index':idx}
                if digest(media_binding)!=e['media_hash']: raise ValueError('MAX actual media changed')
                if action=='sticker':
                    if typ!='STICKER' or not str(att.get('stickerId') or '').isdigit() or not str(att.get('setId') or '').isdigit():
                        raise ValueError('MAX sticker identifier unavailable')
                    send_args={'text':'','sticker':att}
                elif typ=='PHOTO' and isinstance(att.get('photoToken'),str) and att['photoToken']:
                    send_args={'text':p.get('caption',''),'attaches':[{'_type':'PHOTO','photoToken':att['photoToken']}]}
                elif typ=='FILE' and str(att.get('fileId') or '').isdigit():
                    if int(att.get('size') or att.get('fileSize') or 0)>20*1024*1024:
                        raise ValueError('MAX file exceeds provider budget')
                    send_args={'text':p.get('caption',''),'attaches':[{'_type':'FILE','fileId':int(att['fileId'])}]}
                else: raise ValueError('MAX native file reference unavailable')
            except Exception:
                raise DeliveryNotSubmittedError('MAX media reference refused before mutation') from None
            sent=self.client.send_bound(chat_id=e['peer_id'],reply_to=raw_id,cid=cid,**send_args)
            result={'provider_message_id':sent['message_id'],'receipt':sent['receipt']+':'+e['action_id']}
        else: raise ValueError('MAX operation unsupported')
        return {'account_ref':self.account_ref,'peer_id':e['peer_id'],**result}

    def project_receipt(self,e,result):
        if e['action'] not in {'reply','edit','sticker','file'}: return
        row=self.client.get_message(chat_id=e['peer_id'],message_id=result['provider_message_id'])
        conv=self.service._store.conversation(e['conversation_id'],user_id=e['user_id'])
        envelope=message_envelope(row,chat_id=e['peer_id'],own_id=self.sender_id,
                                  kind=conv['conversation_kind'],reconciliation=True)
        if envelope is None: raise ValueError('accepted MAX projection unavailable')
        self.service.receive(inbox_message_from_envelope(envelope,received_at=time.time()),
            route_id=conv['route_id'],user_id=e['user_id'],account_ref=self.account_ref)
