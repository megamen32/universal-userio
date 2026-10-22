"""Owner-scoped durable Telemost jobs and automatic archive/transcription."""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid
import wave
from . import meeting_url
from ..contracts import InboxMessage

ROOT=Path('/var/lib/universal-userio/telemost')
REMOTE='/home/roomhacker/.local/share/userio-telemost'
TERMINAL={'recorded','no_audio','failed','cancelled','expired','unknown'}
BYTES_PER_SECOND=16000*2
LIVE_POLL_SECONDS=15
LIVE_MIN_SECONDS=45
LIVE_CHUNK_SECONDS=120
ARCHIVE_CHUNK_SECONDS=120


class Meetings:
    def __init__(self,service,root=ROOT):
        self.service=service;self.root=Path(root);self.lock=threading.Lock();self.active=set();self.transcribing=threading.Lock()

    def _wav(self,pcm):
        data=io.BytesIO()
        with wave.open(data,'wb') as chunk:
            chunk.setnchannels(1);chunk.setsampwidth(2);chunk.setframerate(16000);chunk.writeframes(pcm)
        return data.getvalue()

    def _command(self):
        if os.environ.get('USERIO_TELEMOST_LOCAL')=='1':
            import sys
            return [sys.executable,'-m','universal_userio.telemost.client']
        host=os.environ.get('USERIO_TELEMOST_SSH_HOST','')
        if host not in {'192.168.2.5','192.168.2.75'}:raise RuntimeError('Telemost recorder not configured')
        return ['runuser','-u','roomhacker','--','ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',
                'roomhacker@'+host,'env','PYTHONPATH='+REMOTE+'/source',REMOTE+'/venv/bin/python',
                '-m','universal_userio.telemost.client']

    def _remote(self,payload,output=None):
        if output is not None:
            result=subprocess.run(self._command(),input=json.dumps(payload).encode(),stdout=output,
                                  stderr=subprocess.PIPE,timeout=180)
            if result.returncode:raise RuntimeError('Telemost worker unavailable')
            return None
        result=subprocess.run(self._command(),input=json.dumps(payload).encode(),capture_output=True,timeout=25)
        if result.returncode or len(result.stdout)>1024*1024:raise RuntimeError('Telemost worker unavailable')
        return json.loads(result.stdout)

    def _save(self,record):
        folder=self.root/record['id'];folder.mkdir(parents=True,mode=0o700,exist_ok=True)
        temp=folder/('.'+uuid.uuid4().hex)
        with temp.open('x') as out:
            os.chmod(temp,0o600);json.dump(record,out,ensure_ascii=False);out.flush();os.fsync(out.fileno())
        os.replace(temp,folder/'meeting.json')

    def get(self,identity,owner):
        identity=str(uuid.UUID(identity))
        record=json.loads((self.root/identity/'meeting.json').read_text())
        if record['owner']!=owner:raise KeyError('meeting not found')
        return record

    def list(self,owner):
        if not self.root.exists():return []
        records=[]
        for path in self.root.glob('*/meeting.json'):
            record=json.loads(path.read_text())
            if record['owner']==owner:records.append(record)
        return sorted(records,key=lambda r:r['started_at'],reverse=True)[:50]

    def start(self,*,user_id,url,request_id,max_seconds=3600,confirm=None):
        # Ignore the former flag from cached HTTP clients; the explicit call authorizes entry.
        url=meeting_url(url)
        if (not isinstance(request_id,str) or not 16<=len(request_id)<=128
            or type(max_seconds) is not int or not 30<=max_seconds<=7200):
            raise ValueError('invalid meeting bounds')
        self._command()
        identity=str(uuid.uuid5(uuid.NAMESPACE_URL,'userio:telemost:'+user_id+':'+request_id))
        with self.lock:
            if (self.root/identity/'meeting.json').exists():
                previous=self.get(identity,user_id)
                if (previous['url'],previous['max_seconds'])!=(url,max_seconds):raise ValueError('request ID already used')
                return previous
            record={'id':identity,'owner':user_id,'url':url,'request_id':request_id,'max_seconds':max_seconds,
                    'status':'submitting','started_at':time.time(),'tracks':[], 'mic_enabled':False,'camera_enabled':False,
                    'chat_supported':False}
            # Persist before remote submission. A lost response is not safe to rejoin.
            self._save(record)
            # Legacy worker transport field, supplied by the explicit authorized start call.
            try:record=self._remote(dict(record,op='start',confirm=True))
            except Exception:
                record['status']='unknown';self._save(record);raise
            self._save(record)
            self.active.add(identity)
            threading.Thread(target=self._watch,args=(identity,user_id),daemon=True,name='telemost-status').start()
            return record

    def stop(self,identity,owner):
        record=self.get(identity,owner)
        self._remote({'op':'stop','id':record['id'],'owner':owner})
        return record

    def _emit(self,record,body,suffix):
        self.service._store.register_account(account_id='telemost',provider='telemost',display_name='Яндекс Телемост',
            can_read=True,can_reply=False,credential_ref='telemost:guest-recorder',user_id=record['owner'])
        cid,_=self.service.receive(InboxMessage(source='telemost',message_id='telemost-'+record['id']+'-'+suffix,
            sender=record['id'],body=body,received_at=time.time(),sender_name='Телемост',direction='system',
            conversation_kind='group',peer_id=record['id'],conversation_title='Встреча Телемоста',
            reconciliation=True),route_id='telemost',user_id=record['owner'],account_ref='telemost')
        record['conversation_id']=cid

    def _watch(self,identity,owner):
        record=self.get(identity,owner)
        try:
            deadline=time.monotonic()+record['max_seconds']+60
            last=''
            while time.monotonic()<deadline:
                record=self._remote({'op':'get','id':identity,'owner':owner})
                if record['status']!=last:
                    last=record['status']
                    self._emit(record,{'waiting':'Ожидаю допуска организатора встречи.',
                        'recording':'Запись встречи начата. Микрофон и камера выключены. Расшифровка появится в чате по ходу разговора.',
                        'failed':'Не удалось подключиться к Телемосту. Автоматический повтор не выполняется.'}.get(last,'Состояние встречи: '+last),'status-'+last)
                self._save(record)
                if last in TERMINAL:break
                if last=='recording':self._transcribe_live(record)
                time.sleep(LIVE_POLL_SECONDS)
            else:raise TimeoutError('meeting status deadline')
            if record['status'] in {'recorded','failed'}:
                self._archive(record)
        except Exception as error:
            record.update(status='unknown' if record.get('status') not in TERMINAL else record['status'],
                          processing_error=type(error).__name__)
        finally:
            self._save(record)
            with self.lock:self.active.discard(identity)

    def _pending(self,record,track,offset):
        """Raw PCM already written by the recorder but not yet transcribed."""
        result=subprocess.run(self._command(),input=json.dumps({'op':'pending','id':record['id'],
            'owner':record['owner'],'index':track['index'],'offset':offset}).encode(),
            capture_output=True,timeout=60)
        if result.returncode:raise RuntimeError('Telemost worker unavailable')
        return result.stdout

    def _transcribe_live(self,record):
        """Transcribe the meeting while it runs, so the chat is never silent.

        Audio already consumed is never re-sent: each track carries its own
        byte cursor, and a fragment is emitted at most once.
        """
        from ..messenger_max_media import transcribe
        for track in record.get('tracks',[]):
            # No audio written yet: the pending tail is empty, nothing to send.
            cursor=int(track.get('pending_offset',0))
            try:pcm=self._pending(record,track,cursor)
            except Exception:continue
            # Wait for a usable amount of speech instead of emitting fragments
            # too short to diarize.
            if len(pcm)<LIVE_MIN_SECONDS*BYTES_PER_SECOND:continue
            limit=LIVE_CHUNK_SECONDS*BYTES_PER_SECOND
            if len(pcm)>limit:pcm=pcm[:limit-(limit%2)]
            with self.transcribing:
                try:
                    recognized=transcribe(self._wav(pcm),'audio/wav',diarize=True)
                    diarization=recognized['diarization']
                    offset=cursor/BYTES_PER_SECOND
                    chunk={'offset':offset,'text':recognized['transcript'],'diarization':diarization,
                           'speaker_scope':f"track-{track['index']}/chunk-{offset:g}",'live':True}
                    track.setdefault('transcripts',[]).append(chunk)
                    track['pending_offset']=cursor+len(pcm)
                    track['live_transcription_status']='ready'
                    self._save(record)
                    body=('Расшифровка встречи, дорожка '+str(track['index'])+', с '+f"{offset:g}"
                          +' с. Метки говорящих действуют только внутри этого фрагмента.\n'
                          +(diarization.get('speaker_text') or recognized['transcript']))
                    self._emit(record,body,'live-'+str(track['index'])+'-'+str(int(offset)))
                    self._save(record)
                except Exception as error:
                    track['live_transcription_error']=type(error).__name__
                    # The cursor must not advance past audio that failed to
                    # transcribe; a later poll retries the same bytes.
                    self._save(record)

    def _archive(self,record):
        from ..messenger_max_media import transcribe
        for track in record['tracks']:
            if track.get('bytes',0)<=44:continue
            path=self.root/record['id']/track['filename']
            temp=path.with_suffix('.part')
            with temp.open('wb') as output:
                os.chmod(temp,0o600)
                self._remote({'op':'audio','id':record['id'],'owner':record['owner'],'index':track['index']},output)
            if temp.stat().st_size!=track['bytes']:raise ValueError('recording size mismatch')
            digest=hashlib.sha256()
            with temp.open('rb') as stream:
                while data:=stream.read(65536):digest.update(data)
            if digest.hexdigest()!=track['sha256']:raise ValueError('recording SHA mismatch')
            os.replace(temp,path);track['archived']=True;track.setdefault('transcripts',[])
            try:
                with wave.open(str(path),'rb') as audio:
                    if (audio.getnchannels(),audio.getsampwidth(),audio.getframerate())!=(1,2,16000):raise ValueError('invalid recording format')
                    # Live fragments already shown in the chat are not re-sent;
                    # the archive continues from the same byte cursor.
                    cursor=int(track.get('pending_offset',0))
                    offset=cursor/BYTES_PER_SECOND
                    while pcm:=audio.readframes(16000*ARCHIVE_CHUNK_SECONDS):
                        data=io.BytesIO()
                        with wave.open(data,'wb') as chunk:
                            chunk.setnchannels(1);chunk.setsampwidth(2);chunk.setframerate(16000);chunk.writeframes(pcm)
                        recognized=transcribe(data.getvalue(),'audio/wav',diarize=True)
                        diarization=recognized['diarization']
                        # Speaker labels are local to each ASR request. Never
                        # silently equate SPEAKER_00 across different chunks.
                        chunk={'offset':offset,'text':recognized['transcript'],
                               'diarization':diarization,
                               'speaker_scope':f"track-{track['index']}/chunk-{offset:g}"}
                        track['transcripts'].append(chunk)
                        cursor+=len(pcm)
                        track['pending_offset']=cursor
                        self._save(record)
                        # Same owned conversation, deterministic message ID;
                        # ordinary history now contains the actual transcript.
                        body=(f"Расшифровка встречи, дорожка {track['index']}, "
                              f"фрагмент с {offset:g} с. Метки говорящих действуют "
                              "только внутри этого фрагмента.\n"
                              +(diarization.get('speaker_text') or recognized['transcript']))
                        self._emit(record,body,f"transcript-{track['index']}-{int(offset)}")
                        offset+=len(pcm)/BYTES_PER_SECOND
                track['transcription_status']='ready'
            except Exception as error:track.update(transcription_status='failed',transcription_error=type(error).__name__)
            self._save(record)
        voice_tracks=[t for t in record['tracks'] if t.get('bytes',0)>44]
        self._emit(record,'Запись встречи завершена. '+('Расшифровка с метками говорящих — в этом чате выше.' if voice_tracks and all(t.get('transcription_status')=='ready' for t in voice_tracks) else 'Расшифровка пока недоступна.')+'\nОткрыть аудио: /telemost?id='+record['id'],'completed')
        self._save(record)

    def audio(self,identity,owner,index):
        record=self.get(identity,owner)
        track=next(t for t in record['tracks'] if t['index']==index and t.get('archived'))
        return self.root/record['id']/track['filename']


    def resume(self):
        """Re-attach watchers to meetings a restart left unfinished.

        The recorder is a separate process, so audio survives a backend
        restart. Without this the pending transcript would be abandoned and
        the owner would never see it.
        """
        if not self.root.exists():return
        for path in sorted(self.root.glob('*/meeting.json')):
            try:record=json.loads(path.read_text())
            except Exception:continue
            identity=str(record.get('id') or '')
            try:identity=str(uuid.UUID(identity))
            except Exception:continue
            if record.get('status') not in {'accepted','waiting','recording','finalizing'}:continue
            with self.lock:
                if identity in self.active:continue
                self.active.add(identity)
            try:threading.Thread(target=self._watch,args=(identity,record['owner']),
                                 daemon=True,name='telemost-resume').start()
            except Exception:pass

def meetings_for(service):
    if not hasattr(service,'_telemost_meetings'):service._telemost_meetings=Meetings(service)
    return service._telemost_meetings
