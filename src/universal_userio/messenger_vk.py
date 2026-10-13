"""Generic VK reactions on the user's existing personal connector.

The durable Messenger journal owns approval. VK uses native cmid for reactions;
message IDs remain canonical global IDs. Reads never mark messages read.
"""
from __future__ import annotations
import base64
import hashlib
import json
from .contracts import DeliveryNotSubmittedError
from .messenger import digest
from .account_connections import vk_personal_message


class VKMessengerProvider:
    operations=frozenset({'history','react'})
    reaction_options=['❤️','🔥','😂','👍']

    def __init__(self,service,account_ref,user_id):
        self.service=service;self.account_ref=account_ref;self.authorized_user=user_id

    @property
    def sender_id(self):return self.account_ref.split(':',1)[1]

    def _client(self,peer_id,*,write=False):
        from .account_connections import _vk_personal_client
        return _vk_personal_client(self.service,{'account_ref':self.account_ref,'peer_id':peer_id},
                                   self.authorized_user,for_send=write)

    def _projection(self,row,peer,cid):
        if str(row.get('peer_id'))!=peer:raise ValueError('VK peer conflict')
        msg=vk_personal_message(account_id=self.account_ref,peer_id=int(peer),row=row,reconciled=True)
        if msg is None:raise ValueError('VK actual source unavailable')
        return {'source':{'account_ref':self.account_ref,'peer_id':peer,'conversation_id':cid,
            'provider_message_id':str(row['id']),'message_id':msg.message_id,'channel':'vk',
            'direction':msg.direction,'conversation_kind':msg.conversation_kind,
            'thread_id':'','reply_to_provider_message_id':'','invocation':{'mention':False,'reply':False}},
            'author':{'id':msg.sender,'name':msg.sender_name,'username':'',
                      'role':'owner' if msg.direction=='outgoing' else 'interlocutor'},
            'text':msg.body,'at':msg.received_at,'edited_at':msg.edited_at,'media':list(msg.attachments),
            'reaction_info':{'own_reaction_id':row.get('reaction_id') or 0,'reactions':row.get('reactions') or []},
            'reaction_readback':{'status':'ready'}}

    def message(self,*,peer_id,provider_message_id,conversation_id):
        client,own=self._client(peer_id)
        if client.identity()!=own:raise ValueError('VK account identity conflict')
        return self._projection(client.message(int(provider_message_id)),peer_id,conversation_id)

    def import_projection(self,p,*,peer_id,conversation_id,kind='unknown'):
        from .contracts import InboxMessage
        s=p['source'];a=p['author']
        if (s['account_ref'],s['peer_id'],s['conversation_id'])!=(self.account_ref,peer_id,conversation_id):
            raise ValueError('VK projection identity conflict')
        msg=InboxMessage('vk',s['message_id'],a['id'],p['text'],p['at'],sender_name=a.get('name') or '',
            direction=s['direction'],peer_id=peer_id,conversation_kind=s['conversation_kind'],
            edited_at=p['edited_at'],attachments=tuple(p['media']),reconciliation=True)
        cid,_=self.service.receive(msg,route_id='vk',user_id=self.authorized_user,account_ref=self.account_ref)
        if cid!=conversation_id:raise ValueError('VK conversation identity conflict')

    def history(self,*,peer_id,conversation_id,limit=50,cursor=''):
        offset=0
        if cursor:
            try:
                c=json.loads(base64.urlsafe_b64decode(cursor))
                if (c['account_ref'],c['peer_id'],c['conversation_id'])!=(self.account_ref,peer_id,conversation_id):
                    raise ValueError('VK cursor scope conflict')
                offset=c['offset']
                if type(offset) is not int or offset<0:raise ValueError('invalid VK cursor')
            except (ValueError,TypeError,KeyError):raise ValueError('invalid VK cursor') from None
        client,own=self._client(peer_id)
        if client.identity()!=own:raise ValueError('VK account identity conflict')
        n=min(limit,99);rows=client.history(int(peer_id),count=n+1,offset=offset);page=rows[:n]
        more=len(rows)>n
        next_cursor=base64.urlsafe_b64encode(json.dumps({'account_ref':self.account_ref,'peer_id':peer_id,
                'conversation_id':conversation_id,'offset':offset+n}).encode()).decode() if more else ''
        return {'messages':[self._projection(row,peer_id,conversation_id) for row in reversed(page)],
                'next_cursor':next_cursor,'partial':True,'more_stored':False,'page_limit':n}

    @staticmethod
    def _binding(p):
        s=p['source']
        b={k:s[k] for k in ('account_ref','peer_id','provider_message_id','direction','thread_id','reply_to_provider_message_id')}
        b.update(author_id=p['author']['id'],edited_at=int(p['edited_at'] or 0),
                 text_sha256=hashlib.sha256(p['text'].encode()).hexdigest())
        return b

    def execute(self,e):
        if (e['account_ref'],e['user_id'],e['action'])!=(self.account_ref,self.authorized_user,'react'):
            raise DeliveryNotSubmittedError('VK account operation conflict')
        try:
            client,own=self._client(e['peer_id'],write=True)
            if client.identity()!=own:raise ValueError('VK account identity conflict')
            row=client.message(int(e['source_provider_message_id']))
            p=self._projection(row,e['peer_id'],e['conversation_id'])
            if digest(self._binding(p))!=e['source_hash']:raise ValueError('VK source changed')
        except Exception:
            raise DeliveryNotSubmittedError('VK source could not be verified; no mutation submitted') from None
        client.react(peer_id=int(e['peer_id']),row=row,emoji=e['payload']['emoji'])
        return {'account_ref':self.account_ref,'peer_id':e['peer_id'],
                'provider_message_id':e['source_provider_message_id'],
                'receipt':'vk-api-reaction:'+e['action_id']}
