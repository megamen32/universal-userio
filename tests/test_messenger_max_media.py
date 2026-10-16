"""Focused integration: expected1s/max15s; native media URL/bytes, no network/ASR."""
import io
import pytest
from universal_userio.messenger_max_media import fetch_native_url,image_content,read_attachment

class Response(io.BytesIO):
    headers={'Content-Type':'text/plain'}
    def __enter__(self):return self
    def __exit__(self,*args):self.close()


def test_media_cdn_bound_and_oversize_refusal():
    opened=[]
    def opener(url,**kw):opened.append(url);return Response(b'actual text')
    assert fetch_native_url('https://file-ms.oneme.ru/exact',maximum=64,opener=opener)==(b'actual text','text/plain')
    for url in ('http://i.oneme.ru/exact','https://oneme.ru.evil.test/file','https://127.0.0.1/file','https://user:password@i.mycdn.me/file'):
        with pytest.raises(ValueError):fetch_native_url(url,maximum=64,opener=opener)
    assert len(opened)==1
    with pytest.raises(ValueError):fetch_native_url('https://i.oneme.ru/large',maximum=2,opener=opener)


def test_animated_photo_returns_real_first_frame():
    from PIL import Image
    img=Image.new('RGB',(2,2),'red');buf=io.BytesIO();img.save(buf,format='GIF')
    result=image_content(buf.getvalue(),'photo')
    assert result['image']['mimeType']=='image/png' and result['bytes']>0 and result['status']=='ready'


def test_native_audio_metadata_and_transcript_preserve_source(monkeypatch):
    from universal_userio import messenger_max_media as m
    calls=[]
    class Client:
        def media_reference(self,**kw):calls.append(kw);return {'MP4_720':'https://maxvd1.okcdn.ru/actual'}
    monkeypatch.setattr(m,'fetch_native_url',lambda url,maximum:(b'actual audio','audio/mp4'))
    monkeypatch.setattr(m,'transcribe',lambda data,mime:{'kind':'voice','transcript':'actual recognized speech','status':'ready'})
    result=read_attachment(Client(),peer='12',message_id='34',attachment={'_type':'AUDIO','audioId':56})
    assert result['transcript']=='actual recognized speech'
    assert calls==[{'chat_id':'12','message_id':'34','attachment':{'_type':'AUDIO','audioId':56}}]


def test_meeting_asr_requests_diarization_and_preserves_source_segments(monkeypatch):
    """Fast unit expected1/max10s: exact existing form flag and ASR result."""
    import json
    from universal_userio import messenger_max_media as m
    requests=[]
    segments=[{'start':1.2,'end':2.4,'speaker':'SPEAKER_00','text':'Первый вопрос.'},
              {'start':3.0,'end':4.0,'speaker':'SPEAKER_01','text':'Второй ответ.'}]
    payload={'text':'Первый вопрос. Второй ответ.','speaker_text':'SPEAKER_00: Первый вопрос.\nSPEAKER_01: Второй ответ.',
             'segments':segments,'speaker_turns':segments,'diarization_model':'actual-diarizer'}
    class Opener:
        def open(self,request,timeout):
            requests.append(request)
            return Response(json.dumps(payload).encode())
    monkeypatch.setenv('USERIO_STT_API_KEY','local-test-placeholder')
    monkeypatch.setattr(m.urllib.request,'build_opener',lambda *args:Opener())
    diarized=m.transcribe(b'actual fixture','audio/wav',diarize=True)
    plain=m.transcribe(b'actual fixture','audio/wav')
    assert b'name="diarize"' in requests[0].data and b'\r\ntrue\r\n' in requests[0].data
    assert b'verbose_json' in requests[0].data
    assert diarized['diarization']['segments']==segments
    assert diarized['diarization']['speaker_text']==payload['speaker_text']
    assert diarized['transcript']==payload['text']
    assert b'name="diarize"' not in requests[1].data and 'diarization' not in plain


def test_requested_diarization_never_silently_becomes_plain_text(monkeypatch):
    from universal_userio import messenger_max_media as m
    class Opener:
        def open(self,*args,**kwargs):return Response(b'{"text":"No speaker evidence"}')
    monkeypatch.setenv('USERIO_STT_API_KEY','local-test-placeholder')
    monkeypatch.setattr(m.urllib.request,'build_opener',lambda *args:Opener())
    with pytest.raises(ValueError,match='diarization unavailable'):
        m.transcribe(b'fixture','audio/wav',diarize=True)
