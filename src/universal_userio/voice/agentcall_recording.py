"""Private aligned recordings of received/sent AudioSocket PCM, not STT labels."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import time
import uuid
import wave

SAMPLE_RATE=8000
ARCHIVE_LIMIT=5*1024**3
MAX_CALL_SECONDS=2*60*60
RESERVATION=256*1024
ZERO=b'\0'*65536


class RecordingLimit(RuntimeError):pass


@contextmanager
def archive_guard(root,extraBytes=0):
    """Shared producer/transcriber quota, before a new owned write/reservation."""
    if type(extraBytes) is not int or extraBytes<0:raise ValueError('invalid archive growth')
    root=Path(root);owner=root.stat()
    fd=os.open(root/'.capacity.lock',os.O_RDWR|os.O_CREAT,0o600)
    try:
        os.fchown(fd,owner.st_uid,owner.st_gid)
        deadline=time.monotonic()+.25
        while True:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()>=deadline:raise RecordingLimit('archive_capacity_busy')
                time.sleep(.005)
        total=0
        for base,dirs,files in os.walk(root,followlinks=False):
            dirs[:]=[d for d in dirs if not (Path(base)/d).is_symlink()]
            for name in files:
                p=Path(base)/name
                if not p.is_symlink():total+=p.stat().st_size
        if total+extraBytes>ARCHIVE_LIMIT:raise RecordingLimit('archive_capacity_exhausted')
        yield
    finally:os.close(fd)


class CallRecorder:
    def __init__(self,call_id,direction,peer_fields,directory=None,*,clock=time.monotonic,sample_rate=8000):
        if sample_rate not in (8000,12000,16000,24000,32000,44100,48000):raise ValueError('invalid recording sample rate')
        self.sample_rate=sample_rate
        self.call_id=str(uuid.UUID(call_id))
        if direction not in {'incoming','outgoing'}:raise ValueError('invalid call direction')
        self.direction=direction
        self.peer_fields={k:str(v)[:512] for k,v in peer_fields.items() if k in {'peerRef','accountRef','peerLabel'}}
        self.root=Path(directory or os.getenv('AGENTCALL_RECORDING_DIRECTORY','/var/lib/agentcall/recordings'))
        self.path=self.root/self.call_id
        self.clock=clock;self.origin=clock();self.started_at=datetime.now(timezone.utc).isoformat()
        self.failure='';self.finished=False;self.result=None;self.created=False
        self.tracks={};self.samples={'caller':0,'assistant':0};self.recorded_frames={'caller':0,'assistant':0}
        try:
            self.root.mkdir(parents=True,mode=0o700,exist_ok=True)
            parent=self.root.stat()
            if self.root.is_symlink() or parent.st_mode & 0o077:raise PermissionError('archive must be private')
            self.owner=(parent.st_uid,parent.st_gid)
            self.path.mkdir(mode=0o700);self.created=True;os.chown(self.path,*self.owner)
            for name in self.samples:
                fd=os.open(self.path/(name+'.wav'),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                try:
                    os.fchown(fd,*self.owner)
                    file=os.fdopen(fd,'wb',buffering=65536)
                except Exception:
                    os.close(fd);raise
                self.tracks[name]={'file':file,'wave':None,'reserved':0}
                self._reserve(name,44)
                wav=wave.open(file,'wb');wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(self.sample_rate)
                self.tracks[name]['wave']=wav;wav.writeframesraw(b'')
            self._manifest(False,'recording')
        except Exception as error:self.fail(error)

    def fail(self,error):
        if not self.failure:
            self.failure=str(error)[:128] if isinstance(error,(RecordingLimit,str)) else type(error).__name__

    def _reserve(self,name,size):
        track=self.tracks[name]
        if size<=track['reserved']:return
        target=math.ceil(size/RESERVATION)*RESERVATION
        current=os.fstat(track['file'].fileno()).st_size
        with archive_guard(self.root,target-current):
            os.ftruncate(track['file'].fileno(),target)
            track['reserved']=target

    def _silence(self,name,count):
        while count:
            frames=min(count,len(ZERO)//2)
            self.tracks[name]['wave'].writeframesraw(ZERO[:frames*2])
            self.samples[name]+=frames;count-=frames

    def _record(self,name,pcm):
        if self.failure or self.finished:return
        try:
            if len(pcm)%2:raise ValueError('odd PCM length')
            at=max(0,round((self.clock()-self.origin)*self.sample_rate))
            target=max(at,self.samples[name]);end=target+len(pcm)//2
            if end>MAX_CALL_SECONDS*self.sample_rate:raise RecordingLimit('call_duration_exceeded')
            self._reserve(name,44+end*2)
            self._silence(name,target-self.samples[name])
            self.tracks[name]['wave'].writeframesraw(pcm)
            self.samples[name]=end;self.recorded_frames[name]+=1
        except Exception as error:self.fail(error)

    def incoming(self,pcm):self._record('caller',pcm)
    def outgoing(self,pcm):self._record('assistant',pcm)

    def _manifest(self,complete,state):
        data={'schemaVersion':1,'callId':self.call_id,'direction':self.direction,'peerFields':self.peer_fields,
              'tracks':{'caller':'caller.wav','assistant':'assistant.wav'},'sampleRate':self.sample_rate,
              'channels':1,'sampleWidth':2,'startedAt':self.started_at,'complete':complete,'state':state,
              'duration':max(self.samples.values())/self.sample_rate,'frames':dict(self.samples),
              'recordedFrames':dict(self.recorded_frames),'recordingFailure':self.failure,
              'trackDirections':{'caller':'received','assistant':'sent'}}
        if self.finished:data['finishedAt']=datetime.now(timezone.utc).isoformat()
        payload=(json.dumps(data,ensure_ascii=False,indent=2)+'\n').encode()
        temporary=self.path/('.manifest-'+uuid.uuid4().hex+'.tmp')
        with archive_guard(self.root,len(payload)):
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            try:
                try:os.fchown(fd,*self.owner)
                except Exception:os.close(fd);raise
                with os.fdopen(fd,'wb') as file:file.write(payload);file.flush();os.fsync(file.fileno())
                os.replace(temporary,self.path/'manifest.json')
                directory_fd=os.open(self.path,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(directory_fd)
                finally:os.close(directory_fd)
            finally:temporary.unlink(missing_ok=True)
        return self.path/'manifest.json'

    def finish(self):
        if self.finished:
            if self.result:return self.result
            raise RuntimeError('recording finalization failed')
        self.finished=True
        if not self.created:raise RuntimeError('recording directory unavailable; old archive retained')
        end=min(round(max(0,self.clock()-self.origin)*self.sample_rate),round(MAX_CALL_SECONDS*self.sample_rate))
        end=max(end,max(self.samples.values()))
        for name,track in self.tracks.items():
            try:
                if track['wave']:
                    self._reserve(name,44+end*2)
                    self._silence(name,end-self.samples[name])
                    track['wave'].close()
                    os.ftruncate(track['file'].fileno(),44+self.samples[name]*2)
                    os.fsync(track['file'].fileno())
            except Exception as error:self.fail(error)
            finally:
                if track['wave']:
                    try:track['wave'].close()
                    except Exception as error:self.fail(error)
                try:track['file'].close()
                except Exception as error:self.fail(error)
        try:self.result=self._manifest(not bool(self.failure),'incomplete' if self.failure else 'complete')
        except Exception as error:self.fail(error);raise RuntimeError('recording manifest unavailable') from None
        return self.result


class RecordingWriter:
    """Observe only complete AudioSocket packets accepted by the real writer."""
    def __init__(self,writer,recorder,on_failure=None):
        self.writer=writer;self.recorder=recorder;self.pending=bytearray();self.on_failure=on_failure
    def __getattr__(self,name):return getattr(self.writer,name)
    def write(self,data):
        self.writer.write(data)  # A rejected write must never become recorded speech.
        self.pending.extend(data)
        while len(self.pending)>=3:
            length=int.from_bytes(self.pending[1:3],'big')
            if len(self.pending)<3+length:break
            kind=self.pending[0];pcm=bytes(self.pending[3:3+length]);del self.pending[:3+length]
            if 0x10<=kind<=0x16:
                self.recorder.outgoing(pcm)
        if self.recorder.failure and self.on_failure:self.on_failure()
    def finish(self):
        if self.pending:self.recorder.fail(ValueError('partial AudioSocket write'))
        if self.recorder.failure and self.on_failure:self.on_failure()
