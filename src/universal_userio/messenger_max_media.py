"""Bounded media bytes from an actual MAX attachment, never caller URLs.

Native attachment tokens remain in memory. Images use a first frame, audio
uses the existing fleet Whisper service/key seam. No uploads or new login.
"""
from __future__ import annotations
import base64
import hashlib
import io
import json
import os
import subprocess
import urllib.parse
import urllib.request
import uuid


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('provider media redirect refused')


def fetch_native_url(url, *, maximum, opener=None):
    parsed=urllib.parse.urlsplit(str(url))
    host=parsed.hostname or ''
    if (parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in {None,443}
            or not any(host==d or host.endswith('.'+d) for d in ('oneme.ru','mycdn.me','okcdn.ru'))):
        raise ValueError('actual provider CDN unavailable')
    opener=opener or urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open
    with opener(url,timeout=15) as response:
        data=response.read(maximum+1)
        mime=str(response.headers.get('Content-Type') or '').split(';')[0]
    if not data or len(data)>maximum: raise ValueError('provider media exceeds bounded limit')
    return data,mime


def image_content(data, kind):
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        if image.width*image.height>4*1024*1024: raise ValueError('provider image exceeds pixel budget')
        image.seek(0)
        out=io.BytesIO();image.convert('RGBA').save(out,format='PNG')
        data=out.getvalue()
    if len(data)>2*1024*1024: raise ValueError('provider first frame exceeds image budget')
    return {'kind':kind,'status':'ready','image':{'mimeType':'image/png','dataBase64':base64.b64encode(data).decode()},
            'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'mimeType':'image/png','partial':False}


def transcribe(data, mime, *, diarize=False):
    # Same protected key loader as deploy/telegram-qr-connector/transcription.mjs.
    key=next((os.environ.get(k,'').strip() for k in ('USERIO_STT_API_KEY','USERIO_WHISPER_API_KEY','GREPMESH_STT_API_KEY')
              if os.environ.get(k,'').strip()),'')
    if not key:
        result=subprocess.run(['/usr/local/bin/node','/home/roomhacker/agents-projects/simple-secret-storage/bin/sss-get.mjs',
                               'KANBAN_WHISPER_API_KEY'],capture_output=True,text=True,timeout=20)
        if result.returncode==0:key=result.stdout.strip()
    if not key:raise ValueError('existing transcription credential unavailable')
    model=os.environ.get('USERIO_STT_MODEL') or os.environ.get('USERIO_WHISPER_MODEL') or 'whisper-1'
    response_format='verbose_json' if diarize else 'json'
    boundary='userio-'+uuid.uuid4().hex
    prefix=(f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{model}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="response_format"\r\n\r\n{response_format}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="voice.m4a"\r\n'
            f'Content-Type: {mime or "audio/mp4"}\r\n\r\n').encode()
    if diarize:
        prefix=(f'--{boundary}\r\nContent-Disposition: form-data; name="diarize"\r\n\r\ntrue\r\n').encode()+prefix
    url=os.environ.get('USERIO_STT_URL') or os.environ.get('USERIO_WHISPER_URL') or 'http://192.168.2.75:7653/v1/audio/transcriptions'
    req=urllib.request.Request(url,data=prefix+data+f'\r\n--{boundary}--\r\n'.encode(),
         headers={'Authorization':'Bearer '+key,'Content-Type':'multipart/form-data; boundary='+boundary})
    with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(req,timeout=45) as response:
        raw=response.read(131073)
    if len(raw)>131072:raise ValueError('transcription response exceeds limit')
    payload=json.loads(raw)
    text=payload.get('text')
    if not isinstance(text,str):raise ValueError('actual transcription unavailable')
    result={'kind':'voice','status':'ready','transcript':text[:32000],'partial':len(text)>32000,
            'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'mimeType':mime}
    if diarize:
        result['diarization']={k:payload[k] for k in ('speaker_text','diarization_model','segments','speaker_turns') if k in payload}
        if 'segments' not in result['diarization']:
            raise ValueError('requested diarization unavailable; audio retained')
    return result


def read_attachment(client, *, peer, message_id, attachment):
    typ=str(attachment.get('_type') or '')
    if typ=='PHOTO':
        data,mime=fetch_native_url(attachment.get('baseUrl'),maximum=2*1024*1024)
        return image_content(data,'photo')
    if typ not in {'AUDIO','FILE'}:raise ValueError('MAX media kind unavailable')
    ref=client.media_reference(chat_id=peer,message_id=message_id,attachment=attachment)
    # Native download metadata; no path/URL accepted from the MCP caller.
    url=ref.get('url') or ref.get('downloadUrl')
    if typ=='AUDIO':url=url or ref.get('MP4_720') or ref.get('MP4_480') or ref.get('MP4_360') or ref.get('MP4_240')
    data,mime=fetch_native_url(url,maximum=20*1024*1024 if typ=='AUDIO' else 2*1024*1024)
    if typ=='AUDIO':return transcribe(data,mime)
    if mime.startswith('image/'):return image_content(data,'file')
    name=str(attachment.get('name') or attachment.get('fileName') or '')
    if mime.startswith('text/') or name.lower().endswith(('.txt','.csv','.md','.json','.log')):
        text=data.decode('utf-8',errors='strict')
        return {'kind':'file','status':'ready','text':text[:32000],'partial':len(text)>32000,
                'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'mimeType':mime}
    return {'kind':'file','status':'unsupported_content_type','mimeType':mime,'bytes':len(data),
            'sha256':hashlib.sha256(data).hexdigest(),'partial':True}
