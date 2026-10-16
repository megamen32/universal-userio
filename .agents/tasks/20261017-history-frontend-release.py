"""Thin published static update of the separate frontend; exact Docker config.

Expected20/max200s, 1GiB/CPU2 builder, no credentials/build dependencies.
Does not restart backend/voice. The previous frontend is retained for rollback.
"""
import hashlib,http.client,json,os,re,socket,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
AREA=ROOT/'.tmp/20261017-history-frontend'
NAME='userio-frontend'
CONFIG=Path('/etc/userio-containers/compose.host.json')
class Docker(http.client.HTTPConnection):
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(60);self.sock.connect('/var/run/docker.sock')
def api(method,path,body=None):
    c=Docker('localhost',timeout=60)
    try:
        c.request(method,'/v1.45'+path,json.dumps(body)if body is not None else None,{'Content-Type':'application/json'})
        r=c.getresponse();data=r.read()
        if r.status>=300:raise RuntimeError('Docker '+method+' failed '+str(r.status))
        return json.loads(data)if data else None
    finally:c.close()
def cmd(args):return subprocess.check_output(list(map(str,args)),stderr=subprocess.DEVNULL,text=True,timeout=120).strip()
def main():
    AREA.mkdir(mode=0o700,exist_ok=True)
    head=cmd(['git','-C',ROOT,'rev-parse','HEAD'])
    if head!=cmd(['git','-C',ROOT,'rev-parse','origin/main']):raise RuntimeError('publish first')
    live=api('GET','/containers/'+NAME+'/json')
    if live['Config']['Image']!='universal-userio-frontend:search-20261016':raise RuntimeError('frontend changed')
    context=AREA/'context';context.mkdir(exist_ok=True)
    def blob(path):return subprocess.check_output(['git','-C',str(ROOT),'show',head+':'+path])
    base='src/universal_userio/static';html=blob(base+'/index.html')
    paths=['/index.html']+re.findall(r'(?:src|href)="(/assets/[^\"]+)"',html.decode())
    lines=['FROM '+live['Image']+'\n'];hashes={}
    for p in paths:
        data=blob(base+p);target=context/p.lstrip('/');target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        hashes[p]=hashlib.sha256(data).hexdigest();lines.append('COPY '+p.lstrip('/')+' /usr/share/nginx/html'+p+'\n')
    (context/'Dockerfile').write_text(''.join(lines))
    image='universal-userio-frontend:history-'+head[:12]
    cmd(['docker','build','--network=none','--pull=false','--memory=1g','--memory-swap=1g','--cpu-period=100000','--cpu-quota=200000','-t',image,context])
    previous=NAME+'-prev-history-20261017'
    api('POST','/containers/'+NAME+'/stop?t=10');api('POST','/containers/'+NAME+'/rename?name='+previous)
    try:
        body={**live['Config'],'Image':image,'HostConfig':live['HostConfig']}
        api('POST','/containers/create?name='+NAME,body);api('POST','/containers/'+NAME+'/start')
        after=api('GET','/containers/'+NAME+'/json')
        for key in ['Memory','MemoryReservation','MemorySwap','NanoCpus','CpuQuota','CpuPeriod','PidsLimit','NetworkMode','Binds','Tmpfs','LogConfig']:
            if after['HostConfig'].get(key)!=live['HostConfig'].get(key):raise RuntimeError('config differs '+key)
        for _ in range(20):
            try:
                c=http.client.HTTPConnection('127.0.0.1',18094,timeout=2);c.request('GET','/healthz');r=c.getresponse();r.read();c.close()
                if r.status==200:break
            except OSError:pass
            time.sleep(.5)
        else:raise RuntimeError('frontend health failed')
        configured=json.loads(CONFIG.read_bytes());configured['services'].setdefault('frontend',{})['image']=image
        p=CONFIG.with_name('.frontend-history.json');p.write_text(json.dumps(configured,indent=2));p.chmod(0o600);p.replace(CONFIG)
    except BaseException:
        try:api('DELETE','/containers/'+NAME+'?force=true')
        except Exception:pass
        api('POST','/containers/'+previous+'/rename?name='+NAME);api('POST','/containers/'+NAME+'/start');raise
    fd=os.open(AREA/'receipt.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    result=dict(installed=True,head=head,image=image,previous=previous,hashes=hashes,exact_host_config=True)
    with os.fdopen(fd,'w')as f:json.dump(result,f)
    print(json.dumps(result))
if __name__=='__main__':main()
