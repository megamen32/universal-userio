"""Read-only fresh pages from the existing, account-owned GramJS mirror.

No new client, session, read acknowledgement or mutation route is created.
Only IDs returned by the just-completed native page are projected as fresh.
"""
import base64
import json
import os
import time
import urllib.request

from .account_connections import _NoRedirect
from .messenger import Messenger, canonical
from .contracts import DeliveryNotSubmittedError, InboxMessage, UserPrincipal


class TelegramMirrorProvider:
    operations=frozenset({'history'})

    def __init__(self, service, account_ref, user_id, opener=None):
        self.service=service; self.account_ref=account_ref; self.authorized_user=user_id
        self.sender_id=account_ref.split(':',1)[1]
        self.opener=opener or urllib.request.build_opener(_NoRedirect,urllib.request.ProxyHandler({})).open
        outbox=getattr(service,'telegram_outbox',None)
        operations={'history'}
        if callable(getattr(outbox,'react',None)): operations.add('react')
        if callable(getattr(outbox,'edit_message',None)): operations.add('edit')
        supports_reply=getattr(outbox,'supports_reply',None)
        if callable(getattr(outbox,'reply_message',None)) or (callable(supports_reply) and supports_reply(account_ref)):
            operations.add('reply')
        self.operations=frozenset(operations)

    def supports_action(self, action, conversation):
        outbox=getattr(self.service,'telegram_outbox',None)
        if action=='reply':
            supports=getattr(outbox,'supports_reply',None)
            if callable(supports):
                return supports(self.account_ref,str(conversation.get('peer_id') or ''))
            return callable(getattr(outbox,'reply_message',None))
        if action!='edit': return True
        supports=getattr(outbox,'supports_edit',None)
        if callable(supports):
            return supports(self.account_ref,str(conversation.get('peer_id') or ''))
        return callable(getattr(outbox,'edit_message',None))

    def execute_telegram_admin(self, envelope):
        if (envelope.get('action')!='telegram_admin' or envelope.get('channel')!='telegram'
                or envelope.get('account_ref')!=self.account_ref
                or envelope.get('user_id')!=self.authorized_user):
            raise DeliveryNotSubmittedError('Telegram admin account conflict')
        operation=envelope.get('operation')
        if operation not in {'group_create','group_invite','group_remove','channel_create','channel_delete'}:
            raise DeliveryNotSubmittedError('Telegram admin operation unavailable')
        outbox=getattr(self.service,'telegram_outbox',None)
        execute=getattr(outbox,'telegram_admin_execute',None)
        if not callable(execute): raise DeliveryNotSubmittedError('Telegram admin route unavailable')
        result=execute(account_ref=self.account_ref,operation=operation,payload=envelope['payload'],
                       request_id=envelope['action_id'])
        if (not isinstance(result,dict) or result.get('account_id')!=self.account_ref
                or result.get('operation')!=operation or result.get('ok') is not True
                or result.get('status') not in {'complete','partial'}
                or not isinstance(result.get('receipt'),str) or not result['receipt']):
            raise RuntimeError('Telegram admin provider receipt is invalid')
        peer_id=str(result.get('peer_id') or envelope.get('peer_id') or '')
        if operation in {'group_create','channel_create'} and not peer_id:
            raise RuntimeError('Telegram admin creation receipt has no peer')
        return {'account_ref':self.account_ref,'operation':operation,'peer_id':peer_id,
                'status':result['status'],'receipt':result['receipt'],
                'result':{k:result[k] for k in ('title','conversation_kind','missing_invitees','participant_id') if k in result}}

    def history(self, *, peer_id, conversation_id, limit=50, cursor=''):
        before=0
        if cursor:
            try:
                if not isinstance(cursor,str) or len(cursor)>1024:raise ValueError()
                c=json.loads(base64.urlsafe_b64decode(cursor))
                if (c['account_ref'],c['conversation_id'],c['peer_id'])!=(self.account_ref,conversation_id,peer_id):raise ValueError()
                before=c['before']
                if type(before) is not int or not 0<before<2**31:raise ValueError()
            except (ValueError,TypeError,KeyError):raise ValueError('invalid Telegram native cursor') from None
        token=os.environ.get('USERIO_API_TOKEN','')
        if not token:raise ValueError('existing Telegram mirror credential unavailable')
        url=os.environ.get('USERIO_TELEGRAM_QR_URL','http://127.0.0.1:18095').rstrip('/')+'/reconcile'
        request=urllib.request.Request(url,data=canonical({'account_id':self.account_ref,
            'peer_id':peer_id,'limit':limit+1,'before_id':before,'scan':True}),
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with self.opener(request,timeout=30) as response:
            raw=response.read(65537)
        if len(raw)>65536:raise ValueError('Telegram page receipt exceeds limit')
        page=json.loads(raw)
        ids=page.get('message_ids')
        if (page.get('account_id'),page.get('peer_id'))!=(self.account_ref,peer_id):raise ValueError('Telegram page scope conflict')
        if (not isinstance(ids,list) or len(ids)>limit+1
                or any(type(i) is not int or not 0<i<2**31 or before and i>=before for i in ids)
                or page.get('provider_read_acknowledged') is not False):raise ValueError('invalid fresh Telegram receipt')
        ids=sorted(set(ids),reverse=True)
        api=Messenger(self.service._store,self.service,
            UserPrincipal(self.authorized_user,'mirror-reader','user',read_only=True))
        conv=api._conversation(self.account_ref,conversation_id)
        projected=[]
        for native_id in ids[:limit]:
            mid=self.account_ref+'|'+peer_id+':'+str(native_id)
            message=self.service._store.message(mid,source='telegram',user_id=self.authorized_user)
            if not message or message['conversation_id']!=conv['id']:raise ValueError('fresh Telegram projection missing')
            projected.append(api._project(conv,message))
        more=len(ids)>limit or page.get('has_more') is True
        boundary=ids[limit-1] if len(ids)>limit else page.get('oldest_id')
        next_cursor=''
        if more and type(boundary) is int and boundary>0:
            next_cursor=base64.urlsafe_b64encode(canonical({'account_ref':self.account_ref,
                'conversation_id':conversation_id,'peer_id':peer_id,'before':boundary})).decode()
        return {'messages':list(reversed(projected)),'next_cursor':next_cursor,
            'partial':True,'coverage':'bounded_native_history_page','observed_at':time.time(),
            'native_fetched':page.get('fetched'),'provider_read_acknowledged':False}

    def import_projection(self, projected, *, peer_id, conversation_id, kind='unknown'):
        # Reconciliation already ingested these rows using the selected client.
        source=projected['source']
        if (source['account_ref'],source['peer_id'],source['conversation_id'])!=(self.account_ref,peer_id,conversation_id):
            raise ValueError('Telegram projection scope conflict')

    def execute(self, envelope):
        action=envelope.get('action')
        if (envelope.get('account_ref'), envelope.get('user_id')) != (
                self.account_ref, self.authorized_user) or action not in {'react','edit','reply'}:
            raise DeliveryNotSubmittedError('Telegram account operation conflict')
        conversation = self.service._store.conversation(
            envelope['conversation_id'], user_id=self.authorized_user)
        if (conversation is None or conversation.get('account_ref') != self.account_ref
                or str(conversation.get('peer_id')) != str(envelope.get('peer_id'))):
            raise DeliveryNotSubmittedError('Telegram conversation changed')
        outbox = getattr(self.service, 'telegram_outbox', None)
        messenger=Messenger(self.service._store,self.service,
            UserPrincipal(self.authorized_user,'telegram-messenger-action','user'))
        projected, source_hash=messenger._binding(conversation,envelope['source_message_id'])
        if source_hash != envelope['source_hash']:
            raise DeliveryNotSubmittedError('Telegram source changed before provider action')
        if action=='react':
            send = getattr(outbox, 'react', None)
            if not callable(send):
                raise DeliveryNotSubmittedError('Telegram reaction route unavailable')
            result = send(chat=str(conversation.get('sender') or ''),
                chat_id=str(envelope['peer_id']), account_ref=self.account_ref,
                message_id=str(envelope['source_provider_message_id']),
                emoji=str(envelope['payload']['emoji']))
            return {'account_ref':self.account_ref, 'peer_id':str(envelope['peer_id']),
                'provider_message_id':str(envelope['source_provider_message_id']),
                'receipt':result}

        if action=='edit':
            if projected['source']['direction']!='outgoing' or projected['text']!=envelope['payload']['expected_text']:
                raise DeliveryNotSubmittedError('Telegram edit source is no longer the owned current message')
            edit=getattr(outbox,'edit_message',None)
            if not callable(edit):
                raise DeliveryNotSubmittedError('Telegram edit route unavailable')
            result=edit(chat_id=str(envelope['peer_id']),account_ref=self.account_ref,
                message_id=str(envelope['source_message_id']),expected_text=str(envelope['payload']['expected_text']),
                text=str(envelope['payload']['text']))
            if (not isinstance(result,dict) or result.get('ok') is not True
                    or result.get('account_id')!=self.account_ref
                    or str(result.get('peer_id'))!=str(envelope['peer_id'])
                    or str(result.get('message_id'))!=str(envelope['source_provider_message_id'])
                    or result.get('text')!=envelope['payload']['text'] or result.get('out') is not True):
                raise RuntimeError('Telegram edit readback identity conflict')

            current=self.service._store.message(envelope['source_message_id'],source='telegram',
                                                user_id=self.authorized_user)
            if current is None or current['conversation_id']!=conversation['id']:
                raise RuntimeError('Telegram edited message mirror row disappeared')
            row, _ = self.service.receive(InboxMessage(
                source='telegram', message_id=str(envelope['peer_id'])+':'+str(envelope['source_provider_message_id']),
                sender=str(current['sender']), sender_name=str(current.get('sender_name') or ''),
                body=str(result['text']), received_at=float(current['received_at']), direction='outgoing',
                peer_id=str(envelope['peer_id']), conversation_kind=str(conversation.get('conversation_kind') or 'unknown'),
                edited_at=float(result['edit_date']), attachments=tuple(current.get('attachments') or ()),
                reconciliation=True), route_id=str(conversation['route_id']), user_id=self.authorized_user,
                account_ref=self.account_ref)
            if row!=conversation['id']:
                raise RuntimeError('Telegram edit changed the conversation identity')
            return {'account_ref':self.account_ref,'peer_id':str(envelope['peer_id']),
                'provider_message_id':str(envelope['source_provider_message_id']),
                'receipt':f"telegram-qr-edit:{result.get('slot','?')}:{envelope['source_provider_message_id']}"}

        reply=getattr(outbox,'reply_message',None)
        if not callable(reply):
            raise DeliveryNotSubmittedError('Telegram reply route unavailable')
        result=reply(chat_id=str(envelope['peer_id']),account_ref=self.account_ref,
            message_id=str(envelope['source_provider_message_id']),
            thread_id=str(envelope.get('thread_id') or ''),expected_text=projected['text'],
            text=str(envelope['payload']['text']),request_id=str(envelope['action_id']))
        if (not isinstance(result,dict) or result.get('ok') is not True
                or result.get('account_id')!=self.account_ref
                or str(result.get('peer_id'))!=str(envelope['peer_id'])
                or not str(result.get('message_id') or '').isdigit()
                or not 0<int(result.get('message_id') or '0')<=0x7FFFFFFF
                or str(result.get('reply_to_id'))!=str(envelope['source_provider_message_id'])
                or str(result.get('thread_id') or '')!=str(envelope.get('thread_id') or '')
                or result.get('text')!=envelope['payload']['text'] or result.get('out') is not True
                or type(result.get('date')) not in (int,float) or result['date']<=0):
            raise RuntimeError('Telegram reply receipt identity conflict')
        return {'account_ref':self.account_ref,'peer_id':str(envelope['peer_id']),
            'provider_message_id':str(result['message_id']),
            'receipt':f"telegram-qr-reply:{result.get('slot','?')}:{result['message_id']}:{envelope['action_id']}",
            'text':str(result['text']),'provider_at':float(result['date']),
            'reply_to_provider_message_id':str(result['reply_to_id']),
            'provider_thread_id':str(result.get('thread_id') or '')}

    def project_receipt(self,envelope,result):
        if envelope.get('action')=='telegram_admin':
            if envelope.get('operation') not in {'group_create','channel_create'}: return
            peer_id=str(result.get('peer_id') or '')
            detail=result.get('result') or {}
            kind=str(detail.get('conversation_kind') or ('group' if envelope['operation']=='group_create' else 'channel'))
            if not peer_id or kind not in {'group','channel'}: raise ValueError('Telegram created peer type is invalid')
            self.service._store.create_conversation('telegram',str(detail.get('title') or envelope['payload'].get('title') or peer_id),
                user_id=self.authorized_user,account_ref=self.account_ref,peer_id=peer_id,
                conversation_kind=kind,route_id='telegram')
            return
        if envelope.get('action')!='reply': return
        conversation=self.service._store.conversation(
            envelope['conversation_id'],user_id=self.authorized_user)
        if (conversation is None or conversation.get('account_ref')!=self.account_ref
                or str(conversation.get('peer_id'))!=str(envelope.get('peer_id'))):
            raise ValueError('Telegram reply receipt conversation changed')
        message=InboxMessage(
            source='telegram',message_id=str(envelope['peer_id'])+':'+str(result['provider_message_id']),
            sender=str(conversation.get('sender') or ''),
            sender_name=str(conversation.get('display_name') or ''),body=str(result['text']),
            received_at=float(result['provider_at']),direction='outgoing',
            peer_id=str(envelope['peer_id']),
            conversation_kind=str(conversation.get('conversation_kind') or 'unknown'),
            reconciliation=True,provider_reply_to=str(result['reply_to_provider_message_id']),
            provider_thread_id=str(result.get('provider_thread_id') or ''))
        row,_=self.service.receive(message,route_id=str(conversation['route_id']),
            user_id=self.authorized_user,account_ref=self.account_ref)
        if row!=conversation['id']:
            raise ValueError('Telegram reply receipt changed the conversation identity')
