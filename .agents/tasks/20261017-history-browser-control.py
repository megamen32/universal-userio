"""Control only the named managed browser44; sensitive JS travels on stdin."""
import json,shlex,subprocess,sys,os
from pathlib import Path
SESSION='userio-history-owned-20261017'
HOST='roomhacker@192.168.2.5'
UNIT='userio-history-browser-proof-20261017.service'
def run(args,body=None):
 r=subprocess.run((['runuser','-u','roomhacker','--'] if os.getuid()==0 else [])+['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',HOST,'systemctl --user is-active --quiet '+shlex.quote(UNIT)+' && '+shlex.join(['/usr/bin/agent-browser','--session',SESSION,*args])],input=body,text=True,capture_output=True,timeout=60)
 if r.returncode:raise RuntimeError('browser command failed: '+r.stderr[:250])
 print(r.stdout)
if sys.argv[1]=='login':
 e=dict(l.split('=',1)for l in Path('/home/roomhacker/agents-projects/universal-userio/.env.owner-seed').read_text().splitlines()if '='in l and not l.startswith('#'))
 u=e['USERIO_SEED_USERNAME'].strip().strip('\"');p=e['USERIO_SEED_PASSWORD'].strip().strip('\"')
 script='''(() => {const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;for(const [selector,value]of %s){const el=document.querySelector(selector);set.call(el,value);el.dispatchEvent(new Event('input',{bubbles:true}));}document.querySelector('button').click();return 'login submitted';})()''' % json.dumps([['input[name=username]',u],['input[name=password]',p]])
 run(['eval','--stdin'],script)
elif sys.argv[1]=='eval':run(['eval','--stdin'],sys.stdin.read())
else:run(sys.argv[1:])
