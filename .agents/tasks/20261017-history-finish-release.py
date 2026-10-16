"""Install published history-query/UI delta over the accepted UserIO image.

Expected30/max300s. Source from Git objects only, no foreign WIP. Same runtime
configuration, rollback on failure, no provider actions. Run with sudo.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
AREA = ROOT / '.tmp/20261017-history-finish'
NAME = 'userio-backend'
CONFIG = Path('/etc/userio-containers/compose.host.json')

def cmd(args, timeout=30, **kwargs):
    result = subprocess.run(list(map(str,args)), capture_output=True, timeout=timeout, **kwargs)
    if result.returncode:
        raise RuntimeError('operation failed: ' + str(args[0]))
    return result.stdout.decode().strip()

def save(name, value):
    fd = os.open(AREA / (name+'.json'), os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600)
    with os.fdopen(fd,'w') as out: json.dump(value,out)

def inspect(name): return json.loads(cmd(['docker','inspect',name]))[0]
def blob(head,path): return subprocess.check_output(['git','-C',str(ROOT),'show',head+':'+path])

def main():
    AREA.mkdir(mode=0o700, exist_ok=True)
    head=cmd(['git','-C',ROOT,'rev-parse','HEAD'])
    if head != cmd(['git','-C',ROOT,'rev-parse','origin/main']): raise RuntimeError('publish main first')
    live=inspect(NAME)
    if live['Config']['Image'] != 'universal-userio-personal-send:bf69d6b0350d': raise RuntimeError('accepted runtime changed')
    available=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))*1024
    if available < 21*1024**3: raise RuntimeError('insufficient reserve')
    # Reuse reviewed same-config recreation and active-call/meeting guard.
    modules={}
    for label,file in [('activate','20261016-personal-send-activate.py'),('release','20261016-personal-send-release.py')]:
        spec=importlib.util.spec_from_file_location(label,ROOT/'.agents/tasks'/file)
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);modules[label]=mod
    paths=['src/universal_userio/semantic_index.py','src/universal_userio/static/index.html']
    html=blob(head,paths[1]).decode()
    paths += ['src/universal_userio/static'+p for p in re.findall(r'(?:src|href)="(/assets/[^\"]+)"',html)]
    context=AREA/'context';context.mkdir()
    image='universal-userio-history:'+head[:12]
    dockerfile=['FROM '+live['Image']+'\n']
    expected={}
    for path in paths:
        data=blob(head,path);target=context/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        dest='/opt/universal-userio/src/'+path[4:];expected[dest]=hashlib.sha256(data).hexdigest()
        dockerfile.append('COPY '+path+' '+dest+'\n')
    dockerfile.append('LABEL org.opencontainers.image.revision="'+head+'"\n')
    (context/'Dockerfile').write_text(''.join(dockerfile))
    started=time.monotonic()
    cmd(['docker','build','--pull=false','--network=none','--memory=1g','--memory-swap=1g',
         '--cpu-period=100000','--cpu-quota=200000','--ulimit','nproc=128:128','-t',image,context],
        timeout=120,env={**os.environ,'DOCKER_BUILDKIT':'0'})
    modules['release'].idle()
    previous='userio-backend-prev-history-20261017'
    original=CONFIG.read_bytes()
    save('intent',dict(head=head,old_image=live['Config']['Image'],image=image,paths=paths,expected=expected,available=available))
    argv=modules['activate'].run_argv(live,image)
    cmd(['docker','stop','-t','40',NAME],timeout=60);cmd(['docker','rename',NAME,previous])
    try:
        cmd(argv,timeout=120)
        if not modules['activate'].health(NAME): raise RuntimeError('new runtime unhealthy')
        code='import pathlib,hashlib,json;print(json.dumps({p:hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in '+repr(list(expected))+'}))'
        actual=json.loads(cmd(['docker','exec',NAME,'python','-c',code]))
        if actual!=expected: raise RuntimeError('installed source mismatch')
        after=inspect(NAME)
        for key in ['Memory','MemoryReservation','MemorySwap','NanoCpus','PidsLimit','NetworkMode','RestartPolicy','Ulimits','Tmpfs','LogConfig']:
            if live['HostConfig'].get(key)!=after['HostConfig'].get(key): raise RuntimeError('changed runtime config '+key)
        if set(live['HostConfig'].get('Binds')or[])!=set(after['HostConfig'].get('Binds')or[]): raise RuntimeError('changed binds')
        configured=json.loads(original);configured['services']['backend']['image']=image
        temporary=CONFIG.with_name('.history-finish.json');temporary.write_text(json.dumps(configured,indent=2));temporary.chmod(0o600);temporary.replace(CONFIG)
    except BaseException:
        cmd(['docker','rm','-f',NAME]);cmd(['docker','rename',previous,NAME]);cmd(['docker','start',NAME])
        raise
    result=dict(installed=True,head=head,image=image,previous=previous,exact_files=len(expected),elapsed_seconds=round(time.monotonic()-started,3))
    save('receipt',result);print(json.dumps(result))

if __name__=='__main__': main()
