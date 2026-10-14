"""Post-call transcription of actual, separate AudioSocket directions.

The existing systemd service manager owns one bounded job per immutable call.
It survives gateway restarts; no model/tool turn or phone action is replayed.
"""
import audioop
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import uuid
import wave

CHUNK_SECONDS=60
JOB_SECONDS=1100


def write_bytes(path, payload):
    from agentcall_recording import archive_guard
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    # Account for both the previous result and its atomic replacement.
    with archive_guard(path.parent.parent,len(payload)):
        try:
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as f:
                f.write(payload);f.flush();os.fsync(f.fileno())
            owner=path.parent.stat()
            if os.geteuid()==0:os.chown(temporary,owner.st_uid,owner.st_gid)
            os.replace(temporary,path)
        finally:temporary.unlink(missing_ok=True)


def write_json(path, data):
    write_bytes(path,json.dumps(data,ensure_ascii=False,indent=2).encode())


def validate_manifest(value):
    root=Path(os.environ.get('AGENTCALL_RECORDING_DIRECTORY','/var/lib/agentcall/recordings')).resolve()
    path=Path(value)
    if path.is_symlink():raise ValueError('recording manifest symlink')
    path=path.resolve(strict=True)
    if path.name!='manifest.json' or path.parent.parent!=root:
        raise ValueError('recording outside archive')
    if path.stat().st_size>65536:raise ValueError('recording manifest capacity')
    data=json.loads(path.read_text())
    call=str(uuid.UUID(data['callId']))
    rate=data.get('sampleRate')
    if call!=path.parent.name or rate not in (8000,12000,16000,24000,32000,44100,48000):
        raise ValueError('recording call or rate mismatch')
    if data.get('tracks')!={'caller':'caller.wav','assistant':'assistant.wav'}:
        raise ValueError('recording tracks mismatch')
    for filename in data['tracks'].values():
        track=path.parent/filename
        if track.is_symlink() or track.resolve().parent!=path.parent:
            raise ValueError('recording track outside call')
        with wave.open(str(track),'rb') as w:
            if (w.getnchannels(),w.getsampwidth(),w.getframerate())!=(1,2,rate) or w.getnframes()>2*3600*rate:
                raise ValueError('recording audio contract')
    return path,data


def submit_transcription(value):
    path,data=validate_manifest(value)
    from agentcall_recording import archive_guard
    claim=path.parent/'transcription-job.json'
    job={'callId':data['callId'],'status':'QUEUED','unit':'agentcall-transcribe@'+data['callId']+'.service'}
    payload=json.dumps(job).encode()
    with archive_guard(path.parent.parent,0):
        if claim.exists():return json.loads(claim.read_text())
    with archive_guard(path.parent.parent,len(payload)):
        try:fd=os.open(claim,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        except FileExistsError:return json.loads(claim.read_text())
        with os.fdopen(fd,'wb') as f:
            f.write(payload);f.flush();os.fsync(f.fileno())
        owner=path.parent.stat()
        if os.geteuid()==0:os.chown(claim,owner.st_uid,owner.st_gid)
    try:
        subprocess.run(['systemctl','start','--no-block',job['unit']],check=True,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5)
    except Exception as error:
        job.update(status='SUBMIT_FAILED',error=type(error).__name__)
        write_json(claim,job)
        raise RuntimeError('post-call transcription could not be queued') from None
    return job


def post_stt(pcm, rate):
    # Use the gateway's exact supported STT configuration, including defaults.
    # Importing its configuration does not start a worker, model or call.
    from agentcall_realtime_bridge import BackendConfig
    config=BackendConfig.from_environment()
    wav=io.BytesIO()
    with wave.open(wav,'wb') as w:
        w.setparams((1,2,rate,0,'NONE','not compressed'));w.writeframes(pcm)
    boundary='phone-recording-'+uuid.uuid4().hex
    body=bytearray()
    for key,value in {'model':'whisper-1','language':config.language,
                      'response_format':'verbose_json','vad_filter':'true','cache':'false'}.items():
        body.extend(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+key+'"\r\n\r\n'+value+'\r\n').encode())
    body.extend(('--'+boundary+'\r\nContent-Disposition: form-data; name="file"; filename="recording.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode())
    body.extend(wav.getvalue());body.extend(('\r\n--'+boundary+'--\r\n').encode())
    headers={'Content-Type':'multipart/form-data; boundary='+boundary}
    key=config.stt_api_key
    if key:headers['Authorization']='Bearer '+key
    request=urllib.request.Request(config.stt_url,data=body,headers=headers)
    with urllib.request.urlopen(request,timeout=min(60,config.request_timeout)) as response:raw=response.read(1048577)
    if len(raw)>1048576:raise ValueError('STT response capacity')
    result=json.loads(raw)
    text=result.get('text')
    if not isinstance(text,str):raise ValueError('STT response missing text')
    return result


def digital_silence(pcm):
    return not pcm or audioop.max(audioop.bias(pcm,2,-audioop.avg(pcm,2)),2)<=1


def recognized_segments(value, offset, duration):
    if isinstance(value,str):value={'text':value}
    if not isinstance(value,dict) or not isinstance(value.get('text'),str):
        raise ValueError('STT text contract')
    if 'segments' not in value:
        # Preserve text-only responses, but never call their whole chunk a
        # speech interval or infer overlap from it.
        return [{'start':offset,'end':offset+duration,'text':value['text'].strip(),
                 'timingSource':'chunk'}] if value['text'].strip() else []
    items=value['segments']
    if not isinstance(items,list) or len(items)>1000:raise ValueError('STT segment capacity')
    result=[]
    for item in items:
        if not isinstance(item,dict) or not isinstance(item.get('text'),str):raise ValueError('STT segment text')
        begin,end=item.get('start'),item.get('end')
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (begin,end)):
            raise ValueError('STT segment timestamp')
        if not 0<=begin<min(end,duration) or end>duration+0.02:raise ValueError('STT segment outside audio')
        if item['text'].strip():result.append({'start':offset+begin,'end':offset+min(end,duration),
            'text':item['text'].strip(),'timingSource':'provider_segment'})
    if value['text'].strip() and not result:raise ValueError('STT text without timed segments')
    return result


def timeline_rows(tracks):
    speech={role:[s for s in tracks.get(role,{}).get('segments',[])
                  if s.get('timingSource')=='provider_segment'] for role in ('caller','assistant')}
    points=sorted({v for items in speech.values() for s in items for v in (s['start'],s['end'])})
    rows=[]
    for begin,end in zip(points,points[1:]):
        active={role:[s for s in items if s['start']<end and s['end']>begin] for role,items in speech.items()}
        if any(active.values()):rows.append(dict(start=begin,end=end,overlap=bool(active['caller'] and active['assistant']),**active))
    return rows


def write_timeline(directory,result,suffix):
    lines=['# Две параллельные дорожки звонка','',
        'Текст относится к указанному речевому сегменту целиком. Границы пересечений не означают точные времена отдельных слов.',
        '', '| Время от начала звонка | Собеседник | Наш помощник | Пересечение |',
        '| --- | --- | --- | --- |']
    def cell(items):
        return '<br>'.join(f"[{s['start']:.2f}–{s['end']:.2f} с] "+s['text'].replace('|','\\|').replace('\n',' ') for s in items) or '—'
    for row in result['timeline']:
        lines.append(f"| {row['start']:.2f}–{row['end']:.2f} с | {cell(row['caller'])} | {cell(row['assistant'])} | {'Да' if row['overlap'] else '—'} |")
    if any(s.get('timingSource')!='provider_segment' for track in result['tracks'].values() for s in track['segments']):
        lines+=['','Для части текста провайдер не вернул границы речи. Этот текст сохранён отдельно и не использован для вычисления пересечений.']
    if not result['timeline']:lines+=['','Подтверждённых речевых интервалов нет.']
    write_bytes(directory/('timeline'+suffix+'.md'),'\n'.join(lines).encode())


def finish_job(claim, result):
    if not claim.exists():return
    job=json.loads(claim.read_text())
    # A recovered systemd submission error is history, not a failed result.
    # Reconcile cached results without submitting audio to the provider again.
    previous_error=job.pop('error',None)
    if previous_error and job.get('status') in ('SUBMIT_FAILED','COMPLETE'):
        job.setdefault('submissionError',previous_error)
    job['status']=result['status']
    if result.get('error'):job['error']=result['error']
    write_json(claim,job)


def transcribe_manifest(value, *, transcribe=None, timeline_revision=False):
    path,data=validate_manifest(value)
    revision=2 if timeline_revision is True else timeline_revision
    if revision not in (False,2,3):raise ValueError('unsupported timeline revision')
    suffix='-v'+str(revision) if revision else ''
    output=path.parent/('transcript'+suffix+'.json')
    claim=path.parent/('timeline-job'+suffix+'.json' if revision else 'transcription-job.json')
    if output.exists():
        previous=json.loads(output.read_text())
        if previous.get('status')=='PROCESSING':
            previous.update(status='INCOMPLETE',error='InterruptedJob')
            write_json(output,previous)
        finish_job(claim,previous)
        return previous
    if timeline_revision:
        # An explicitly requested new analysis never overwrites old evidence.
        # Claim precedes any provider admission, including interrupted startup.
        from agentcall_recording import archive_guard
        payload=json.dumps({'callId':data['callId'],'status':'PROCESSING','purpose':'parallel speech timelines '+str(revision)}).encode()
        with archive_guard(path.parent.parent,len(payload)):
            try:fd=os.open(claim,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            except FileExistsError:raise RuntimeError('timeline analysis already claimed; no replay') from None
            with os.fdopen(fd,'wb') as f:f.write(payload);f.flush();os.fsync(f.fileno())
            owner=path.parent.stat()
            if os.geteuid()==0:os.chown(claim,owner.st_uid,owner.st_gid)
    result={'schema':'agentcall.recording-transcript.v1','callId':data['callId'],
            'direction':data.get('direction'),'recordingComplete':data.get('complete') is True,
            'status':'PROCESSING','speakerSource':'AudioSocket directions, not recognition or names',
            'tracks':{role:{'label':label,'audioFile':filename,'segments':[]} for role,label,filename in
                [('caller','Собеседник','caller.wav'),('assistant','Наш помощник','assistant.wav')]}}
    write_json(output,result)
    start=time.monotonic();request=transcribe or post_stt
    try:
        for role in ('caller','assistant'):
            with wave.open(str(path.parent/data['tracks'][role]),'rb') as w:
                position=0;rate=w.getframerate()
                while True:
                    pcm=w.readframes(CHUNK_SECONDS*rate)
                    if not pcm:break
                    if time.monotonic()-start>JOB_SECONDS:raise TimeoutError('transcription deadline')
                    seconds=len(pcm)/(2*rate)
                    if not digital_silence(pcm):
                        segments=recognized_segments(request(pcm,rate),position/rate,seconds)
                        if segments:
                            result['tracks'][role]['segments'].extend(segments)
                            write_json(output,result)
                    position+=len(pcm)//2
        result['status']='COMPLETE' if result['recordingComplete'] else 'INCOMPLETE'
    except Exception as error:
        result.update(status='INCOMPLETE',error=type(error).__name__)
    result['processingSeconds']=round(time.monotonic()-start,3)
    result['timeline']=timeline_rows(result['tracks'])
    result['timelineStatus']='UNAVAILABLE' if any(s.get('timingSource')!='provider_segment'
        for track in result['tracks'].values() for s in track['segments']) else 'AVAILABLE'
    result['timingGranularity']='provider speech segments; not individual words'
    write_json(output,result)
    text=[]
    for role in ('caller','assistant'):
        text.append(result['tracks'][role]['label']+':')
        text.extend(f"[{item['start']:.1f}–{item['end']:.1f} с] {item['text']}" for item in result['tracks'][role]['segments'])
        text.append('')
    write_bytes(path.parent/('transcript'+suffix+'.txt'),'\n'.join(text).encode())
    write_timeline(path.parent,result,suffix)
    finish_job(claim,result)
    return result


def main():
    os.umask(0o077)
    revision=3 if '--timeline-v3' in sys.argv[2:] else '--timeline-v2' in sys.argv[2:]
    result=transcribe_manifest(sys.argv[1],timeline_revision=revision)
    print(json.dumps({'callId':result['callId'],'status':result['status']},ensure_ascii=False))
    return 0 if result['status']=='COMPLETE' else 1


if __name__=='__main__':sys.exit(main())
