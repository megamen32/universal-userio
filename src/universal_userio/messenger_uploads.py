"""Bounded private staging for new files, never paths or provider URLs.

Bytes belong to one user/source and one approved action. The existing action
journal owns delivery uncertainty; staging is not a second send queue.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import time

from .messenger import canonical, digest

# Legacy providers without a published upload capability retain their existing
# conservative bound. Telegram uses the account's live MTProto upload ceiling,
# with SQLite's configured BLOB limit as the storage backstop.
MAX_BYTES = 20 * 1024 * 1024
MAX_STORAGE_BYTES = 1_000_000_000
TTL_SECONDS = 900


class MessengerUploads:
    def __init__(self, store, user):
        self.store, self.user = store, user
        with store._lock:
            store._connection.execute('''CREATE TABLE IF NOT EXISTS messenger_uploads (
                user_id TEXT NOT NULL,id TEXT NOT NULL,request_id TEXT NOT NULL,
                binding TEXT NOT NULL,metadata TEXT NOT NULL,hash TEXT NOT NULL,
                data BLOB,expires_at REAL NOT NULL,action_id TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(user_id,id),UNIQUE(user_id,request_id))''')

    def _byte_limit(self, max_bytes):
        getlimit=getattr(self.store._connection,'getlimit',None)
        sqlite_limit=getlimit(getattr(sqlite3,'SQLITE_LIMIT_LENGTH',0)) if callable(getlimit) else MAX_STORAGE_BYTES
        storage_limit=min(MAX_STORAGE_BYTES,sqlite_limit)
        return min(storage_limit,max_bytes) if type(max_bytes) is int and max_bytes>0 else storage_limit

    @staticmethod
    def _validate_metadata(filename,mime_type,request_id):
        if (not isinstance(filename,str) or not 1<=len(filename)<=128
                or filename in {'.','..'} or any(c in filename for c in '/\\')
                or any(ord(c)<32 or ord(c)==127 for c in filename)):
            raise ValueError('plain_filename_required')
        if not isinstance(mime_type,str) or not re.fullmatch(r'[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+',mime_type) or len(mime_type)>128:
            raise ValueError('mime_type_required')
        if not isinstance(request_id,str) or not re.fullmatch(r'[a-zA-Z0-9:_-]{16,128}',request_id):
            raise ValueError('request_id_required')

    def stage(self,binding,*,filename,mime_type,data_base64,sha256,request_id,max_bytes=None):
        limit=self._byte_limit(max_bytes)
        if not isinstance(data_base64,str) or len(data_base64)>4*((limit+2)//3):
            raise ValueError('upload_exceeds_account_or_storage_limit')
        try:data=base64.b64decode(data_base64,validate=True)
        except (ValueError,UnicodeError):raise ValueError('invalid_upload_base64') from None
        return self.stage_bytes(binding,filename=filename,mime_type=mime_type,data=data,
            sha256=sha256,request_id=request_id,max_bytes=max_bytes)

    def stage_bytes(self,binding,*,filename,mime_type,data,sha256,request_id,max_bytes=None):
        self._validate_metadata(filename,mime_type,request_id)
        if not isinstance(data,bytes):raise ValueError('invalid_upload_bytes')
        limit=self._byte_limit(max_bytes)
        if len(data)>limit:raise ValueError('upload_exceeds_account_or_storage_limit')
        if not data:raise ValueError('upload_must_not_be_empty')
        actual=hashlib.sha256(data).hexdigest()
        if sha256!=actual:raise ValueError('upload_sha256_conflict')
        uid='upload_'+hashlib.sha256((self.user+'\0'+request_id).encode()).hexdigest()
        metadata={'upload_id':uid,'filename':filename,'mime_type':mime_type,'size':len(data),'sha256':actual}
        identity=digest({'binding':binding,'metadata':metadata})
        now=time.time()
        with self.store._lock,self.store._connection:
            self.store._connection.execute('UPDATE messenger_uploads SET data=NULL WHERE expires_at<=? AND data IS NOT NULL',(now,))
            row=self.store._connection.execute('SELECT * FROM messenger_uploads WHERE user_id=? AND id=?',(self.user,uid)).fetchone()
            if row:
                if row['hash']!=identity:raise ValueError('upload_idempotency_conflict')
                if row['expires_at']<=now or row['data'] is None:raise ValueError('upload_expired_or_consumed')
                self._verify(row)
                return {**metadata,'expires_at':row['expires_at']}
            expiry=now+TTL_SECONDS
            self.store._connection.execute('INSERT INTO messenger_uploads (user_id,id,request_id,binding,metadata,hash,data,expires_at) VALUES (?,?,?,?,?,?,?,?)',
                (self.user,uid,request_id,canonical(binding).decode(),canonical(metadata).decode(),identity,data,expiry))
        return {**metadata,'expires_at':expiry}

    def _row(self, upload_id):
        row=self.store._connection.execute('SELECT * FROM messenger_uploads WHERE user_id=? AND id=?',(self.user,upload_id)).fetchone()
        if row is None: raise KeyError('upload_not_found')
        if row['expires_at']<=time.time() or row['data'] is None: raise ValueError('upload_expired_or_consumed')
        self._verify(row)
        return row

    @staticmethod
    def _verify(row):
        metadata=json.loads(row['metadata'])
        if digest({'binding':json.loads(row['binding']),'metadata':metadata})!=row['hash']:
            raise ValueError('upload_metadata_conflict')
        data=bytes(row['data'])
        if len(data)!=metadata['size'] or hashlib.sha256(data).hexdigest()!=metadata['sha256']:
            raise ValueError('upload_bytes_conflict')

    def metadata(self, upload_id, binding):
        with self.store._lock:
            row=self._row(upload_id)
            if json.loads(row['binding'])!=binding: raise ValueError('upload_source_conflict')
            return json.loads(row['metadata'])

    def bind(self, upload_id, action_id):
        # Caller holds the same SQLite writer transaction as action insertion.
        row=self._row(upload_id)
        if row['action_id'] not in {'',action_id}: raise ValueError('upload_already_bound')
        self.store._connection.execute('UPDATE messenger_uploads SET action_id=? WHERE user_id=? AND id=?',(action_id,self.user,upload_id))

    def data(self, envelope):
        with self.store._lock:
            row=self._row(envelope['payload']['upload_id'])
            if row['action_id']!=envelope['action_id']: raise ValueError('upload_action_conflict')
            binding=upload_binding(envelope)
            if json.loads(row['binding'])!=binding: raise ValueError('upload_source_conflict')
            metadata=json.loads(row['metadata'])
            if any(envelope['payload'].get(k)!=v for k,v in metadata.items()): raise ValueError('upload_metadata_conflict')
            data=bytes(row['data'])
            if len(data)!=metadata['size'] or hashlib.sha256(data).hexdigest()!=metadata['sha256']:
                raise ValueError('upload_bytes_conflict')
            return data

    def consume(self, upload_id):
        with self.store._lock, self.store._connection:
            self.store._connection.execute('UPDATE messenger_uploads SET data=NULL WHERE user_id=? AND id=?',(self.user,upload_id))


def upload_binding(envelope):
    return {k:envelope[k] for k in ('account_ref','conversation_id','source_message_id','source_hash','source_binding','thread_id')}
