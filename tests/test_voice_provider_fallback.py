"""Provider-request fallback, never a retry of an Agent/tool turn."""
import asyncio
from types import SimpleNamespace

import pytest

from universal_userio.voice import agentcall_fast_agent_fallback as fallback


class Refused(RuntimeError):
    def __init__(self,status):self.status_code=status


class Params:
    def __init__(self,model='old-model'):self.model=model
    def model_copy(self,update):return Params(update.get('model',self.model))


class NativeLLM:
    def __init__(self,model,script):
        self.model=model;self.script=list(script);self.calls=[];self.chunks=[];self.tools=[]
    def add_stream_listener(self,listener):
        self.chunks.append(listener);return lambda:self.chunks.remove(listener)
    def add_tool_stream_listener(self,listener):
        self.tools.append(listener);return lambda:self.tools.remove(listener)
    async def generate(self,messages,request_params,tools):
        self.calls.append((messages,request_params,tools))
        outcome=self.script.pop(0)
        if isinstance(outcome,tuple):
            kind,value,error=outcome
            for cb in self.chunks if kind=='text' else self.tools:
                cb(value) if kind=='text' else cb('tool_call_start',{'name':value})
            raise error
        if isinstance(outcome,Exception):raise outcome
        return outcome


def chain(scripts,**kwargs):
    models=['glm-5.3-flash','MiniMax-M3.1-Flash-Preview','gpt-6-luna']
    providers=['zai-direct','minimax-direct','codexresponses']
    llms=[NativeLLM(m,s) for m,s in zip(models,scripts)]
    legs=[fallback.ProviderLeg(slot,m,p,lambda llm=llm:llm)
          for slot,m,p,llm in zip(['voice','voice_fallback_minimax','voice_fallback_luna'],models,providers,llms)]
    return fallback.IncidentProviderFallback(legs,**kwargs),llms


@pytest.mark.parametrize('limit_count',[1,2])
def test_precontent429_selects_real_next_native_model(limit_count):
    scripts=[[Refused(429)] if n<limit_count else ['answer'] for n in range(3)]
    model,llms=chain(scripts)
    messages=[{'role':'user','content':'remember cedar'}];tools=[object()]
    answer=asyncio.run(model.generate(messages,Params(),tools))
    assert answer=='answer'
    assert all(l.calls[0][0] is messages and l.calls[0][2] is tools for l in llms[:limit_count+1])
    assert llms[limit_count].calls[0][1].model==llms[limit_count].model
    assert model.model_route[-1]['model']==llms[limit_count].model
    assert model.model_route[-1]['outcome']=='completed'
    assert all(not row['injected'] for row in model.model_route)


def test_accepted_tool_result_survives_provider_switch_without_reexecution():
    # The first native request chooses a tool. The already accepted result is
    # supplied on the next request, where rate limiting occurs.
    model,llms=chain([['tool-call',Refused(429)],[Refused(429)],['final answer']])
    actions=[];tools=[lambda:actions.append('accepted') or 'hostname=voice-qa-sandbox']
    messages=[{'role':'user','content':'hostname'}]
    assert asyncio.run(model.generate(messages,Params(),tools))=='tool-call'
    receipt=tools[0]()
    messages.extend([{'role':'assistant','tool_call':'one'},{'role':'tool','content':receipt}])
    assert asyncio.run(model.generate(messages,Params(),tools))=='final answer'
    assert actions==['accepted']
    assert llms[2].calls[0][0] is messages and llms[2].calls[0][0][-1]['content']==receipt
    assert [row['outcome'] for row in model.model_route]==['completed','rate_limited','rate_limited','completed']


@pytest.mark.parametrize('kind',['text','tool'])
def test_partial_content_commits_request_and_never_falls_back(kind):
    model,llms=chain([[ (kind,'partial',Refused(429)) ],['must not run'],[]])
    with pytest.raises(Refused):asyncio.run(model.generate([{'role':'user'}],Params(),[]))
    assert not llms[1].calls and not llms[2].calls
    assert model.model_route[-1]['outcome']=='failed_after_content'


def test_403_does_not_fallback():
    model,llms=chain([[Refused(403)],['must not run'],[]])
    with pytest.raises(Refused):asyncio.run(model.generate([{'role':'user'}],Params(),[]))
    assert not llms[1].calls


def test_injected_429_is_distinct_from_actual_provider_completion():
    model,llms=chain([[],[],['real native Luna answer']],fault_slots=lambda:{'voice','voice_fallback_minimax'})
    assert asyncio.run(model.generate([{'role':'user'}],Params(),[]))=='real native Luna answer'
    assert not llms[0].calls and not llms[1].calls
    assert [row['injected'] for row in model.model_route]==[True,True,False]
    assert model.model_route[-1]['provider']=='codexresponses'


def test_incident_chain_selects_glm_before_old_quota_probe(monkeypatch):
    from dataclasses import replace
    from universal_userio.voice import agentcall_realtime_bridge as bridge
    config=replace(bridge.BackendConfig.from_environment(),fast_agent_provider_chain_enabled=True)
    brain=bridge.FastAgentIncidentBrain(config,{},'')
    monkeypatch.setenv('ZAI_API_KEY','private-test-key')
    monkeypatch.setattr(brain,'_probe_primary',lambda:pytest.fail('old MiniMax quota probe'))
    assert brain._provider_route()==('generic.glm-5.3-flash','https://api.z.ai/api/coding/paas/v4','private-test-key')


def test_provider_control_is_exact_finite_and_independent_of_incident_authority(monkeypatch,tmp_path):
    import json,time,sys
    from universal_userio.voice import noticeplace_voice_context as context_api
    monkeypatch.setitem(sys.modules,'noticeplace_voice_context',context_api)
    context={'secretary_voice_call_id':'call-1','voice_target':'@a:test','voice_transport':'matrix',
             'voice_account_ref':'matrix:b','voice_room':'!room:test','voice_sdk_user_id':'person-1'}
    entry={'call_id':'call-1','caller':'@a:test','account_ref':'matrix:b','room':'!room:test',
           'sdk_user_id':'person-1','expires_at':time.time()+120,'synthetic':True,
           'participants':['@a:test','@b:test'],'rate_limit_slots':['voice','voice_fallback_minimax']}
    path=tmp_path/'scope.json';path.chmod(0o600) if path.exists() else path.touch(mode=0o600)
    monkeypatch.setenv('AGENTCALL_QA_SCOPE',json.dumps({'scope_file':str(path)}))
    def write(value):path.write_text(json.dumps({'entries':[],'provider_control':value}))
    write([entry]);assert fallback.provider_fault_slots(context)=={'voice','voice_fallback_minimax'}
    for field in ('secretary_voice_call_id','voice_target','voice_account_ref','voice_room','voice_sdk_user_id'):
        assert not fallback.provider_fault_slots(dict(context,**{field:'foreign'}))
    for invalid in [dict(entry,expires_at=time.time()-1),dict(entry,expires_at=time.time()+601),
                    dict(entry,rate_limit_slots=['owner']),dict(entry,synthetic=False),None]:
        write([invalid]);assert not fallback.provider_fault_slots(context)
    write([entry]);path.chmod(0o644);assert not fallback.provider_fault_slots(context)


def test_native_error_channel_429_and_rollback_are_not_content():
    error_reply=SimpleNamespace(stop_reason='error',channels={'fast-agent-error':[SimpleNamespace(text='Generic request failed (status=429): rate limit')]})
    model,llms=chain([[error_reply],[Refused(429)],['Luna native answer']])
    assert asyncio.run(model.generate([{'role':'user'}],Params(),[]))=='Luna native answer'
    assert model.model_route[-1]['model']=='gpt-6-luna'


def test_empty_native_commit_or_rollback_is_not_partial_content():
    model,llms=chain([[('text',SimpleNamespace(text='',event='rollback'),Refused(429))],['answer'],[]])
    assert asyncio.run(model.generate([{'role':'user'}],Params(),[]))=='answer'
