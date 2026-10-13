"""Install the already verified pinned native wheel beside the UserIO runtime.

Offline; no account, auth, provider, voice configuration or source build.
Run through the owning project's bounded source-job/deployment route.
"""
import hashlib
from pathlib import Path
import subprocess
import sys

wheel=Path(sys.argv[1]).resolve()
if (wheel.name!='ntgcalls-3.0.3-cp310-cp310-manylinux_2_28_x86_64.whl'
    or hashlib.sha256(wheel.read_bytes()).hexdigest()!=sys.argv[2]):
    raise SystemExit('verified pinned native wheel required')
runtime=Path('/opt/universal-userio/telegram-call-runtime')
if not runtime.exists():
    subprocess.run(['/usr/bin/python3.10','-m','venv','--without-pip','--system-site-packages',str(runtime)],check=True)
site=runtime/'lib/python3.10/site-packages'
if not (site/'ntgcalls-3.0.3.dist-info').is_dir():
    subprocess.run(['/usr/bin/python3.10','-m','pip','install','--no-index','--no-deps',
        '--no-cache-dir','--target',str(site),str(wheel)],check=True)
subprocess.run([str(runtime/'bin/python'),'-c',
    "import ntgcalls,importlib.metadata; assert importlib.metadata.version('ntgcalls')=='3.0.3'; print('Native Telegram runtime3.0.3 ready')"],check=True)
