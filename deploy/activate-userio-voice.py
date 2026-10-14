#!/usr/bin/env python3
"""Bind existing voice services to an installed published UserIO release.

No new daemon, queue, configuration, accounts or call. Preserve legacy paths
and operator audio assets. Rollback restores exact prior runtime files/links.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--runtime',default='/opt/universal-userio')
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--adapter',required=True)
    parser.add_argument('--adapter-sha256',required=True)
    parser.add_argument('--receipt',required=True)
    args=parser.parse_args()
    if os.geteuid()!=0:raise RuntimeError('host administrator required')
    receipt=Path(args.receipt)
    if receipt.exists():raise RuntimeError('reconcile existing activation; do not replay')
    runtime=Path(args.runtime).resolve();voice=runtime/'src/universal_userio/voice'
    installed=json.loads((runtime/'.userio-release.json').read_text())
    manifest=json.loads(Path(args.manifest).read_text())
    if any(installed[k]!=manifest[k] for k in installed):
        raise RuntimeError('manifest differs from installed published release')
    entries={e['path']:e for e in manifest['entries']}
    files=sorted(p for p in voice.glob('*.py') if p.name!='__init__.py')
    if not files:raise RuntimeError('installed voice source missing')
    for p in files:
        rel=p.relative_to(runtime).as_posix()
        if hashlib.sha256(p.read_bytes()).hexdigest()!=entries[rel]['sha256']:
            raise RuntimeError('voice source differs from published release')
        compile(p.read_bytes(),str(p),'exec')
    channels=subprocess.check_output(['asterisk','-rx','core show channels concise'],text=True)
    if channels.strip():raise RuntimeError('live telephone channel prevents activation')
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:argv=(p/'cmdline').read_bytes().split(b'\0')
        except (OSError,PermissionError):continue
        if any(x in {b'universal_userio.matrix_call_worker',b'universal_userio.telegram_call_worker'} for x in argv):
            raise RuntimeError('live RTC call prevents activation')
    adapter=Path(args.adapter).resolve()
    if hashlib.sha256(adapter.read_bytes()).hexdigest()!=args.adapter_sha256:
        raise RuntimeError('published Exmanager adapter differs')
    legacy=Path('/usr/local/lib/agentcall')
    backup=receipt.with_suffix('.rollback');backup.mkdir(mode=0o700,parents=True,exist_ok=False)
    targets={p.name:p for p in files};targets['agentcall_secretary_voice.py']=adapter
    old={}
    for name,target in targets.items():
        path=legacy/name
        if not path.exists():raise RuntimeError('missing existing voice module: '+name)
        old[name]={'link':os.readlink(path)} if path.is_symlink() else {'file':name}
        if not path.is_symlink():shutil.copy2(path,backup/name)
    record={'status':'intent','source':manifest['commit'],'old':old,'files':{k:str(v) for k,v in targets.items()}}
    receipt.write_text(json.dumps(record,indent=2));receipt.chmod(0o600)
    try:
        for name,target in targets.items():
            temporary=legacy/(name+'.userio-new')
            temporary.symlink_to(target);os.replace(temporary,legacy/name)
        subprocess.run(['systemctl','restart','agentcall-gatewayd.service'],check=True,timeout=30)
        subprocess.run(['systemctl','is-active','--quiet','agentcall-gatewayd.service','agentcall-secretary-api.service'],check=True,timeout=10)
        for name,target in targets.items():
            if (legacy/name).resolve()!=target:raise RuntimeError('voice link differs')
        record.update(status='activated',active_calls=0)
        receipt.write_text(json.dumps(record,indent=2))
        print(json.dumps({'status':'activated','modules':len(targets),'source':manifest['commit'],'active_calls':0}))
    except Exception:
        for name,prior in old.items():
            path=legacy/name;path.unlink()
            if 'link' in prior:path.symlink_to(prior['link'])
            else:shutil.copy2(backup/name,path)
        subprocess.run(['systemctl','restart','agentcall-gatewayd.service'],check=True,timeout=30)
        record['status']='rolled_back';receipt.write_text(json.dumps(record,indent=2));raise


if __name__=='__main__':main()
