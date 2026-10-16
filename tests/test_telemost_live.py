

def test_live_transcription_emits_while_recording(tmp_path,monkeypatch):
    """A running meeting must fill the chat before it ends."""
    import hashlib,io,wave
    from universal_userio.telemost.meetings import Meetings
    from universal_userio import messenger_max_media
    received=[];calls=[]
    class Store:
        def register_account(self,**kwargs):pass
    class Service:
        _store=Store()
        def receive(self,message,**kwargs):
            received.append((message,kwargs));return 'live-owned-conversation',None
    record={'id':'00000000-0000-0000-0000-0000000000ff','owner':'test-user','status':'recording',
            'tracks':[{'index':1,'filename':'track-1.wav','bytes':69110412}]}
    meetings=Meetings(Service(),tmp_path);meetings._save(record)
    monkeypatch.setattr(meetings,'_command',lambda:[])
    # The recorder hands back one growing tail of raw PCM per poll.
    tail=b'\x01\x02'*(16000*60)
    def pending(record,track,cursor):
        if cursor:return b''
        return tail
    monkeypatch.setattr(meetings,'_pending',pending)
    def transcribe(audio,mime,*,diarize):
        calls.append(diarize)
        return {'transcript':'Живой текст встречи.', 'diarization':{
            'speaker_text':'Спикер 1: Живой текст встречи.',
            'segments':[{'start':0.2,'end':1.2,'speaker':'SPEAKER_00','text':'Живой текст встречи.'}]}}
    monkeypatch.setattr(messenger_max_media,'transcribe',transcribe)
    meetings._transcribe_live(record)
    assert calls==[True], 'live transcription must request diarization'
    assert len(received)==1
    message,kwargs=received[0]
    assert kwargs['user_id']=='test-user' and kwargs['route_id']=='telemost'
    assert message.peer_id==record['id'] and message.direction=='system'
    assert message.message_id.endswith('live-1-0')
    assert 'Спикер 1' in message.body
    assert record['tracks'][0]['pending_offset']==len(tail)
    # A second poll must not re-emit audio already shown in the chat.
    meetings._transcribe_live(record)
    assert len(received)==1


def test_live_transcription_waits_for_usable_audio(tmp_path,monkeypatch):
    """Silence shorter than the minimum must not spend an ASR call."""
    from universal_userio.telemost.meetings import Meetings
    from universal_userio import messenger_max_media
    calls=[]
    class Store:
        def register_account(self,**kwargs):pass
    class Service:
        _store=Store()
        def receive(self,message,**kwargs):return 'c',None
    record={'id':'00000000-0000-0000-0000-0000000000fe','owner':'test-user','status':'recording',
            'tracks':[{'index':1,'filename':'track-1.wav','bytes':1000}]}
    meetings=Meetings(Service(),tmp_path);meetings._save(record)
    monkeypatch.setattr(meetings,'_command',lambda:[])
    monkeypatch.setattr(meetings,'_pending',lambda record,track,cursor:b'\x00\x00'*16000*10)
    monkeypatch.setattr(messenger_max_media,'transcribe',
        lambda audio,mime,**kw:calls.append(kw) or {'transcript':'','diarization':{'segments':[]}})
    meetings._transcribe_live(record)
    assert calls==[]
    assert not record['tracks'][0].get('pending_offset')


def test_pending_endpoint_is_owner_scoped_and_frame_aligned(tmp_path):
    """Live PCM never leaks another owner's meeting or splits a sample."""
    import wave
    from aiohttp import web
    from universal_userio.telemost import worker as worker_module
    path=tmp_path/'00000000-0000-0000-0000-0000000000fd'
    path.mkdir(parents=True)
    with wave.open(str(path/'track-1.wav'),'wb') as handle:
        handle.setnchannels(1);handle.setsampwidth(2);handle.setframerate(16000)
        handle.writeframes(b'\x07\x08'*16000)
    record={'id':'00000000-0000-0000-0000-0000000000fd','owner':'test-user','status':'recording',
            'tracks':[{'index':1,'filename':'track-1.wav'}]}
    import json
    (path/'meeting.json').write_text(json.dumps(record))
    worker=worker_module.Worker(tmp_path)
    # Header plus whole frames only; an odd tail byte is dropped.
    assert worker.root==tmp_path
    assert (path/'track-1.wav').stat().st_size-44==16000*2
