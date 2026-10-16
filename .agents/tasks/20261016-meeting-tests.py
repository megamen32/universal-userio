"""Bounded source tests on the existing native RTC dependency image.

Focused integration expected20/max200seconds. No network/provider credentials,
no existing runtime mounts. One pytest process, finite RAM/CPU/PIDs/temp.
"""
from pathlib import Path
import importlib.util
import json
import os
import shutil
import subprocess
import time

ROOT=Path(__file__).resolve().parents[2]
AREA=ROOT/'.tmp/20261016-meeting-diarization'
AREA.mkdir(parents=True,exist_ok=True)
TOOLS=AREA/'test-tools';TOOLS.mkdir(exist_ok=True)
for name in ('pytest','_pytest','pluggy','iniconfig','packaging','pygments','py'):
    spec=importlib.util.find_spec(name)
    source=Path(spec.origin)
    if spec.submodule_search_locations:
        if not (TOOLS/name).exists():shutil.copytree(source.parent,TOOLS/name,ignore=shutil.ignore_patterns('__pycache__'))
    elif not (TOOLS/source.name).exists():shutil.copy2(source,TOOLS/source.name)
image=json.loads(subprocess.check_output(['docker','inspect','userio-backend']))[0]['Image']
started=time.monotonic()
command=['docker','run','--rm','--name','userio-meeting-tests-20261016','--network','none',
    '--memory','3g','--memory-reservation','2g','--memory-swap','3328m','--cpus','4','--pids-limit','128',
    '--read-only','--tmpfs','/tmp:size=128m,mode=1777','--tmpfs','/run:size=16m',
    '-v',str(ROOT)+':/workspace:ro','-v',str(TOOLS)+':/test-tools:ro',
    '-e','PYTHONPATH=/workspace/src:/test-tools','-e','PYTEST_DISABLE_PLUGIN_AUTOLOAD=1',
    '-e','PYTHONDONTWRITEBYTECODE=1','-e','TELEMOST_TEST_TEMP=/tmp/telemost-tests',
    '--workdir','/workspace','--entrypoint','python',image,'-m','pytest','-q','-p','no:cacheprovider',
    'tests/test_messenger_max_media.py','tests/test_telemost.py','tests/test_telemost_signaling.py']
try:
    result=subprocess.run(command,capture_output=True,text=True,timeout=200)
except subprocess.TimeoutExpired:
    subprocess.run(['docker','stop','-t','5','userio-meeting-tests-20261016'],capture_output=True)
    raise
proof={'category':'focused integration','expectedSeconds':20,'maximumSeconds':200,
       'elapsedSeconds':round(time.monotonic()-started,3),'exitCode':result.returncode,'image':image,
       'stdout':result.stdout,'stderr':result.stderr}
(AREA/'tests.json').write_text(json.dumps(proof));print(result.stdout);print(result.stderr[-2500:])
raise SystemExit(result.returncode)
