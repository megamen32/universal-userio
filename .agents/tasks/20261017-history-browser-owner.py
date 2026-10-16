import json,os,signal,subprocess,time
from pathlib import Path
root=Path.home()/'.local/state/userio-history-20261017'
root.mkdir(exist_ok=True,mode=0o700)
for n in ('profile','tmp','config','cache','data'):(root/n).mkdir(exist_ok=True,mode=0o700)
env=dict(os.environ,TMPDIR=str(root/'tmp'),XDG_CONFIG_HOME=str(root/'config'),XDG_CACHE_HOME=str(root/'cache'),XDG_DATA_HOME=str(root/'data'),DBUS_SESSION_BUS_ADDRESS='unix:path='+str(root/'no-shared-bus'))
cmd=['/usr/bin/agent-browser','--session','userio-history-owned-20261017','--headed','--profile',str(root/'profile')]
signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(SystemExit()))
try:
 r=subprocess.run(cmd+['open','https://msg.bezrabotnyi.com/login'],env=env,capture_output=True,text=True,timeout=45)
 (root/'launch.json').write_text(r.stdout)
 if r.returncode:raise RuntimeError('own browser launch failed')
 (root/'ready').write_text('ready')
 while not (root/'finish').exists():
  total=sum(p.stat().st_size for p in root.rglob('*') if p.is_file() and not p.is_symlink())
  if total>128*1024*1024:raise RuntimeError('browser storage cap exceeded')
  time.sleep(2)
finally:
 subprocess.run(cmd+['close'],env=env,capture_output=True,timeout=15)
