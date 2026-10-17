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
from .contracts import UserPrincipal


class TelegramMirrorProvider:
    operations=frozenset({'history'})

    def __init__(self, service, account_ref, user_id, opener=None):
        self.service=service; self.account_ref=account_ref; self.authorized_user=user_id
        self.sender_id=account_ref.split(':',1)[1]
        self.opener=opener or urllib.request.build_opener(_NoRedirect,urllib.request.ProxyHandler({})).open

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
