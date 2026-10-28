"""Fast synthetic scope proofs: no network, model, call or production database."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import types
from dataclasses import replace
import subprocess
from unittest.mock import patch

import pytest
from universal_userio.voice import noticeplace_voice_context as context_api
from universal_userio.voice import agentcall_fast_agent_worker as worker


BINDING = {'call_mode':'qa_scoped', 'voice_transport':'matrix',
           'qa_caller':'@caller:test', 'qa_room':'!room:test',
           'qa_account_ref':'matrix:test-b',
           'qa_sdk_user_id':'8b7ea2ed-82e2-42b1-8e8a-f61c0e6103b6',
           'incident_id':'synthetic-1', 'correlation_id':'corr:qa:synthetic-1'}


def scope_document():
    return {'entries':[{'caller':BINDING['qa_caller'], 'room':BINDING['qa_room'],
        'transport':'matrix', 'account_ref':BINDING['qa_account_ref'],
        'sdk_user_id':BINDING['qa_sdk_user_id'], 'incident_id':'synthetic-1',
        'participants':['@caller:test','@peer:test'], 'synthetic':True,
        'project':'voice-qa', 'expires_at':time.time()+600,
        'correlation_id':BINDING['correlation_id'],
        'token_env':'NOTICEPLACE_VOICE_QA_TOKEN'}]}


def test_binding_revalidates_protected_scope_and_ttl(monkeypatch, tmp_path):
    path = tmp_path/'scope.json'
    document = scope_document()
    path.write_text(json.dumps(document)); path.chmod(0o600)
    monkeypatch.setenv('AGENTCALL_QA_SCOPE',json.dumps({'scope_file':str(path)}))
    assert context_api.qa_incident_scope(BINDING)['incident_id'] == 'synthetic-1'
    for field in ('qa_caller','qa_room','qa_account_ref','qa_sdk_user_id','incident_id'):
        with pytest.raises(PermissionError):
            context_api.qa_incident_scope(dict(BINDING, **{field:'foreign'}))
    document['entries'][0]['expires_at'] = time.time()-1
    path.write_text(json.dumps(document))
    with pytest.raises(PermissionError):context_api.qa_incident_scope(BINDING)
    path.write_text(json.dumps({'entries':[]}))
    with pytest.raises(PermissionError):context_api.qa_incident_scope(BINDING)


def test_scoped_reader_fetches_only_bound_incident(monkeypatch):
    monkeypatch.setenv('AGENTCALL_QA_SCOPE',json.dumps(scope_document()))
    monkeypatch.setenv('NOTICEPLACE_VOICE_QA_TOKEN','synthetic-token')
    store=context_api.ScopedNoticePlaceVoiceStore(BINDING)
    calls=[]
    def request(path, params=None):
        calls.append(path)
        return {'id':'synthetic-1','project':'voice-qa','title':'HTTP 502',
                'correlation_id':BINDING['correlation_id'],
                'state':'open','has_session':True,'session_id':'owner-session'}
    store._request=request
    result=store.list_incidents()
    assert result['incidents'][0]['id']=='synthetic-1'
    assert calls==['/v1/incidents/synthetic-1']
    assert store.incident('synthetic-1')['has_session'] is False
    with pytest.raises(PermissionError):store.incident('owner-incident')
    assert len(calls)==2


def test_qa_worker_exposes_only_scoped_agentcall_server():
    assert worker.voice_servers({'call_mode':'qa_scoped'}, {'NoticePlace':{},'AgentHerder':{}})==['agentcall']
    assert worker.voice_servers({'call_mode':'incoming'}, {'NoticePlace':{}})==['AgentHerder','agentcall','NoticePlace']


def test_ack_unknown_is_durable_and_never_replayed(monkeypatch, tmp_path):
    monkeypatch.setenv('AGENTCALL_QA_SCOPE',json.dumps(scope_document()))
    monkeypatch.setenv('NOTICEPLACE_VOICE_QA_TOKEN','synthetic-token')
    store=context_api.ScopedNoticePlaceVoiceStore(BINDING, receipt_directory=tmp_path)
    store._request=lambda *args: {'id':'synthetic-1','project':'voice-qa','state':'open',
                                 'correlation_id':BINDING['correlation_id']}
    attempts=[]
    def unknown(*args):
        attempts.append(args)
        raise TimeoutError('response unknown')
    store._post_ack=unknown
    assert store.acknowledge('synthetic-1')['state']=='unknown'
    restarted=context_api.ScopedNoticePlaceVoiceStore(BINDING,receipt_directory=tmp_path)
    restarted._request=store._request
    restarted._post_ack=unknown
    assert restarted.acknowledge('synthetic-1')['state']=='unknown'
    assert len(attempts)==1
    with pytest.raises(PermissionError):restarted.acknowledge('foreign')
    assert len(attempts)==1


def test_ack_checks_project_and_correlation_before_post(monkeypatch, tmp_path):
    monkeypatch.setenv('AGENTCALL_QA_SCOPE',json.dumps(scope_document()))
    store=context_api.ScopedNoticePlaceVoiceStore(BINDING,receipt_directory=tmp_path)
    attempts=[]
    store._post_ack=lambda *args: attempts.append(args)
    store._request=lambda *args: {'id':'synthetic-1','project':'voice-qa','state':'open',
                                 'correlation_id':'foreign'}
    with pytest.raises(PermissionError):store.acknowledge('synthetic-1')
    assert not attempts


def load_session_mcp(monkeypatch):
    class Server:
        def __init__(self,*args,**kwargs):self.tools={}
        def tool(self,**kwargs):
            def register(function):
                self.tools[function.__name__]=function
                return function
            return register
    module=types.ModuleType('mcp.server.mcpserver');module.MCPServer=Server
    monkeypatch.setitem(sys.modules,'mcp.server.mcpserver',module)
    path=Path(context_api.__file__).with_name('agentcall_session_mcp.py')
    spec=importlib.util.spec_from_file_location('qa_session_mcp',path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


def test_scoped_tools_read_ack_return_and_revalidate_revocation(monkeypatch, tmp_path):
    document=scope_document()
    monkeypatch.setenv('AGENTCALL_QA_SCOPE',json.dumps(document))
    monkeypatch.setenv('NOTICEPLACE_VOICE_QA_TOKEN','synthetic-token')
    session=load_session_mcp(monkeypatch)
    state={'value':'open'}
    requests=[]
    store=context_api.ScopedNoticePlaceVoiceStore(BINDING,receipt_directory=tmp_path/'actions')
    def request(path,params=None):
        requests.append(path)
        return {'id':'synthetic-1','project':'voice-qa','state':state['value'],
                'title':'HTTP 502','correlation_id':BINDING['correlation_id']}
    store._request=request
    store._post_ack=lambda incident_id: state.update(value='acknowledged')
    context=dict(BINDING,turn_id='a'*32,secretary_voice_call_id='7a0440b3-2637-4966-8865-e97820d9bd96',
                 current_utterance='Подтверждаю этот инцидент.')
    server=session.build_scoped_server(context,store)
    assert set(server.tools)=={'incidents_list','incident_read','acknowledge_incident','switch_to_secretary'}
    assert server.tools['incidents_list']()['incidents'][0]['title']=='HTTP 502'
    assert server.tools['acknowledge_incident']('synthetic-1')['state']=='acknowledged'
    assert requests==['/v1/incidents/synthetic-1']*3
    root=tmp_path/'runtime';root.mkdir()
    real_path=Path
    monkeypatch.setattr(session,'Path',lambda path:root if path=='/run/agentcall/fast-agent' else real_path(path))
    assert server.tools['switch_to_secretary']()['mode']=='secretary'
    proposal=json.loads((root/('turn-'+'a'*32+'.voice-mode.json')).read_text())
    assert proposal['call_id']==context['secretary_voice_call_id']
    monkeypatch.setenv('AGENTCALL_QA_SCOPE',json.dumps({'entries':[]}))
    with pytest.raises(PermissionError):server.tools['incident_read']('synthetic-1')
    assert len(requests)==3


def test_owner_reader_keeps_existing_tool_profile(monkeypatch):
    session=load_session_mcp(monkeypatch)
    context={'call_mode':'incoming','turn_id':'b'*32}
    server=session.build_incoming_server(context,store=object(),userio_chats=object())
    assert {'incidents_list','incident_read','incident_session_read','agent_session_read',
            'secretary_chats_search','secretary_chats_read'} <= set(server.tools)


def test_scoped_ack_uses_existing_api_and_confirms_readback(monkeypatch,tmp_path):
    monkeypatch.setenv('AGENTCALL_QA_SCOPE',json.dumps(scope_document()))
    monkeypatch.setenv('NOTICEPLACE_VOICE_QA_TOKEN','synthetic-token')
    state={'value':'open'}; requests=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):
            return json.dumps({'id':'synthetic-1','project':'voice-qa',
                'correlation_id':BINDING['correlation_id'],'state':state['value']}).encode()
    def runner(request,timeout=None):
        requests.append(request)
        if request.get_method()=='POST':state['value']='acknowledged'
        return Response()
    store=context_api.ScopedNoticePlaceVoiceStore(BINDING,base_url='http://localhost',
        runner=runner,receipt_directory=tmp_path)
    result=store.acknowledge('synthetic-1')
    assert result['state']=='acknowledged'
    assert result['delivery_cancellation_verified'] is False
    post=requests[1]
    assert post.full_url=='http://localhost/v1/incidents/synthetic-1/ack'
    assert json.loads(post.data)=={'actor':'voice-qa:'+BINDING['qa_sdk_user_id']}
    assert post.get_header('Idempotency-key').startswith('voice-qa-ack-')
    assert [request.get_method() for request in requests]==['GET','POST','GET']
    assert store.acknowledge('synthetic-1')['state']=='acknowledged'
    assert sum(request.get_method()=='POST' for request in requests)==1


def test_real_qa_cli_turn_has_no_global_mcp_route(monkeypatch,tmp_path):
    import agentcall_realtime_bridge as bridge
    import noticeplace_voice_context as bare_context
    monkeypatch.setenv('AGENTCALL_QA_SCOPE',json.dumps(scope_document()))
    monkeypatch.setenv('NOTICEPLACE_VOICE_QA_TOKEN','synthetic-token')
    home=tmp_path/'home';home.mkdir()
    (home/'fastagent.config.yaml').write_text('mcp: {servers: {NoticePlace: {command: owner}, AgentHerder: {url: http://owner}}}')
    executable=tmp_path/'executable';executable.touch()
    config=replace(bridge.BackendConfig.from_environment(),fast_agent_enabled=True,
        fast_agent_bin=str(executable),fast_agent_python=str(executable),fast_agent_mcp_script=str(executable),
        fast_agent_home=str(home),llm_api_key='synthetic-provider',fast_agent_fallback_enabled=False)
    calls=[]
    def runner(command,**kwargs):
        import yaml
        calls.append(command)
        assert '--url' not in command and '--servers' not in command
        document=yaml.safe_load(Path(command[command.index('--config-path')+1]).read_text())
        assert document['mcp']['servers']=={}
        assert kwargs['env']['AGENTCALL_QA_SCOPE']
        return subprocess.CompletedProcess(command,0,stdout='Синтетическая карточка: HTTP 502.',stderr='')
    brain=bridge.FastAgentIncidentBrain(config,BINDING,'Тестовый разговор',runner=runner)
    monkeypatch.setattr(brain,'_provider_route',lambda:('test-model','http://local-model','synthetic-provider'))
    monkeypatch.setattr(bare_context.ScopedNoticePlaceVoiceStore,'_request',lambda *args:{
        'id':'synthetic-1','project':'voice-qa','correlation_id':BINDING['correlation_id'],
        'state':'open','title':'HTTP 502'})
    runtime=tmp_path/'runtime'
    monkeypatch.setattr(bridge,'Path',lambda path:runtime if path=='/run/agentcall/fast-agent' else Path(path))
    assert brain.reply('Что с тестовым инцидентом?')=='Синтетическая карточка: HTTP 502.'
    assert len(calls)==1
