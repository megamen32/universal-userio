"""Publish-first single-file form release; same-config backend and rollback.

Expected60/max600s. Build1GiB/swap0/CPU2/PIDs128, context<1MiB;
live3GiB+256MiBswap unchanged. No login, password, messaging or DB mutation.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
AREA = ROOT / 'var/20261017-login-form-repair/release'
CONFIG = Path('/etc/userio-containers/compose.host.json')
NAME = 'userio-backend'
PREVIOUS = 'userio-backend-prev-login-form-20261017'
SOURCE = 'deploy/telegram-qr-connector/server.mjs'
TARGET = '/opt/userio-telegram-qr/server.mjs'

def cmd(args, timeout=30, **kwargs):
    r = subprocess.run(list(map(str, args)), capture_output=True, timeout=timeout, **kwargs)
    if r.returncode: raise RuntimeError('operation failed: ' + str(args[0]))
    return r.stdout.decode().strip()

def inspect(name): return json.loads(cmd(['docker', 'inspect', name]))[0]
def save(name, value):
    fd = os.open(AREA / (name + '.json'), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as f: json.dump(value, f); f.flush(); os.fsync(f.fileno())

def module(label, file):
    spec = importlib.util.spec_from_file_location(label, ROOT / '.agents/tasks' / file)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

def qr_state():
    # Resolve the existing reader's service credential inside its namespace.
    # Only safe slot metadata exits the container; no credential or QR value.
    code = '''import json,urllib.request
from pathlib import Path
vals=None
for p in Path('/proc').iterdir():
 if not p.name.isdigit():continue
 try:
  if (p/'cmdline').read_bytes().startswith(b'python\\x00-m\\x00universal_userio.runtime\\x00'):
   vals=dict(x.split('=',1) for x in (p/'environ').read_bytes().decode().split(chr(0)) if '=' in x);break
 except OSError:pass
if vals is None:raise RuntimeError('reader unavailable')
req=urllib.request.Request(vals['USERIO_TELEGRAM_QR_URL']+'/state',headers={'Authorization':'Bearer '+vals['USERIO_API_TOKEN']})
with urllib.request.urlopen(req,timeout=5) as r:rows=json.loads(r.read(32768))
f=Path('/var/lib/universal-userio/telegram-qr/sessions/account-4.session')
print(json.dumps({'slot':[{k:s.get(k) for k in ['id','accountId','status','sync','syncChats','syncError']} for s in rows if s['id']=='account-4'],'saved':f.exists() and f.stat().st_size>0 and f.stat().st_mode&0o777==0o600}))'''
    return json.loads(cmd(['docker', 'exec', NAME, 'python', '-c', code], timeout=15))

def ready(state):
    return state['saved'] and len(state['slot']) == 1 and all([
        state['slot'][0]['accountId'] == 'telegram:540308572',
        state['slot'][0]['status'] == 'connected', state['slot'][0]['sync'] == 'live',
        not state['slot'][0]['syncError']])

def atomic_config(data):
    temporary = CONFIG.with_name('.login-form-20261017.json')
    temporary.write_bytes(data); temporary.chmod(0o600); temporary.replace(CONFIG)

def main():
    AREA.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (AREA / 'intent.json').exists(): raise RuntimeError('inspect same release intent; do not replay')
    head = cmd(['git', '-C', ROOT, 'rev-parse', 'HEAD'])
    if head != cmd(['git', '-C', ROOT, 'rev-parse', 'origin/main']): raise RuntimeError('publish first')
    data = subprocess.check_output(['git', '-C', ROOT, 'show', head + ':' + SOURCE])
    expected = hashlib.sha256(data).hexdigest()
    if b'body:new URLSearchParams(new FormData(f))' not in data: raise RuntimeError('published form fix absent')
    before = inspect(NAME)
    if before['Config']['Image'] != 'universal-userio-history:92f8ffc8c7cf': raise RuntimeError('accepted runtime changed')
    if not ready(qr_state()): raise RuntimeError('current account4 login/backfill not ready')
    available = int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:'))) * 1024
    if available < 21 * 1024**3: raise RuntimeError('20GiB reserve plus1GiB build unavailable')
    old = cmd(['docker', 'exec', NAME, 'python', '-c', 'import hashlib;print(hashlib.sha256(open('+repr(TARGET)+',"rb").read()).hexdigest())'])
    if old != '83aaf1802bf067b16b8a8fb9c65eaed2447d6ca3fafbefc5ff38f3dfa6cec5da': raise RuntimeError('accepted server source changed')
    release = module('form_release_guard', '20261016-personal-send-release.py')
    activate = module('form_recreate', '20261016-personal-send-activate.py')
    image = 'universal-userio-login-form:' + head[:12]
    started = time.monotonic()
    prepared = AREA / 'prepared.json'
    if prepared.exists():
        if json.loads(prepared.read_text()) != {'head':head,'image':image,'expected':expected}: raise RuntimeError('prepared source changed')
    else:
        context = AREA / 'context'; context.mkdir()
        (context / 'server.mjs').write_bytes(data)
        (context / 'Dockerfile').write_text('FROM ' + before['Image'] + '\nCOPY server.mjs ' + TARGET + '\nLABEL org.opencontainers.image.revision="' + head + '"\n')
        cmd(['docker', 'build', '--pull=false', '--network=none', '--memory=1g', '--memory-swap=1g',
             '--cpu-period=100000', '--cpu-quota=200000', '--ulimit', 'nproc=128:128', '-t', image, context],
            timeout=180, env={**os.environ, 'DOCKER_BUILDKIT': '0'})
    # Run only a bounded source probe, never image defaults/provider clients.
    code = 'import hashlib;print(hashlib.sha256(open('+repr(TARGET)+',"rb").read()).hexdigest())'
    got = cmd(['docker', 'run', '--rm', '--network=none', '--memory=128m', '--memory-swap=128m', '--cpus=1', '--pids-limit=32', image, 'python', '-c', code])
    if got != expected: raise RuntimeError('built object mismatch')
    if not prepared.exists(): save('prepared', {'head':head,'image':image,'expected':expected})
    if '--build-only' in sys.argv:
        print(json.dumps({'prepared':True,'image':image,'published':head,'activated':False,'elapsedSeconds':round(time.monotonic()-started,3)})); return
    release.idle()
    if not ready(qr_state()): raise RuntimeError('login ceased to be ready before activation')
    original = CONFIG.read_bytes()
    save('intent', {'head': head, 'image': image, 'beforeImage': before['Config']['Image'], 'expected': expected, 'availableBytes': available, 'previous': PREVIOUS})
    argv = activate.run_argv(before, image)
    cmd(['docker', 'stop', '-t', '40', NAME], timeout=60)
    cmd(['docker', 'rename', NAME, PREVIOUS])
    try:
        cmd(argv, timeout=60)
        healthy = False
        for _ in range(30):
            r = subprocess.run(['docker', 'exec', NAME, 'python', '-m', 'universal_userio.container_runtime', '--health'], capture_output=True, timeout=10)
            if r.returncode == 0: healthy = True; break
            time.sleep(2)
        if not healthy: raise RuntimeError('new backend unhealthy')
        if cmd(['docker', 'exec', NAME, 'python', '-c', code]) != expected: raise RuntimeError('installed object mismatch')
        after = inspect(NAME)
        for key in ['Memory','MemoryReservation','MemorySwap','NanoCpus','CpuShares','PidsLimit','NetworkMode','RestartPolicy','Ulimits','Tmpfs','LogConfig','ReadonlyRootfs']:
            if before['HostConfig'].get(key) != after['HostConfig'].get(key): raise RuntimeError('changed runtime field '+key)
        if set(before['HostConfig'].get('Binds') or []) != set(after['HostConfig'].get('Binds') or []): raise RuntimeError('changed bind set')
        for _ in range(40):
            state = qr_state()
            if ready(state): break
            time.sleep(3)
        else: raise RuntimeError('saved account4 did not return live')
        configured = json.loads(original); configured['services']['backend']['image'] = image
        atomic_config(json.dumps(configured, indent=2).encode())
    except BaseException:
        cmd(['docker','rm','-f',NAME]); cmd(['docker','rename',PREVIOUS,NAME]); cmd(['docker','start',NAME])
        atomic_config(original)
        raise
    result = {'installed':True,'head':head,'image':image,'previous':PREVIOUS,'sourceSha256':expected,'sameConfig':True,'account4':state,'elapsedSeconds':round(time.monotonic()-started,3)}
    save('receipt', result); print(json.dumps(result))

if __name__ == '__main__': main()
