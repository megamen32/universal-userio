"""Scoped immutable backend release over the currently accepted image.

Expected30/max300seconds. No dependency rebuild, frontend/voice replacement,
configuration/credential changes, provider requests or historical send replay.
Run as root only for the existing protected Compose override; publish first.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
AREA=ROOT/'.tmp/20261016-meeting-diarization/release'
OWNED=['src/universal_userio/messenger_max_media.py','src/universal_userio/telemost/meetings.py']
CONFIG=Path('/etc/userio-containers/compose.host.json')

def command(args,**kwargs):
    return subprocess.run([str(x)for x in args],check=True,capture_output=True,text=True,timeout=kwargs.pop('timeout',30),**kwargs).stdout.strip()

def save(name,value):
    p=AREA/(name+'.json');fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w')as f:json.dump(value,f);f.flush();os.fsync(f.fileno())

def inspect(name):return json.loads(command(['docker','inspect',name]))[0]

def source_hashes():
    code='import pathlib,hashlib,json; r=pathlib.Path("/opt/universal-userio/src");print(json.dumps({str(p.relative_to(r)):hashlib.sha256(p.read_bytes()).hexdigest()for p in r.rglob("*.py")}))'
    return json.loads(command(['docker','exec','userio-backend','python','-c',code]))

def idle():
    # Existing no-replay cutover check, plus current container-owned recorder.
    spec=importlib.util.spec_from_file_location('container_release',ROOT/'scripts/container_release.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.assert_idle()
    code='import pathlib,json; print(json.dumps([r["id"]for p in pathlib.Path("/var/lib/universal-userio/telemost").glob("*/meeting.json")if (r:=json.loads(p.read_text())).get("status")in {"accepted","waiting","recording","finalizing","submitting"}]))'
    if json.loads(command(['docker','exec','userio-backend','python','-c',code])):raise RuntimeError('active meeting; no backend replacement')

def main():
    AREA.mkdir(mode=0o700,parents=True,exist_ok=True)
    head=command(['git','-C',ROOT,'rev-parse','HEAD'])
    if head!=command(['git','-C',ROOT,'rev-parse','origin/main']):raise RuntimeError('publish reviewed main first')
    if command(['git','-C',ROOT,'diff','HEAD','--',*OWNED]):raise RuntimeError('owned sources differ from published input')
    available=int(next(l.split()[1]for l in Path('/proc/meminfo').read_text().splitlines()if l.startswith('MemAvailable:')))*1024
    if available<21*1024**3:raise RuntimeError('insufficient fresh capacity for1GiB build plus20GiB reserve')
    backend,frontend,voice=map(inspect,['userio-backend','userio-frontend','userio-voice'])
    before=source_hashes();context=AREA/'context';context.mkdir()
    for path in OWNED:
        key=path.removeprefix('src/')
        baseline=subprocess.check_output(['git','-C',str(ROOT),'show','HEAD^:'+path])
        if before.get(key)!=hashlib.sha256(baseline).hexdigest():raise RuntimeError('owned runtime source differs from reviewed parent')
        data=subprocess.check_output(['git','-C',str(ROOT),'show','HEAD:'+path]);target=context/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    save('before',{'head':head,'backendId':backend['Id'],'baseImage':backend['Image'],
         'frontendId':frontend['Id'],'voiceId':voice['Id'],'sourceHashes':before,'available':available})
    command(['docker','tag',backend['Image'],'userio-meeting-parent:20261016'])
    dockerfile='FROM userio-meeting-parent:20261016\n'+''.join('COPY '+p+' /opt/universal-userio/'+p+'\n'for p in OWNED)+f'LABEL org.opencontainers.image.revision="{head}"\n'
    (context/'Dockerfile').write_text(dockerfile)
    image='universal-userio-meeting:'+head[:12]
    started=time.monotonic()
    build=command(['docker','build','--pull=false','--network=none','--memory=1g','--memory-swap=1g',
        '--cpu-period=100000','--cpu-quota=200000','--ulimit','nproc=128:128','-t',image,context],
        env={**os.environ,'DOCKER_BUILDKIT':'0'},timeout=120)
    save('build',{'image':image,'elapsedSeconds':time.monotonic()-started,'log':build[-12000:]})
    idle()
    original=CONFIG.read_bytes();configured=json.loads(original)
    configured.setdefault('services',{}).setdefault('backend',{})['image']=image
    save('activation-intent',{'head':head,'oldImage':backend['Image'],'newImage':image,'configSHA256':hashlib.sha256(original).hexdigest()})
    # Same root-owned control plane, replacing ONLY this backend image field.
    temporary=CONFIG.with_name('.compose.meeting-20261016.json')
    temporary.write_text(json.dumps(configured,indent=2));temporary.chmod(CONFIG.stat().st_mode&0o777);temporary.replace(CONFIG)
    compose=['docker','compose','-f',ROOT/'compose.yaml','-f',CONFIG,'up','-d','--no-deps','--no-build','--wait','--wait-timeout','120','backend']
    try:command(compose,timeout=150)
    except Exception:
        temporary.write_bytes(original);temporary.chmod(CONFIG.stat().st_mode&0o777);temporary.replace(CONFIG)
        command(compose,timeout=150)
        raise
    after=inspect('userio-backend');hashes=source_hashes()
    expected=dict(before)
    for path in OWNED:expected[path.removeprefix('src/')]=hashlib.sha256((ROOT/path).read_bytes()).hexdigest()
    assert hashes==expected,'unowned installed source changed'
    assert inspect('userio-frontend')['Id']==frontend['Id'] and inspect('userio-voice')['Id']==voice['Id']
    for k in ('Memory','MemoryReservation','MemorySwap','NanoCpus','PidsLimit','Binds','Tmpfs'):
        assert after['HostConfig'][k]==backend['HostConfig'][k],k
    save('installed',{'head':head,'image':after['Image'],'backendId':after['Id'],'sameFrontend':True,'sameVoice':True,
         'sameLimitsMounts':True,'sourceDiffOnlyOwned':True,'health':after['State'].get('Health',{}).get('Status')})
    print(json.dumps({'installed':True,'head':head,'image':after['Image'],'sameOtherContainers':True,'sameLimitsMounts':True}))

if __name__=='__main__':main()
