"""Exercise the portable branch of the existing release controller without providers."""
import importlib.util
from pathlib import Path
import pytest


def load():
    path=Path(__file__).resolve().parents[1]/'scripts/voice_container.py'
    spec=importlib.util.spec_from_file_location('portable_voice',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


IMAGE='ghcr.io/megamen32/universal-userio-voice@sha256:'+'a'*64


def test_portable_standby_has_no_host_code_or_provider_worker():
    module=load();spec=module.create_spec(IMAGE,'standby','/etc/userio-voice','/var/lib/userio-voice',19095)
    assert spec['HostConfig']['Binds']==['/etc/userio-voice:/etc/userio-voice:ro','/var/lib/userio-voice:/var/lib/agentcall']
    assert spec['Cmd']==['serve']
    assert spec['HostConfig']['NetworkMode']=='host'
    assert not any('9092' in value for value in spec['Env'])
    for image,role,port in [('voice:latest','standby',19095),(IMAGE,'standby',9092),(IMAGE,'calls-primary',19095)]:
        with pytest.raises(ValueError):module.create_spec(image,role,'/etc/userio-voice','/var/lib/userio-voice',port)


def test_failed_readiness_restores_previous_without_data_restore(monkeypatch):
    module=load();calls=[]
    old={'Config':{'Labels':{module.LABEL:'1'}},'State':{'Running':True}}
    def api(method,path,body=None,**kwargs):
        calls.append((method,path,body))
        if path.startswith('/images/'):return {'Config':{'Labels':{module.LABEL:'1'}}}
        if method=='GET' and path.endswith('/json'):
            if '-previous' in path:return None
            if len([row for row in calls if row[1]==path])==1:return old
            return {'State':{'Running':False}}
        return {}
    monkeypatch.setattr(module,'api',api)
    with pytest.raises(RuntimeError,match='readiness'):
        module.install(IMAGE,'standby','userio-voice-check','/etc/config','/var/lib/data',19095)
    assert ('POST','/containers/userio-voice-check-previous/rename?name=userio-voice-check',None) in calls
    assert calls[-1]==('POST','/containers/userio-voice-check/start',None)
    assert not any('volume' in path for _,path,_ in calls)


def test_native_primary_is_not_replaced_without_handoff(monkeypatch):
    module=load();calls=[]
    def api(method,path,*args,**kwargs):
        calls.append((method,path))
        if path.startswith('/images/'):return {'Config':{'Labels':{module.LABEL:'1'}}}
        return {'Config':{'Labels':{}},'State':{'Running':True}}
    monkeypatch.setattr(module,'api',api)
    with pytest.raises(RuntimeError,match='native idle handoff'):
        module.install(IMAGE,'calls-primary','userio-voice','/etc/config','/var/lib/data',9092)
    assert all(method=='GET' for method,_ in calls)


def test_image_wires_fixed_matrix_and_fastagent_interpreters():
    root=Path(__file__).resolve().parents[1]
    portable=(root/'Dockerfile').read_text().split('AS portable-voice',1)[1]
    # The canonical gateway spawns these exact paths; a clean host cannot provide them.
    assert 'ln -s /usr/local /opt/universal-userio/.venv' in portable
    assert '/opt/universal-userio/.venv/bin/python -c' in portable
    module=load()
    assert module.runtime_environment({})['AGENTCALL_FAST_AGENT_PYTHON']=='/usr/local/bin/python'
    assert module.runtime_environment({})['AGENTCALL_FAST_AGENT_BIN']=='/usr/local/bin/fast-agent'


def test_same_healthy_digest_is_a_noop_for_primary(monkeypatch):
    module=load();calls=[]
    image_id='sha256:'+'c'*64
    def api(method,path,*args,**kwargs):
        calls.append((method,path))
        if path.startswith('/images/'):
            return {'Id':image_id,'Config':{'Labels':{module.LABEL:'1'}}}
        return {'Id':'existing','Image':image_id,
                'Config':{'Labels':{module.LABEL:'1','com.userio.role':'calls-primary'},
                          'Env':['USERIO_VOICE_ROLE=calls-primary','USERIO_VOICE_PORT=9092']},
                'HostConfig':{'Binds':['/etc/config:/etc/userio-voice:ro','/var/lib/data:/var/lib/agentcall']},
                'State':{'Running':True,'Health':{'Status':'healthy'}}}
    monkeypatch.setattr(module,'api',api)
    assert module.install(IMAGE,'calls-primary','userio-voice','/etc/config','/var/lib/data',9092)['status']=='unchanged'
    assert all(method=='GET' for method,_ in calls)
