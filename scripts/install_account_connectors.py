#!/usr/bin/env python3
"""Install published own connectors while retaining old sessions/configuration."""
import argparse,hashlib,json,os,pwd,grp,subprocess,time
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('--receipt',required=True);args=p.parse_args()
assert os.geteuid()==0
root=Path(__file__).resolve().parents[1]
def run(*cmd):return subprocess.run(cmd,capture_output=True,check=True).stdout
commit=run('git','-C',str(root),'rev-parse','HEAD').decode().strip()
assert commit==run('git','-C',str(root),'rev-parse','origin/main').decode().strip()
def source(name):return run('git','-C',str(root),'show',commit+':'+name)
area=Path(args.receipt).resolve().parent/'connector-rollback';area.mkdir(mode=0o700,parents=True,exist_ok=False)
changed=[]
def install(path,data,mode=0o644):
 path=Path(path);backup=area/str(len(changed))
 if path.exists():backup.write_bytes(path.read_bytes());backup.chmod(0o600)
 changed.append({'path':str(path),'previous':str(backup) if backup.exists() else None,'sha256':hashlib.sha256(data).hexdigest()})
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name(path.name+'.userio-new');tmp.write_bytes(data);tmp.chmod(mode);tmp.replace(path)
# Refuse to overwrite an unreviewed newer deployment of the owned Node wrapper.
old=Path('/opt/userio-whatsapp-qr/server.mjs')
assert hashlib.sha256(old.read_bytes()).hexdigest()=='3a67b725225cc692228ae3ac9c9e8ff0d472d0fa73098baca93485b6903ff350'
for name in ['server.mjs','enrollment-owner.mjs','tenant-routing.mjs','package.json','package-lock.json']:
 install('/opt/userio-whatsapp-qr/'+name,source('deploy/whatsapp-qr-connector/'+name))
install('/etc/systemd/system/userio-whatsapp-qr.service.d/userio-resources.conf',source('deploy/userio-whatsapp-qr-resource.conf'))
state=Path('/var/lib/universal-userio/sip');state.mkdir(mode=0o700,exist_ok=True);state.chmod(0o700)
config=Path('/etc/asterisk/userio-peers');config.mkdir(mode=0o750,exist_ok=True);os.chown(config,0,grp.getgrnam('asterisk').gr_gid)
peer=config/'peers.conf'
if not peer.exists():install(peer,b'; UserIO personal SIM peers\n',0o640);os.chown(peer,0,grp.getgrnam('asterisk').gr_gid)
install(config/'extensions.conf',source('deploy/extensions-userio-sim.conf'),0o640);os.chown(config/'extensions.conf',0,grp.getgrnam('asterisk').gr_gid)
for name,include in [('sip.conf',str(peer)),('extensions.conf',str(config/'extensions.conf'))]:
 path=Path('/etc/asterisk')/name;line='#include "'+include+'"'
 data=path.read_text()
 if line not in data:install(path,(data.rstrip()+'\n'+line+'\n').encode(),path.stat().st_mode&0o777)
install('/etc/systemd/system/userio-sip-provision.service',source('deploy/userio-sip-provision.service'))
run('systemctl','daemon-reload');run('/usr/sbin/asterisk','-rx','dialplan reload');run('/usr/sbin/asterisk','-rx','sip reload')
run('systemctl','enable','userio-sip-provision.service');run('systemctl','start','userio-sip-provision.service');run('systemctl','restart','userio-whatsapp-qr.service')
for unit in ['userio-sip-provision.service','userio-whatsapp-qr.service']:run('systemctl','is-active',unit)
Path(args.receipt).write_text(json.dumps({'commit':commit,'changed':changed,'at':time.time(),'sessions_preserved':True,'sends':0,'calls':0},indent=2));Path(args.receipt).chmod(0o600)
print(json.dumps({'installed':True,'commit':commit,'receipt':args.receipt}))
