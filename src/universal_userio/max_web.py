"""MAX file staging reuses UserIO's normal human-send claim and native client."""
import json
import hashlib
from .messenger_uploads import MessengerUploads
from .contracts import DeliveryNotSubmittedError

class MaxWeb:
    def __init__(self,service,user):
        self.service,self.store,self.user=service,service._store,user
        self.uploads=MessengerUploads(self.store,user)
        with self.store._lock,self.store._connection:
            self.store._connection.execute("""CREATE TABLE IF NOT EXISTS max_web_files (
                user_id TEXT NOT NULL,draft_id TEXT NOT NULL,upload_id TEXT NOT NULL,
                native_attachment TEXT NOT NULL DEFAULT '',PRIMARY KEY(user_id,draft_id))""")

    @staticmethod
    def binding(conv):return {k:conv[k] for k in ('id','account_ref','peer_id')}

    def conversation(self,cid):
        conv=self.store.conversation(cid,user_id=self.user)
        if not conv or conv['source']!='max':raise ValueError('Личный диалог MAX не найден')
        if not self.store.send_enabled(user_id=self.user):raise ValueError('Отправка выключена')
        if not self.store.route_allowed(user_id=self.user,source='max',route_id=conv['route_id']):
            raise ValueError('MAX route unavailable')
        self.service.require_max_reply_account(conv,self.user)
        return conv

    def stage(self,cid,payload):
        conv=self.conversation(cid)
        return self.uploads.stage(self.binding(conv),**{k:payload[k] for k in
            ('filename','mime_type','data_base64','sha256','request_id')})

    def bind(self,draft,conv,upload_id):
        with self.store._lock,self.store._connection:
            prior=self.store._connection.execute('SELECT * FROM max_web_files WHERE user_id=? AND draft_id=?',
                (self.user,draft.id)).fetchone()
            if prior:
                if prior['upload_id']!=upload_id:raise ValueError('MAX file request conflict')
                return
            if not upload_id:return
            if draft.status!='proposed':raise ValueError('Cannot attach to a submitted message')
            self.uploads.metadata(upload_id,self.binding(conv));self.uploads.bind(upload_id,draft.id)
            self.store._connection.execute('INSERT INTO max_web_files(user_id,draft_id,upload_id) VALUES(?,?,?)',
                (self.user,draft.id,upload_id))

    def for_draft(self,draft_id,conv):
        with self.store._lock:
            row=self.store._connection.execute('SELECT * FROM max_web_files WHERE user_id=? AND draft_id=?',
                (self.user,draft_id)).fetchone()
            if row is None:return None
            staged=self.uploads._row(row['upload_id'])
            if staged['action_id']!=draft_id or json.loads(staged['binding'])!=self.binding(conv):
                raise ValueError('MAX file source identity conflict')
            return dict(row)

    def send_file(self,client,conv,draft,row):
        native=row['native_attachment']
        if native:
            attachment=json.loads(native)
        else:
            with self.store._lock:
                staged=self.uploads._row(row['upload_id'])
                meta=json.loads(staged['metadata']);data=bytes(staged['data'])
            attachment=client.upload_file(data=data,filename=meta['filename'],mime_type=meta['mime_type'])
            with self.store._lock,self.store._connection:
                self.store._connection.execute('UPDATE max_web_files SET native_attachment=? WHERE user_id=? AND draft_id=?',
                    (json.dumps(attachment),self.user,draft.id))
        cid=int(hashlib.sha256(draft.id.encode()).hexdigest()[:13],16)
        try:
            result=client.send_bound(chat_id=conv['peer_id'],text=draft.body,reply_to="",attaches=[attachment],cid=cid)
        except DeliveryNotSubmittedError:
            raise RuntimeError('MAX file uploaded; send outcome incomplete, do not replay') from None
        mid=result.get('message_id')
        if not mid:raise RuntimeError('MAX did not return the exact sent file message ID')
        self.uploads.consume(row['upload_id'])
        return str(mid)
