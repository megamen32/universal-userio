"""Portable mode of the existing UserIO container release controller.

No dialer, poller or scheduler. Configuration and delivery journals stay external.
"""
from __future__ import annotations
import hashlib
import grp
import http.client
import io
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tarfile
import time
from urllib.parse import quote

ADAPTER_REVISION = '5de41fd714910fc04f742c4a91dd5ce69401635c'
ADAPTER_PATH = 'integrations/agentcall/agentcall_secretary_voice.py'
ADAPTER_SHA = '486a64879998962f43c090a9a199823e4f9779ba5db20c27b1461d062f820522'
IMAGE = re.compile(r'ghcr\.io/megamen32/universal-userio-voice@sha256:[a-f0-9]{64}\Z')
LABEL = 'com.userio.portable-voice'
STOP_TIMEOUT = 20
SECRETARY_STATE = '/etc/agentcall-secretary'
DOCKER_TIMEOUT = 40


def build_context(root, revision, archive, destination):
    # A worktree has no independent copy of the external project.
    foreign = Path('/home/roomhacker/agents-projects/exmanager')
    payload = subprocess.check_output(['git', '-C', str(foreign), 'show', ADAPTER_REVISION+':'+ADAPTER_PATH])
    if hashlib.sha256(payload).hexdigest() != ADAPTER_SHA:
        raise RuntimeError('published SDK adapter digest differs')
    modules = {}
    dependency_inputs = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as source, tarfile.open(destination, 'w') as target:
        for member in source:
            if member.name.startswith(('src/', 'scripts/', 'deploy/containers/')) or member.name in ('Dockerfile', 'pyproject.toml'):
                data = source.extractfile(member).read() if member.isfile() else None
                target.addfile(member, io.BytesIO(data) if data is not None else None)
                if member.isfile() and member.name in ('deploy/containers/requirements.txt','deploy/containers/requirements-portable-voice.txt'):
                    dependency_inputs[member.name]=hashlib.sha256(data).hexdigest()
                if member.isfile() and member.name.startswith('src/universal_userio/voice/') and member.name.endswith('.py'):
                    modules[member.name] = hashlib.sha256(data).hexdigest()
        release = {'revision':revision, 'adapter_revision':ADAPTER_REVISION, 'adapter_sha256':ADAPTER_SHA,
                   'modules': modules, 'dependency_inputs':dependency_inputs, 'external_services':['Secretary SDK', 'STT', 'TTS', 'UserIO API/MCP']}
        for name, data in {'deploy/containers/external/agentcall_secretary_voice.py':payload,
                           'deploy/containers/portable-release.json':json.dumps(release, sort_keys=True).encode()}.items():
            info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o644
            target.addfile(info,io.BytesIO(data))
    print(json.dumps({'context':str(destination),'revision':revision,'adapter_revision':ADAPTER_REVISION,
                      'sha256':hashlib.sha256(destination.read_bytes()).hexdigest()}))


class Docker(http.client.HTTPConnection):
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.sock.settimeout(DOCKER_TIMEOUT)
        self.sock.connect('/var/run/docker.sock')


def api(method,path,body=None, *, missing=False):
    conn=Docker('localhost')
    try:
        conn.request(method,'/v1.45'+path,json.dumps(body) if body is not None else None,
                     {'Content-Type':'application/json'} if body is not None else {})
        response=conn.getresponse();raw=response.read()
        if missing and response.status==404:return None
        if response.status>=300:raise RuntimeError(f'Docker {method} failed HTTP {response.status}')
        return json.loads(raw) if raw else None
    finally:conn.close()


def create_spec(image, role, config, data, port):
    if not IMAGE.fullmatch(image):raise ValueError('immutable published UserIO voice digest required')
    if role not in {'standby','calls-primary'}:raise ValueError('explicit role required')
    if role=='standby' and not 19095<=port<=19104:raise ValueError('standby port must be 19095..19104')
    if role=='calls-primary' and port!=9092:raise ValueError('primary must preserve the existing AudioSocket port9092')
    for path in (config,data):
        if not Path(path).is_absolute() or ':' in path or '..' in Path(path).parts:
            raise ValueError('absolute config/data paths required')
    spec = {'Image':image,'Entrypoint':['python','/opt/universal-userio/scripts/voice_container.py'],
            'Cmd':['serve'],'Env':[f'USERIO_VOICE_ROLE={role}',f'USERIO_VOICE_PORT={port}'],
            'Labels':{LABEL:'1','com.userio.role':role},
            'Healthcheck':{'Test':['CMD','python','/opt/universal-userio/scripts/voice_container.py','health'],
                           'Interval':2000000000,'Timeout':3000000000,'Retries':3,'StartPeriod':1000000000},
            'HostConfig':{'Init':True,'NetworkMode':'host','RestartPolicy':{'Name':'unless-stopped'},
                          'Binds':[f'{config}:/etc/userio-voice:ro',f'{data}:/var/lib/agentcall'],
                          'Tmpfs':{'/run/agentcall':'mode=0755','/tmp':'mode=1777'},
                          'LogConfig':{'Type':'json-file','Config':{'max-size':'10m','max-file':'3'}}}}
    if role == 'calls-primary':
        # The existing backend reaches the typed gateway over this runtime IPC.
        spec['HostConfig']['Binds'].append('/run/agentcall:/run/agentcall')
        del spec['HostConfig']['Tmpfs']['/run/agentcall']
        # The supervised Secretary transport owns the native app credential and
        # republishes it on every app-token rotation. A static copy of that
        # credential dies with the next rotation, so the primary reads the
        # supervised file itself instead of a private copy of it. The bind is
        # added only where that protected state actually exists.
        if Path(SECRETARY_STATE).is_dir():
            spec['HostConfig']['Binds'].append(SECRETARY_STATE + ':' + SECRETARY_STATE + ':ro')
    return spec


def install(image,role,name,config,data,port, *, timeout=75):
    if not re.fullmatch(r'userio-voice(?:-[a-z0-9-]+)?',name):raise ValueError('UserIO voice component name required')
    spec=create_spec(image,role,config,data,port)
    # Pull is an explicit normal Docker operation before this call; never embed registry credentials.
    info=api('GET','/images/'+quote(image,safe='')+'/json')
    if info['Config'].get('Labels',{}).get(LABEL)!='1':raise ValueError('image is not a portable UserIO voice release')
    old=api('GET','/containers/'+name+'/json',missing=True)
    if old and old['Config'].get('Labels',{}).get(LABEL)!='1':
        raise RuntimeError('existing component belongs to another release path; native idle handoff required')
    if (old and old.get('Image') == info.get('Id') and info.get('Id')
            and old['State'].get('Health', {}).get('Status') == 'healthy'
            and all(value in old['Config'].get('Env', []) for value in spec['Env'])
            and sorted(old.get('HostConfig', {}).get('Binds', [])) == sorted(spec['HostConfig']['Binds'])):
        return {'status':'unchanged','name':name,'role':role,'image':image,
                'container_id':old['Id'],'calls_tested':False}
    if old and old['State'].get('Running') and role=='calls-primary':
        raise RuntimeError('running primary needs native idle handoff before replacement; reconcile receipts then stop the exact component')
    previous=name+'-previous'
    prior=api('GET','/containers/'+previous+'/json',missing=True) if old else None
    archive=None
    if prior:
        if prior['Config'].get('Labels',{}).get(LABEL)!='1' or prior['State'].get('Running'):
            raise RuntimeError('rollback component is foreign or running; preserve it')
        archive=previous+'-'+prior['Id'][:12]
        if api('GET','/containers/'+archive+'/json',missing=True):
            raise RuntimeError('owned rollback cleanup is pending; preserve its exact receipt')
    created=renamed=archived=False
    try:
        if archive:
            api('POST','/containers/'+previous+'/rename?name='+archive);archived=True
        if old:
            if old['State'].get('Running'):
                api('POST','/containers/'+name+'/stop?t='+str(STOP_TIMEOUT))
            api('POST','/containers/'+name+'/rename?name='+previous);renamed=True
        api('POST','/containers/create?name='+name,spec);created=True
        api('POST','/containers/'+name+'/start')
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            current=api('GET','/containers/'+name+'/json')
            if current['State'].get('Health',{}).get('Status')=='healthy':
                receipt={'status':'installed','name':name,'role':role,'image':image,
                         'container_id':current['Id'],'previous':previous if old else None,
                         'revision':info['Config']['Labels'].get('org.opencontainers.image.revision'),
                         'calls_tested':False}
                if archived:
                    try:api('DELETE','/containers/'+archive)
                    except Exception:receipt['cleanup_pending']=archive
                return receipt
            if not current['State'].get('Running'):break
            time.sleep(.5)
        raise RuntimeError('portable voice readiness deadline exceeded')
    except BaseException:
        if created:api('DELETE','/containers/'+name+'?force=true')
        if renamed:
            api('POST','/containers/'+previous+'/rename?name='+name)
            if old['State'].get('Running'):api('POST','/containers/'+name+'/start')
        if archived:api('POST','/containers/'+archive+'/rename?name='+previous)
        raise


def verify_payload():
    # Unix sockets, not audio/TTS/model requests; no accepted call can be created.
    import aiohttp, websockets, pydantic
    from livekit import rtc
    import agentcall_secretary_voice
    release=json.loads(Path('/opt/universal-userio/.userio-portable-release.json').read_text())
    for name,digest in release['modules'].items():
        if hashlib.sha256((Path('/opt/universal-userio')/name).read_bytes()).hexdigest()!=digest:return 1
    if hashlib.sha256(Path('/usr/local/lib/agentcall/agentcall_secretary_voice.py').read_bytes()).hexdigest()!=release['adapter_sha256']:return 1
    return 0


def incoming_socket_ready(path,settings):
    value=settings.get('AGENTCALL_INCOMING_SOCKET_GID')
    if value is None:gid=grp.getgrnam('asterisk').gr_gid
    elif isinstance(value,str) and re.fullmatch(r'[1-9][0-9]{0,9}',value) and int(value)<=2147483647:gid=int(value)
    else:return False
    metadata=path.stat()
    return metadata.st_gid==gid and metadata.st_mode&0o777==0o660


def health():
    if verify_payload():return 1
    for name in ('control.sock','gatewayd.sock','incoming.sock'):
        if not (Path('/run/agentcall')/name).is_socket():return 1
    settings=json.loads(Path('/etc/userio-voice/environment.json').read_text())
    if not incoming_socket_ready(Path('/run/agentcall/incoming.sock'),settings):return 1
    return 0


def runtime_environment(settings):
    result=dict(settings)
    result.update(AGENTCALL_FAST_AGENT_PYTHON="/usr/local/bin/python",
                  AGENTCALL_FAST_AGENT_BIN="/usr/local/bin/fast-agent")
    return result


def serve():
    path=Path('/etc/userio-voice/environment.json')
    metadata=path.stat()
    if metadata.st_mode&0o077 or metadata.st_uid!=0:raise PermissionError('protected root-owned configuration required')
    settings=json.loads(path.read_text())
    if not isinstance(settings,dict) or any(not isinstance(v,str) for v in settings.values()):
        raise ValueError('environment.json must map strings to strings')
    role=os.environ['USERIO_VOICE_ROLE'];port=os.environ['USERIO_VOICE_PORT']
    os.environ.update(runtime_environment(settings))
    os.environ.update(AGENTCALL_FAST_AGENT_HOME='/var/lib/agentcall/fast-agent-home',
                      AGENTCALL_TTS_PHONE_FALLBACK='0')
    # A standby starts the same gateway, isolated from provider/account ingress.
    # No PBX control socket is mounted, so startup cleanup cannot affect native calls.
    if role=='standby':os.environ['AGENTCALL_LLM_PROVIDER']='openai-compatible'
    elif settings.get('AGENTCALL_LLM_PROVIDER')!='openai-compatible' or any(not settings.get(key) for key in ('AGENTCALL_STT_URL','AGENTCALL_TTS_URL')):
        raise ValueError('primary requires explicit external STT/TTS and openai-compatible SDK configuration')
    os.execv(sys.executable,[sys.executable,'/usr/local/lib/agentcall/agentcall_realtime_bridge.py',
             '--host','127.0.0.1','--port',port,'--event-socket','/run/agentcall/gatewayd.sock',
             '--control-socket','/run/agentcall/control.sock'])


if __name__=='__main__':
    if sys.argv[1:] == ['payload']:sys.exit(verify_payload())
    if sys.argv[1:] == ['health']:sys.exit(health())
    if sys.argv[1:] == ['serve']:serve()
    else:raise SystemExit('use existing container_release.py install-voice')
