"""Yandex web sends reuse UserIO's durable draft claim and exact receipt."""
import hashlib
import time
import uuid
from urllib.parse import urlsplit
from ..account_connections import ConnectorSecrets
from ..contracts import DeliveryNotSubmittedError
from .sync import read,project

class YandexWeb:
    def __init__(self,service,user_id):
        self.service,self.store,self.user=service,service._store,user_id

    def account(self,reference,*,write=False):
        account=next((a for a in self.store.accounts(user_id=self.user)
            if a['provider']=='yandex' and a['enabled'] and a['id']==reference
            and (not write or 'reply' in a['capabilities'])),None)
        if not account:raise DeliveryNotSubmittedError('Яндекс не подключён для этой операции')
        return ConnectorSecrets().load(account['credential_ref'],user_id=self.user,provider='yandex')

    def create_conversation(self,reference,identifier):
        secret=self.account(reference)
        value=identifier.strip()
        if value.startswith('https://'):
            url=urlsplit(value)
            if url.hostname!='telemost.yandex.ru' or not url.path.startswith('/user/'):
                raise ValueError('Укажите ссылку на пользователя Телемоста')
            value=url.path.removeprefix('/user/')
        if str(uuid.UUID(value))!=value:raise ValueError('Укажите ID пользователя Яндекса')
        data=read(secret,operation='resolve',target_guid=value)
        peer=data['peer_id'];key=f'yandex:{reference}:{peer}'
        identity='conv_'+hashlib.sha256((self.user+'\0'+key).encode()).hexdigest()[:24]
        person=data['person'];title=str(person.get('display_name') or person.get('name') or value)
        with self.store._lock,self.store._connection:
            self.store._connection.execute('''INSERT OR IGNORE INTO conversations
                (user_id,id,conversation_key,route_id,source,sender,identity_id,response_mode,updated_at,account_ref,peer_id,conversation_kind)
                VALUES(?,?,?,?,?,?,NULL,'approve',?,?,?,'direct')''',
                (self.user,identity,key,'manual','yandex',value,time.time(),reference,peer))
            self.store._connection.execute('''INSERT OR REPLACE INTO contact_names
                (user_id,source,sender,name,updated_at) VALUES(?,'yandex',?,?,?)''',
                (self.user,f'chat:{reference}:{peer}',title,time.time()))
        return self.store.conversation(identity,user_id=self.user)

    def prepare(self,conversation,draft):
        secret=self.account(conversation['account_ref'],write=True)
        peer=conversation['peer_id']
        if not peer or not 1<=len(draft.body.strip())<=4096:
            raise DeliveryNotSubmittedError('Сообщение Яндекса должно содержать до 4096 символов')
        payload_id=str(uuid.uuid5(uuid.NAMESPACE_URL, self.user+'\0'+draft.id))
        def send():
            result=read(secret,operation='send',chat_id=peer,text=draft.body,payload_id=payload_id)
            return str(result['receipt'])
        return send

    def archive_receipt(self,conversation,receipt):
        secret=self.account(conversation['account_ref'])
        page=read(secret,chat_id=conversation['peer_id'],before=int(receipt)+1,limit=1)
        for chat in page['chats']:
            for message in project(chat,secret,reconciliation=True):
                self.service.receive(message,route_id='manual',user_id=self.user,
                    account_ref=conversation['account_ref'])
