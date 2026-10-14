"""Install reviewed UserIO Telemost source only; no meeting is joined here."""
from pathlib import Path
import io
import subprocess
import shlex
import tarfile

ROOT=Path(__file__).resolve().parents[1]
HOST='roomhacker@192.168.2.5'
REMOTE='/home/roomhacker/.local/share/userio-telemost'

def remote(command,data=None):
    return subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',HOST,command],input=data,check=True)

if __name__=='__main__':
    remote('test "$(hostname)" = server-44')
    archive=io.BytesIO()
    with tarfile.open(fileobj=archive,mode='w') as tar:
        for name in ('__init__.py','transport.py','worker.py','client.py'):
            path=ROOT/'src/universal_userio/telemost'/name
            tar.add(path,arcname='universal_userio/telemost/'+name)
        # Standalone native worker artifact intentionally doesn't import UserIO's
        # 100-only service/db package initializer.
        content=b'"""Standalone UserIO RTC deployment artifact."""\n'
        info=tarfile.TarInfo('universal_userio/__init__.py');info.size=len(content);info.mode=0o644
        tar.addfile(info,io.BytesIO(content))
    remote('mkdir -p '+REMOTE+'/source; tar -xf - -C '+REMOTE+'/source',archive.getvalue())
    unit=(ROOT/'deploy/userio-telemost.service').read_bytes()
    remote('mkdir -p ~/.config/systemd/user; cat > ~/.config/systemd/user/userio-telemost.service',unit)
    remote(REMOTE+'/venv/bin/python -c '+shlex.quote('import aiortc,aiohttp;print("RTC dependency imports ready")'))
    remote('systemctl --user daemon-reload && systemctl --user enable --now userio-telemost.service')
