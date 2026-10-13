"""Fast unit: call admission, no replay, answer identity (expected 1s, max 10s)."""
import json
import uuid
from unittest.mock import Mock

import pytest
from universal_userio import matrix_calls, call_audio
from universal_userio.matrix_calls import MEMBER, MatrixCalls, fresh_answer


def test_matrix_binds_frozen_voice_preparation_without_mutating_phone_peer():
    from dataclasses import dataclass
    from universal_userio.matrix_call_worker import bind_prepared
    @dataclass(frozen=True)
    class Prepared:
        account_ref: str = 'phone'
        peer_ref: str = '+example'
        peer_label: str = 'Owner'
        greeting_pcm: bytes = b'voice'
    original = Prepared()
    bound = bind_prepared(original, {'history_account_ref':'matrix:owner',
        'room_id':'!room:hs','target':'@recipient:hs'})
    assert (bound.account_ref,bound.peer_ref,bound.peer_label)==('matrix:owner','!room:hs','@recipient:hs')
    assert bound.greeting_pcm is original.greeting_pcm
    assert original.account_ref == 'phone'


def test_matrix_server_speech_keeps_dialogue_settings_without_phone(monkeypatch):
    from dataclasses import dataclass
    from universal_userio.matrix_call_worker import server_voice_config
    @dataclass(frozen=True)
    class Config:
        tts_provider: str = 'android-phone'
        tts_engine: str = 'android'
        tts_url: str = 'phone'
        speech_speed: float = 1.35
        model: str = 'existing'
    monkeypatch.setenv('AGENTCALL_TTS_SILERO_URL','http://server/synthesize')
    original=Config();bound=server_voice_config(original)
    assert (bound.tts_provider,bound.tts_url)==('qwen-warm','http://server/synthesize')
    assert (bound.speech_speed,bound.model)==(original.speech_speed,original.model)
    assert original.tts_provider=='android-phone'


def test_only_fresh_target_membership_is_an_answer():
    event = {'type':MEMBER,'sender':'@b:hs','event_id':'$new','origin_server_ts':101,
             'content':{'application':'m.call','expires':1000,
                        'foci_preferred':[{'type':'livekit','livekit_service_url':'https://rtc'}]}}
    assert fresh_answer([event],'@b:hs',100,{'$old'},'https://rtc')
    for change in ({'sender':'@other:hs'},{'origin_server_ts':99},{'event_id':'$old'},
                   {'content':{}},{'content':dict(event['content'],expires=0)}):
        assert not fresh_answer([dict(event,**change)],'@b:hs',100,{'$old'},'https://rtc')
    assert not fresh_answer([event],'@b:hs',100,set(),'https://different')


def test_restart_never_replays_an_admitted_call(tmp_path, monkeypatch):
    service=Mock();service._store.capability_enabled.return_value=True
    service._store.default_user_id='owner'
    service._store.accounts.return_value=[{'id':'matrix:caller','provider':'matrix',
        'enabled':True,'display_name':'@a:hs','credential_ref':'connector:test'}]
    monkeypatch.setattr(matrix_calls.ConnectorSecrets,'load',lambda *a,**k:
        {'homeserver':'https://hs','access_token':'private','matrix_user_id':'@a:hs'})
    monkeypatch.setattr(matrix_calls,'request_json',lambda *a,**k:{'user_id':'@a:hs'})
    monkeypatch.setattr(matrix_calls.threading.Thread,'start',lambda self:None)
    args=dict(user_id='owner',account_ref='matrix:caller',room_id='!r:hs',target='@b:hs',
              purpose='Проверить голос',request_id='once-unique-request',confirm=True)
    first=MatrixCalls(service,tmp_path).start(**args)
    after_restart=MatrixCalls(service,tmp_path)
    assert after_restart.start(**args)==dict(first,status='unknown')
    assert not after_restart.active
    with pytest.raises(ValueError,match='conflict'):after_restart.start(**dict(args,purpose='Другой звонок'))
    with pytest.raises(KeyError):after_restart.get(first['call_id'],'another-user')
    with pytest.raises(ValueError,match='confirmation'):after_restart.start(**dict(args,confirm=False))


@pytest.mark.parametrize('rate',[8000,48000])
def test_matrix_recording_transcript_is_bound_to_room_and_account(tmp_path,rate):
    import wave
    call_id=str(uuid.uuid4()); folder=tmp_path/call_id;folder.mkdir()
    for track in ('caller','assistant'):
        with wave.open(str(folder/(track+'.wav')),'wb') as audio:
            audio.setparams((1,2,rate,0,'NONE',''));audio.writeframes(b'\0\0'*rate)
    (folder/'manifest.json').write_text(json.dumps({'callId':call_id,'complete':True,'sampleRate':rate,
        'peerFields':{'accountRef':'matrix:owned','peerRef':'!room:hs'}}))
    (folder/'live-transcript.json').write_text(json.dumps({'caller':'Да, слышу.','assistant':'Здравствуйте.'}))
    conv={'id':'conv','source':'matrix:owned','account_ref':'matrix:owned','peer_id':'!room:hs'}
    msg={'source':'matrix:owned','conversation_id':'conv','sender':call_id,'message_id':'ended'}
    audio=call_audio.recording(conv,msg,tmp_path)
    assert [t['transcript'] for t in audio['tracks']]==['Да, слышу.','Здравствуйте.']
    assert audio['tracks'][1]['label']=='ИИ'
    assert audio['duration']==1
    assert call_audio.recording(dict(conv,peer_id='!different:hs'),msg,tmp_path) is None
    assert call_audio.recording(dict(conv,account_ref='matrix:other'),msg,tmp_path) is None
