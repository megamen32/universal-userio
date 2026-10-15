"""Narrow compatibility adapters for the existing phone lease and call jobs.

The phone lease is still issued by its real native systemd unit and unchanged
host guard. This module never grants a lease itself. Transcription runs in the
voice container, once per explicit submission, within its aggregate budget.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

STATE = Path('/var/lib/universal-userio/container-platform')
RECORDINGS = Path('/var/lib/agentcall/recordings')
JOBS = RECORDINGS / '.container-jobs'
UNIT = 'agentcall-gatewayd.service'
HELPER = '/usr/local/lib/userio-container-platform.py'


def private_write(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream)
    temporary.replace(path)


def lease_arguments(args):
    if not args or len(args) > 16 or any(len(v) > 256 for v in args):
        raise ValueError('invalid lease request')
    if args[0] == 'acquire':
        # Match the existing gateway's fixed system requests; never an agent
        # impersonating a native invocation or an arbitrary host command.
        pairs = dict(zip(args[1::2], args[2::2]))
        if len(args) != 9 or set(pairs) != {'--actor-type', '--owner', '--purpose', '--minutes'}:
            raise ValueError('invalid lease acquisition')
        if pairs['--actor-type'] != 'system' or pairs['--owner'] not in {
                'system:agentcall-tts-google', 'system:agentcall-tts', 'system:agentcall-gatewayd'}:
            raise ValueError('unsupported lease owner')
        if pairs['--minutes'] != '3' or not pairs['--purpose'].strip():
            raise ValueError('invalid lease bounds')
    elif args[0] in {'check', 'release'}:
        if len(args) != 2 or not re.fullmatch('[0-9a-f]{32}', args[1]):
            raise ValueError('invalid lease ID')
    else:
        raise ValueError('unsupported lease operation')
    return args


def lease_proxy(args):
    lease_arguments(args)
    command = ['runuser', '-u', 'roomhacker', '--', 'ssh', '-o', 'BatchMode=yes',
               '-o', 'ConnectTimeout=3', 'roomhacker@127.0.0.1',
               'sudo -n python3 ' + HELPER + ' host-lease']
    result = subprocess.run(command, input=json.dumps(args).encode(), capture_output=True, timeout=9)
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    return result.returncode


def host_lease():
    args = lease_arguments(json.loads(sys.stdin.buffer.read(4097)))
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE / 'lease.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        private_write(STATE / 'request.json', args)
        (STATE / 'response.json').unlink(missing_ok=True)
        subprocess.run(['systemctl', 'start', UNIT], check=True, timeout=8)
        result = json.loads((STATE / 'response.json').read_text())
    sys.stdout.write(result['stdout']); sys.stderr.write(result['stderr'])
    return result['returncode']


def native_lease():
    args = lease_arguments(json.loads((STATE / 'request.json').read_text()))
    result = subprocess.run(['/usr/local/bin/s21-lease', *args], capture_output=True, text=True, timeout=6)
    private_write(STATE / 'response.json', dict(returncode=result.returncode,
                  stdout=result.stdout[:8192], stderr=result.stderr[:8192]))
    return 0  # A legitimate busy refusal is returned to the caller, not retried.


def submit(args):
    if len(args) != 3 or args[:2] != ['start', '--no-block']:
        raise ValueError('only call transcription submission is supported')
    match = re.fullmatch(r'agentcall-transcribe@([0-9a-f-]{36})\.service', args[2])
    if not match:
        raise ValueError('unsupported container system command')
    import uuid
    identity = str(uuid.UUID(match[1]))
    if identity != match[1] or not (RECORDINGS / identity / 'manifest.json').is_file():
        raise ValueError('existing call archive required')
    JOBS.mkdir(mode=0o700, exist_ok=True)
    if len(list(JOBS.iterdir())) >= 4096:
        raise RuntimeError('transcription queue capacity')
    try:
        fd = os.open(JOBS / (identity + '.json'), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return 0
    with os.fdopen(fd, 'w') as stream:
        json.dump(dict(id=identity, status='QUEUED', submitted_at=time.time()), stream)
        stream.flush(); os.fsync(stream.fileno())
    return 0


def transcriptions():
    JOBS.mkdir(mode=0o700, exist_ok=True)
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    for path in JOBS.glob('*.json'):
        record = json.loads(path.read_text())
        if record['status'] == 'RUNNING':
            record['status'] = 'UNKNOWN'; private_write(path, record)
    while not stopping:
        for path in sorted(JOBS.glob('*.json')):
            record = json.loads(path.read_text())
            if record['status'] != 'QUEUED' or stopping: continue
            record['status'] = 'RUNNING'; private_write(path, record)
            try:
                result = subprocess.run([sys.executable,
                    '/usr/local/lib/agentcall/agentcall_recording_transcribe.py',
                    str(RECORDINGS / record['id'] / 'manifest.json')], timeout=1200)
                record['status'] = 'COMPLETE' if result.returncode == 0 else 'FAILED'
            except subprocess.TimeoutExpired:
                record['status'] = 'UNKNOWN'
            private_write(path, record)
        time.sleep(1)
    return 0


def main():
    modes = {'lease': lambda: lease_proxy(sys.argv[2:]), 'host-lease': host_lease,
             'native-lease': native_lease, 'submit': lambda: submit(sys.argv[2:]),
             'transcriptions': transcriptions}
    return modes[sys.argv[1]]()


if __name__ == '__main__':
    sys.exit(main())
