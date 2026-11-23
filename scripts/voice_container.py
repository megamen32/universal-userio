"""Portable mode of the existing UserIO container release controller.

No dialer, poller or scheduler. Configuration and delivery journals stay external.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import grp
import http.client
import io
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import time
from urllib.parse import quote

ADAPTER_REVISION = 'af1fc1f31503f0677fe1e6c989b41691bf6a78bb'
ADAPTER_PATH = 'integrations/agentcall/agentcall_secretary_voice.py'
ADAPTER_SHA = '690911ff4bd5bac3e138bd3d1a8e28216e65d5218fbf6946d94c1685d3311a67'
IMAGE = re.compile(r'ghcr\.io/megamen32/universal-userio-voice@sha256:[a-f0-9]{64}\Z')
COMPONENT_NAME = re.compile(r'userio-voice(?:-[a-z0-9-]+)?\Z')
LABEL = 'com.userio.portable-voice'
STOP_TIMEOUT = 20
SECRETARY_STATE = '/etc/agentcall-secretary'
# The incident brain runs from the deployed fast agent home of this host.
FAST_AGENT_HOME = '/usr/local/lib/agentcall/fast-agent-home'
FAST_AGENT_STATE = '/var/lib/noticeplace-phone'
# The incident brain runs from the deployed fast agent tool of this host.
FAST_AGENT_TOOL = '/home/roomhacker/.local/share/uv/tools/fast-agent-mcp'
FAST_AGENT_PYTHON_HOME = '/home/roomhacker/.local/share/uv/python'
# The MCP servers of the incident brain live on this host as well.
NOTICEPLACE_HOME = '/opt/noticeplace'
NOTICEPLACE_MESH = '/run/noticeplace-mesh'
NOTICEPLACE_MCP_ENV = '/etc/noticeplace-voice-mcp.env'
DOCKER_TIMEOUT = 40
TRANSCRIPTION_PID = Path('/tmp/userio-voice-transcription.pid')
RUNTIME_DIR = Path('/run/agentcall')
ADMISSION_SOCKETS = ('control.sock', 'incoming.sock')


def asterisk_client_inputs():
    """Reuse the installed PBX CLI and its library closure in the image."""
    binary = Path('/usr/sbin/asterisk')
    external = 'deploy/containers/external/'
    files = {external+'asterisk': binary.read_bytes()}
    dependencies = subprocess.check_output(['ldd', str(binary)], text=True)
    for row in dependencies.splitlines():
        parts = row.split()
        if len(parts) > 2 and parts[1] == '=>' and parts[2].startswith('/'):
            library = Path(parts[2])
            if library.name.split('.so')[0] not in {'libc', 'libm', 'libpthread', 'librt', 'libdl'}:
                files[external+'asterisk-libs/'+library.name] = library.read_bytes()
    return files


def build_context(root, revision, archive, destination):
    # A worktree has no independent copy of the external project.
    foreign = Path('/home/roomhacker/agents-projects/exmanager')
    payload = subprocess.check_output(['git', '-C', str(foreign), 'show', ADAPTER_REVISION+':'+ADAPTER_PATH])
    if hashlib.sha256(payload).hexdigest() != ADAPTER_SHA:
        raise RuntimeError('published SDK adapter digest differs')
    modules = {}
    dependency_inputs = {}
    asterisk_client = asterisk_client_inputs()
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
                   'modules': modules, 'dependency_inputs': dependency_inputs,
                   'asterisk_client': {name: hashlib.sha256(data).hexdigest()
                                       for name, data in asterisk_client.items()},
                   'external_services':['Secretary SDK', 'STT', 'TTS', 'UserIO API/MCP']}
        for name, data in {'deploy/containers/external/agentcall_secretary_voice.py':payload,
                           'deploy/containers/portable-release.json':json.dumps(release, sort_keys=True).encode(),
                           **asterisk_client}.items():
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
        spec['HostConfig']['Binds'].append('/run/asterisk:/run/asterisk:ro')
        del spec['HostConfig']['Tmpfs']['/run/agentcall']
        # The supervised Secretary transport owns the native app credential and
        # republishes it on every app-token rotation. A static copy of that
        # credential dies with the next rotation, so the primary reads the
        # supervised file itself instead of a private copy of it. The bind is
        # added only where that protected state actually exists.
        if Path(SECRETARY_STATE).is_dir():
            spec['HostConfig']['Binds'].append(SECRETARY_STATE + ':' + SECRETARY_STATE + ':ro')
        # The incident brain of the voice host reads its config from the
        # deployed fast agent home and keeps its sessions in the host state.
        # Without these the incidents mode cannot run a turn on this host.
        if Path(FAST_AGENT_HOME).is_dir():
            spec['HostConfig']['Binds'].append(FAST_AGENT_HOME + ':' + FAST_AGENT_HOME + ':ro')
            if Path(FAST_AGENT_STATE).is_dir():
                spec['HostConfig']['Binds'].append(FAST_AGENT_STATE + ':' + FAST_AGENT_STATE + ':rw')
        # The worker itself is a host tool: without its environment and its
        # interpreter the incidents mode cannot start at all.
        if Path(FAST_AGENT_TOOL).is_dir():
            spec['HostConfig']['Binds'].append(FAST_AGENT_TOOL + ':' + FAST_AGENT_TOOL + ':ro')
            if Path(FAST_AGENT_PYTHON_HOME).is_dir():
                spec['HostConfig']['Binds'].append(
                    FAST_AGENT_PYTHON_HOME + ':' + FAST_AGENT_PYTHON_HOME + ':ro')
        # The MCP servers of the incident brain: the NoticePlace command, its
        # protected environment and the mesh socket of the notice store.
        if Path(NOTICEPLACE_HOME).is_dir():
            spec['HostConfig']['Binds'].append(NOTICEPLACE_HOME + ':' + NOTICEPLACE_HOME + ':ro')
        if Path(NOTICEPLACE_MESH).is_dir():
            spec['HostConfig']['Binds'].append(NOTICEPLACE_MESH + ':' + NOTICEPLACE_MESH + ':rw')
        if Path(NOTICEPLACE_MCP_ENV).is_file():
            spec['HostConfig']['Binds'].append(NOTICEPLACE_MCP_ENV + ':' + NOTICEPLACE_MCP_ENV + ':ro')
    return spec


@contextmanager
def fence_primary_admission(runtime_dir):
    """Hide both listener names without closing any accepted connection."""
    directory = runtime_dir.lstat()
    if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.geteuid() or directory.st_mode & 0o022:
        raise RuntimeError('primary admission directory is not owned by installer')
    sockets = [runtime_dir / name for name in ADMISSION_SOCKETS]
    for path in sockets:
        metadata = path.lstat()
        if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != directory.st_uid or metadata.st_nlink != 1:
            raise RuntimeError(f'primary admission listener is not exclusively owned: {path.name}')

    saved = runtime_dir / '.primary-handoff'
    saved.mkdir(mode=0o700)
    moved = []
    try:
        for path in sockets:
            backup = saved / path.name
            path.rename(backup)
            moved.append((path, backup))
        # Both names are now closed. A connect racing either rename may
        # already be queued on the old listener and must be treated as admitted.
        yield
    finally:
        conflicts = []
        for path, backup in reversed(moved):
            try:
                os.link(backup, path, follow_symlinks=False)
            except FileExistsError:
                conflicts.append(path.name)
                continue
            backup.unlink()
        if conflicts:
            raise RuntimeError(f'primary admission changed during handoff: {", ".join(conflicts)}; saved listeners retained')
        saved.rmdir()


def read_handoff_status(runtime_dir):
    try:
        return maintenance_request('status', runtime_dir)
    except FileNotFoundError:
        raise RuntimeError(
            'legacy voice cannot prove drained: no maintenance socket for admitted requests, prepared UUIDs or AudioSocket'
        ) from None


def check_primary_handoff(name, expected_container):
    """Exercise the barrier for one owned runtime and reopen it on exit."""
    if not COMPONENT_NAME.fullmatch(name):
        raise ValueError('UserIO voice component name required')

    def inspect():
        return api('GET', '/containers/'+name+'/json')

    old = inspect()
    if (old['Id'] != expected_container or not old['State']['Running']
            or old['Config'].get('Labels', {}).get(LABEL) != '1'
            or old['Config']['Labels'].get('com.userio.role') != 'calls-primary'):
        raise RuntimeError('expected running UserIO primary identity is required')
    # The ordinary installer mounts only the Docker API. A barrier check must
    # run on the receiver with the same runtime directory as the primary.
    mount = next((entry for entry in old['Mounts'] if entry['Destination'] == str(RUNTIME_DIR)), None)
    if not mount or mount['Source'] != str(RUNTIME_DIR) or not mount['RW']:
        raise RuntimeError('primary does not own the receiver admission directory')
    epoch = old['State']['StartedAt']
    with fence_primary_admission(RUNTIME_DIR):
        result = read_handoff_status(RUNTIME_DIR)
        current = inspect()
        if (current['Id'] != expected_container or not current['State']['Running']
                or current['State']['StartedAt'] != epoch):
            raise RuntimeError('primary changed during admission check')
        if result.get('safe') is not True:
            raise RuntimeError('primary admission check found outstanding work')
    return {'status': 'admission_check_passed', 'container_id': expected_container}


def install(image,role,name,config,data,port, *, timeout=75):
    if not COMPONENT_NAME.fullmatch(name):raise ValueError('UserIO voice component name required')
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
        raise RuntimeError('running primary needs a verified admission handoff before replacement')
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
                    # Keep the older rollback: its owner may still need it.
                    receipt['preserved_rollback']=archive
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
    try:
        pid = int(TRANSCRIPTION_PID.read_text())
        os.kill(pid, 0)
        if b'universal_userio.container_platform\x00transcriptions' not in Path(f'/proc/{pid}/cmdline').read_bytes():
            return 1
    except (OSError, ValueError):
        return 1
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


def supervise_gateway(argv):
    """Keep the existing durable transcription worker beside the gateway."""
    stopping = False
    gateway = worker = None
    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        if gateway is not None and gateway.poll() is None:
            gateway.terminate()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    def start_worker():
        child = subprocess.Popen([sys.executable, '-m', 'universal_userio.container_platform', 'transcriptions'])
        temporary = TRANSCRIPTION_PID.with_suffix('.tmp')
        temporary.write_text(str(child.pid))
        temporary.replace(TRANSCRIPTION_PID)
        return child
    try:
        worker = start_worker()
        gateway = subprocess.Popen(argv)
        while not stopping:
            try:
                return gateway.wait(timeout=1)
            except subprocess.TimeoutExpired:
                if worker.poll() is not None:
                    time.sleep(1)
                    worker = start_worker()
        return gateway.wait(timeout=15)
    finally:
        TRANSCRIPTION_PID.unlink(missing_ok=True)
        for child in (gateway, worker):
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)


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
    # The installer drains calls before replacement; the portable process never
    # hangs up host AudioSocket channels during startup.
    if role=='standby':os.environ['AGENTCALL_LLM_PROVIDER']='openai-compatible'
    elif settings.get('AGENTCALL_LLM_PROVIDER')!='openai-compatible' or any(not settings.get(key) for key in ('AGENTCALL_STT_URL','AGENTCALL_TTS_URL')):
        raise ValueError('primary requires explicit external STT/TTS and openai-compatible SDK configuration')
    return supervise_gateway([sys.executable,'/usr/local/lib/agentcall/agentcall_realtime_bridge.py',
             '--host','127.0.0.1','--port',port,'--event-socket','/run/agentcall/gatewayd.sock',
             '--control-socket','/run/agentcall/control.sock','--skip-stale-call-cleanup'])


def maintenance_request(action, runtime_dir):
    if action not in {'drain', 'status', 'open'}:
        raise ValueError('unsupported maintenance action')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(str(runtime_dir / 'maintenance.sock'))
        connection.sendall((json.dumps({'action': action}) + '\n').encode())
        with connection.makefile('rb') as response:
            result = json.loads(response.readline(4096))
    if result.get('ok') is not True:
        raise RuntimeError('voice maintenance request failed')
    return result


if __name__=='__main__':
    if sys.argv[1:] == ['payload']:sys.exit(verify_payload())
    if sys.argv[1:] == ['health']:sys.exit(health())
    if sys.argv[1:] == ['serve']:sys.exit(serve())
    if len(sys.argv) == 3 and sys.argv[1] == 'maintenance':
        print(json.dumps(maintenance_request(sys.argv[2], RUNTIME_DIR), sort_keys=True))
        sys.exit(0)
    else:raise SystemExit('use existing container_release.py install-voice')
