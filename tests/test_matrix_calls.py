"""Fast unit: call admission, no replay, answer identity (expected 1s, max 10s)."""
import json
import uuid
from unittest.mock import Mock

import pytest
from universal_userio import matrix_calls, call_audio
from universal_userio.matrix_calls import MEMBER, MatrixCalls, fresh_answer


def test_late_barge_in_clears_the_rtc_tail_while_playout_is_waiting():
    # Fast unit expected<1s/max10s; detects interruption after final drain.
    import asyncio
    from universal_userio.matrix_call_worker import finish_rtc_audio
    async def check():
        started=asyncio.Event();released=asyncio.Event();stopped=asyncio.Event()
        class Source:
            cleared=0
            async def wait_for_playout(self):
                started.set();await released.wait()
            def clear_queue(self):
                self.cleared+=1;released.set()
        source=Source();task=asyncio.create_task(finish_rtc_audio(source,stopped.is_set))
        await started.wait();assert not task.done()
        stopped.set();await asyncio.wait_for(task,1)
        assert source.cleared==1
    asyncio.run(check())


def test_noticeplace_call_stops_escalation_once_the_user_answers(monkeypatch):
    call_id=str(uuid.uuid4())
    record={'call_id':call_id,'target':'@owner:hs','channel':'matrix','status':'active',
        'answered':True,'acknowledged':False}
    class Calls:
        active={call_id:object()}
        lookups=0
        def get(self,_call_id,_user_id):
            self.lookups+=1
            if self.lookups==1: raise KeyError('call not found')
            return dict(record)
        def start(self,**_): return dict(record)
    calls=Calls()
    monkeypatch.setattr(matrix_calls,'calls_for',lambda _service:calls)
    monkeypatch.setenv('USERIO_MATRIX_NOTICE_ACCOUNT','matrix:owner')
    monkeypatch.setenv('USERIO_MATRIX_NOTICE_ROOM','!room:hs')
    monkeypatch.setenv('USERIO_MATRIX_NOTICE_TARGET','@owner:hs')
    result=matrix_calls.noticeplace_call(None,'owner',{'incident_id':'incident-test',
        'title':'Проверка','body':'Состояние сервиса'})
    assert calls.lookups==1
    assert result['answered'] is True
    assert result['acknowledged'] is False
    assert result['ended'] is False
    assert result['safe_to_fallback'] is False


def test_rtc_tail_accepts_the_previous_gateway_during_rolling_deployment():
    import asyncio
    from universal_userio.matrix_call_worker import finish_rtc_audio
    async def check():
        released=asyncio.Event()
        class Source:
            cleared=0
            async def wait_for_playout(self):await released.wait()
            def clear_queue(self):self.cleared+=1;released.set()
        source=Source()
        await asyncio.wait_for(finish_rtc_audio(source,interrupted=True),1)
        assert source.cleared==1
        await finish_rtc_audio(source,interrupted=False)
        assert source.cleared==1
    asyncio.run(check())


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


def test_non_owner_uses_its_owned_matrix_account_and_model_alias(tmp_path, monkeypatch):
    service=Mock();service._store.default_user_id='owner'
    service._store.capability_enabled.return_value=True
    service._store.accounts.return_value=[{'id':'matrix:other','provider':'matrix','enabled':True,
        'display_name':'@other:hs','credential_ref':'connector:other'}]
    sentinel='sentinel-token-only-in-ephemeral-request'
    service._store.ai_settings.return_value={'endpoint':'https://api','model':'omniroute/minimax-combo-m3.1','token':sentinel}
    monkeypatch.setattr(matrix_calls.ConnectorSecrets,'load',lambda *a,**k:
        {'homeserver':'https://hs','access_token':'private','matrix_user_id':'@other:hs'})
    monkeypatch.setattr(matrix_calls,'request_json',lambda *a,**k:{'user_id':'@other:hs'})
    created=[]
    monkeypatch.setattr(matrix_calls.threading.Thread,'start',lambda self:created.append(self))
    record=MatrixCalls(service,tmp_path).start(user_id='other',account_ref='matrix:other',room_id='!r:hs',
        target='@b:hs',purpose='Проверить голос',request_id='other-unique-request',confirm=True)
    assert record['user_id']=='other'
    assert record['agent_context']['call_mode']=='incoming_guest'
    from types import SimpleNamespace
    from universal_userio.voice.agentcall_realtime_bridge import FastAgentIncidentBrain
    guest_brain=object.__new__(FastAgentIncidentBrain)
    guest_brain.config=SimpleNamespace(fast_agent_enabled=True, fast_agent_bin='/bin/true',
        fast_agent_python='/bin/true', fast_agent_mcp_script='/bin/true', llm_api_key='test-key')
    guest_brain.agent_context=record['agent_context']
    assert guest_brain.available is False
    assert record['voice_model']=='omniroute/minimax-combo-m3.1'
    durable=json.loads((tmp_path/record['call_id']/'call.json').read_text())
    assert durable['agent_context']['call_mode']=='incoming_guest'
    assert sentinel not in json.dumps(record)
    assert sentinel not in json.dumps(durable)
    assert len(created)==1 and created[0]._args[-1]['token']==sentinel


def test_non_owner_cannot_inject_owner_secretary_or_agent_session_context(tmp_path):
    service=Mock();service._store.default_user_id='owner'
    service._store.capability_enabled.return_value=True
    calls=MatrixCalls(service,tmp_path)
    with pytest.raises(PermissionError,match='server-controlled'):
        calls.start(user_id='other',account_ref='matrix:other',room_id='!r:hs',target='@b:hs',
            purpose='Test',request_id='non-owner-injected-context',confirm=True,
            agent_context={'call_mode':'incoming','session_id':'owner-private-session'})
    assert service._store.accounts.call_count == 0


def test_owner_matrix_call_uses_existing_secretary_context(tmp_path, monkeypatch):
    service=Mock();service._store.default_user_id='owner'
    service._store.capability_enabled.return_value=True
    service._store.accounts.return_value=[{'id':'matrix:owner','provider':'matrix','enabled':True,
        'display_name':'@bezrabotnyi:chat.bezrabotnyi.com','credential_ref':'connector:owner'}]
    monkeypatch.setattr(matrix_calls.ConnectorSecrets,'load',lambda *a,**k:
        {'homeserver':'https://hs','access_token':'private','matrix_user_id':'@bezrabotnyi:chat.bezrabotnyi.com'})
    monkeypatch.setattr(matrix_calls,'request_json',lambda *a,**k:{'user_id':'@bezrabotnyi:chat.bezrabotnyi.com'})
    monkeypatch.setattr(matrix_calls.threading.Thread,'start',lambda self:None)
    record=MatrixCalls(service,tmp_path).start(user_id='owner',account_ref='matrix:owner',room_id='!r:hs',
        target='@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com',purpose='Секретарский QA',
        request_id='owner-secretary-unique-call',confirm=True)
    assert record['agent_context']=={'call_mode':'incoming'}
    durable=json.loads((tmp_path/record['call_id']/'call.json').read_text())
    assert durable['agent_context']=={'call_mode':'incoming'}
    from types import SimpleNamespace
    from universal_userio.voice.agentcall_realtime_bridge import FastAgentIncidentBrain
    brain=object.__new__(FastAgentIncidentBrain)
    brain.config=SimpleNamespace(fast_agent_enabled=True,fast_agent_bin='/bin/true',
        fast_agent_python='/bin/true',fast_agent_mcp_script='/bin/true',llm_api_key='test-key')
    brain.agent_context=record['agent_context']
    assert brain.available is True


def test_matrix_start_schema_does_not_expose_agent_context():
    from universal_userio.mcp_catalog import MATRIX_CALL_SPECS
    spec=next(item for item in MATRIX_CALL_SPECS if item.name=='userio.calls.matrix.start')
    assert 'agent_context' not in spec.input_schema['properties']


def test_explicit_stop_after_answer_is_recorded_and_not_misclassified_as_unknown(tmp_path):
    call_id=str(uuid.uuid4());folder=tmp_path/call_id;folder.mkdir()
    record={'call_id':call_id,'user_id':'owner','status':'active','answered':True}
    (folder/'call.json').write_text(json.dumps(record))
    calls=MatrixCalls(Mock(),tmp_path)
    stopped=matrix_calls.threading.Event();calls.active[call_id]=stopped
    result=calls.stop(call_id,'owner')
    assert stopped.is_set()
    assert result['stop_requested'] is True
    assert json.loads((folder/'call.json').read_text())['stop_requested'] is True
    matrix_calls.settle_disconnected_call(result)
    assert result['status']=='completed'


def test_voice_config_for_record_applies_only_model_alias():
    from dataclasses import dataclass
    from universal_userio.matrix_call_worker import voice_config_for_record
    @dataclass(frozen=True)
    class Config:
        fast_agent_model: str='server-default'
    config=Config()
    assert voice_config_for_record(config, {'voice_model':'omniroute/minimax-combo-m3.1'}).fast_agent_model == 'omniroute/minimax-combo-m3.1'
    assert voice_config_for_record(config, {}).fast_agent_model == 'server-default'


def test_voice_config_for_request_applies_endpoint_model_and_key_without_persisting_secret(monkeypatch):
    from dataclasses import dataclass
    from universal_userio.matrix_call_worker import voice_config_for_request
    @dataclass(frozen=True)
    class Config:
        fast_agent_model: str='server-default'; fast_agent_base_url: str='server-url'
        llm_url: str='llm-url'; llm_api_key: str='server-key'; llm_model: str='server-model'
        llm_provider: str='ollama'; llm_max_completion_tokens: int=256; llm_reasoning_effort: str='low'
        request_timeout: int=10
        system_prompt: str='Тестовый звонок'
        tts_provider: str='qwen-warm'; tts_url: str='http://tts.server'
        fast_agent_fallback_enabled: bool=True
    config=Config()
    settings={'endpoint':'https://omni.example/v1','model':'omniroute/minimax-combo-m3.1','token':'private-test-token'}
    bound=voice_config_for_request(config, settings)
    assert (bound.fast_agent_base_url,bound.llm_url,bound.llm_model,bound.fast_agent_model)==(
        'https://omni.example/v1','https://omni.example/v1/chat/completions','omniroute/minimax-combo-m3.1','omniroute/minimax-combo-m3.1')
    assert bound.llm_api_key == 'private-test-token'
    assert bound.llm_provider == 'openai-compatible'
    assert (bound.tts_provider, bound.tts_url) == ('qwen-warm', 'http://tts.server')
    assert bound.fast_agent_fallback_enabled is False
    from types import SimpleNamespace
    from universal_userio.voice import agentcall_realtime_bridge as voice
    pipeline=voice.LocalSpeechPipeline(bound)
    requests=[]
    def respond(url, **kwargs):
        requests.append((url,kwargs))
        return ('{"choices":[{"message":{"content":"Ответ модели"}}]}'.encode(), 'application/json')
    monkeypatch.setattr(voice, '_request', respond)
    assert pipeline.reply('Вопрос тестового звонка') == 'Ответ модели'
    request_url, request_kwargs=requests[0]
    assert request_url == 'https://omni.example/v1/chat/completions'
    assert request_kwargs['headers']['Authorization'] == 'Bearer private-test-token'
    assert json.loads(request_kwargs['data'])['model'] == 'omniroute/minimax-combo-m3.1'
    from universal_userio.voice.agentcall_realtime_bridge import VoiceProviderRouter
    router=VoiceProviderRouter(SimpleNamespace(
        fast_agent_model=bound.fast_agent_model, fast_agent_base_url=bound.fast_agent_base_url,
        llm_api_key=bound.llm_api_key, fast_agent_fallback_enabled=bound.fast_agent_fallback_enabled))
    router._probe_primary=lambda: (_ for _ in ()).throw(AssertionError('per-user route must not probe MiniMax origin'))
    route=router._provider_route()
    assert route == ('omniroute/minimax-combo-m3.1','https://omni.example/v1','private-test-token')


@pytest.mark.parametrize('endpoint', ['http://api.example/v1', 'https://user:pass@api.example/v1',
                                      'https://api.example/v1?token=bad'])
def test_voice_config_for_request_rejects_unsafe_endpoints(endpoint):
    from dataclasses import dataclass
    from universal_userio.matrix_call_worker import voice_config_for_request
    @dataclass(frozen=True)
    class Config:
        fast_agent_model: str='server-default'; fast_agent_base_url: str='server-url'
        llm_url: str='llm-url'; llm_api_key: str='server-key'; llm_model: str='server-model'
        llm_provider: str='ollama'; fast_agent_fallback_enabled: bool=True
    with pytest.raises(ValueError,match='invalid per-user'):
        voice_config_for_request(Config(), {'endpoint':endpoint,'model':'model','token':'secret'})


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
