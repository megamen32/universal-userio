"""Personal VK web actions reuse the normal draft claim and bounded file staging."""
import hashlib
import json
import re
import time
from urllib.parse import urlsplit

from .account_connections import _vk_personal_client, vk_personal_message
from .messenger_uploads import MessengerUploads
from .channels.vk_personal import VKEditPreflightError
from .contracts import DeliveryNotSubmittedError


class VKWeb:
    def __init__(self,service,user):
        self.service,self.store,self.user=service,service._store,user
        self.uploads=MessengerUploads(self.store,user)
        with self.store._lock,self.store._connection:
            self.store._connection.execute('''CREATE TABLE IF NOT EXISTS vk_web_files (
                user_id TEXT NOT NULL,draft_id TEXT NOT NULL,upload_id TEXT NOT NULL,
                native_ref TEXT NOT NULL DEFAULT '',PRIMARY KEY(user_id,draft_id))''')
            self.store._connection.execute('''CREATE TABLE IF NOT EXISTS vk_web_edits (
                user_id TEXT NOT NULL,id TEXT NOT NULL,status TEXT NOT NULL,
                PRIMARY KEY(user_id,id))''')

    @staticmethod
    def binding(conv):
        return {k:conv[k] for k in ('id','account_ref','peer_id')}

    def conversation(self,cid,*,write=True):
        conv=self.store.conversation(cid,user_id=self.user)
        if not conv or conv['source']!='vk':raise ValueError('Личный диалог VK не найден')
        if write and not self.store.send_enabled(user_id=self.user):raise ValueError('Отправка отключена')
        if not self.store.route_allowed(user_id=self.user,source='vk',route_id=conv['route_id']):raise ValueError('VK route unavailable')
        _vk_personal_client(self.service,conv,self.user,for_send=write)
        return conv

    def stage(self,cid,payload):
        conv=self.conversation(cid)
        return self.uploads.stage(self.binding(conv),**{k:payload[k] for k in ('filename','mime_type','data_base64','sha256','request_id')})

    def bind(self,draft,conv,upload_id):
        with self.store._lock,self.store._connection:
            prior=self.store._connection.execute('SELECT * FROM vk_web_files WHERE user_id=? AND draft_id=?',(self.user,draft.id)).fetchone()
            if prior:
                if prior['upload_id']!=upload_id:raise ValueError('Запрос уже связан с другим вложением')
                return
            if not upload_id:return
            if draft.status!='proposed':raise ValueError('Нельзя добавить вложение к уже отправленному сообщению')
            self.uploads.metadata(upload_id,self.binding(conv))
            self.uploads.bind(upload_id,draft.id)
            self.store._connection.execute('INSERT INTO vk_web_files(user_id,draft_id,upload_id) VALUES(?,?,?)',(self.user,draft.id,upload_id))

    def for_draft(self,draft_id,conv):
        with self.store._lock:
            row=self.store._connection.execute('SELECT * FROM vk_web_files WHERE user_id=? AND draft_id=?',(self.user,draft_id)).fetchone()
            if row is None:return None
            staged=self.uploads._row(row['upload_id'])
            if staged['action_id']!=draft_id or json.loads(staged['binding'])!=self.binding(conv):raise ValueError('VK attachment source mismatch')
            return dict(row)

    def send_file(self,client,conv,draft,row,random_id):
        ref=row['native_ref']
        if not ref:
            with self.store._lock:
                data=self.uploads._row(row['upload_id']);meta=json.loads(data['metadata']);blob=bytes(data['data'])
            ref=client.upload(peer_id=int(conv['peer_id']),data=blob,filename=meta['filename'],mime_type=meta['mime_type'])
            with self.store._lock,self.store._connection:
                self.store._connection.execute('UPDATE vk_web_files SET native_ref=? WHERE user_id=? AND draft_id=?',(ref,self.user,draft.id))
        receipt=client.send(int(conv['peer_id']),draft.body,random_id,attachment=ref)
        self.uploads.consume(row['upload_id'])
        return receipt

    def edit(self,cid,mid,body,expected):
        conv=self.conversation(cid)
        record=self.store.message(mid,source='vk',user_id=self.user)
        prefix=conv['account_ref']+'|'+conv['peer_id']+':'
        if (not record or record['conversation_id']!=cid or record['direction']!='outgoing'
                or not mid.startswith(prefix) or not mid[len(prefix):].isdigit()):raise ValueError('Можно менять только своё сообщение в этом диалоге')
        if not body.strip() or len(body)>4096:raise ValueError('Нужен текст до 4096 символов')
        eid=hashlib.sha256(json.dumps([cid,mid,expected,body],ensure_ascii=False).encode()).hexdigest()
        client,own=_vk_personal_client(self.service,conv,self.user)
        if client.identity()!=own:raise ValueError('VK account identity mismatch')
        with self.store._lock,self.store._connection:
            old=self.store._connection.execute('SELECT status FROM vk_web_edits WHERE user_id=? AND id=?',(self.user,eid)).fetchone()
            if old and old['status']=='accepted':return record
            if not old:
                if record['body']!=expected:raise ValueError('Сообщение уже изменилось. Обновите диалог.')
                self.store._connection.execute('INSERT INTO vk_web_edits VALUES(?,?,?)',(self.user,eid,'sending'))
        if old:
            # A lost response is resolved by reading this exact native ID, never
            # by repeating the mutation. Future distinct edits use fresh text.
            row=client.message(int(mid[len(prefix):]))
            if row.get('text')!=body:raise RuntimeError('Результат изменения уточняется; обновите диалог без повторения')
        else:
            try:row=client.edit(peer_id=int(conv['peer_id']),message_id=int(mid[len(prefix):]),body=body,expected_body=expected,own_id=own)
            except (VKEditPreflightError,DeliveryNotSubmittedError):
                with self.store._lock,self.store._connection:self.store._connection.execute('DELETE FROM vk_web_edits WHERE user_id=? AND id=?',(self.user,eid))
                raise
        if row.get('peer_id')!=int(conv['peer_id']) or row.get('from_id')!=own or row.get('out')!=1:raise ValueError('VK edit source mismatch')
        self.import_row(conv,row)
        with self.store._lock,self.store._connection:self.store._connection.execute('UPDATE vk_web_edits SET status=? WHERE user_id=? AND id=?',('accepted',self.user,eid))
        return self.store.message(mid,source='vk',user_id=self.user)

    def import_row(self,conv,row):
        message=vk_personal_message(account_id=conv['account_ref'],peer_id=int(conv['peer_id']),row=row,title=conv.get('display_name') or '',reconciled=True,peer_type='chat' if conv.get('conversation_kind')=='group' else 'user')
        if message is None:raise ValueError('VK не вернул сообщение')
        self.service.receive(message,route_id=conv['route_id'],user_id=self.user,account_ref=conv['account_ref'])

    def download(self,cid,mid):
        conv=self.conversation(cid,write=False);msg=self.store.message(mid,source='vk',user_id=self.user)
        prefix=conv['account_ref']+'|'+conv['peer_id']+':'
        if not msg or msg['conversation_id']!=cid or not mid.startswith(prefix):raise ValueError('Вложение не принадлежит этому диалогу')
        client,_=_vk_personal_client(self.service,conv,self.user,for_send=False)
        row=client.message(int(mid[len(prefix):]))
        if row.get('peer_id')!=int(conv['peer_id']):raise ValueError('VK media source mismatch')
        return client.download(row)

    def create_conversation(self,account_ref,identifier):
        conv={'account_ref':account_ref};client,own=_vk_personal_client(self.service,conv,self.user)
        value=identifier.strip()
        if value.startswith(('https://','http://')):
            parsed=urlsplit(value)
            if parsed.hostname not in {'vk.ru','vk.com','www.vk.ru','www.vk.com'}:raise ValueError('Укажите ссылку VK')
            value=parsed.path.strip('/')
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',value):raise ValueError('Укажите профиль или ID VK')
        rows=client.call('users.get',user_ids=value,fields='can_write_private_message')
        if not isinstance(rows,list) or len(rows)!=1 or type(rows[0].get('id')) is not int:raise ValueError('VK не нашёл пользователя')
        person=rows[0];peer=str(person['id']);key=f'vk:{account_ref}:{peer}'
        cid='conv_'+hashlib.sha256((self.user+'\0'+key).encode()).hexdigest()[:24]
        title=' '.join(str(person.get(k) or '') for k in ('first_name','last_name')).strip() or peer
        with self.store._lock,self.store._connection:
            self.store._connection.execute('''INSERT OR IGNORE INTO conversations
                (user_id,id,conversation_key,route_id,source,sender,identity_id,response_mode,updated_at,account_ref,peer_id,conversation_kind)
                VALUES(?,?,?,?,?,?,NULL,'approve',?,?,?,'direct')''',(self.user,cid,key,'vk-personal-read-only','vk',title,time.time(),account_ref,peer))
        return self.store.conversation(cid,user_id=self.user)
