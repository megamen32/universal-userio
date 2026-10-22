#!/usr/bin/env python3
"""Keep the existing native Secretary application transport private over SSH.

Resolves the fixed native app and refreshes its protected JWT at each supervised
start. The app's deployment owner must restart this unit after app replacement;
SSH keepalives cover SSH failure, not an otherwise healthy stale upstream IP.
"""
from __future__ import annotations

import base64
import ipaddress
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time

APP_ID = 'c8b0c2c6-dc99-463f-baed-ff049b455be8'
SSH_HOST = 'roomhacker@192.168.2.100'
LOCAL_PORT = 18086
STATE = Path('/etc/agentcall-secretary')
REMOTE = r'''
import json,subprocess
name='airlock-dev-agent-c8b0c2c6-dc99-463f-baed-ff049b455be8'
x=json.loads(subprocess.check_output(['docker','inspect',name],timeout=10))[0]
if x['Name']!='/'+name or not x['State']['Running']:raise SystemExit(2)
addresses={v['IPAddress'] for v in x['NetworkSettings']['Networks'].values() if v.get('IPAddress')}
if len(addresses)!=1:raise SystemExit(3)
env=dict(v.split('=',1) for v in x['Config']['Env'] if '=' in v)
print(json.dumps({'address':next(iter(addresses)),'token':env['AIRLOCK_AGENT_TOKEN'],
                 'containerId':x['Id'],'imageId':x['Image']}))
'''


def validate(snapshot):
    address = ipaddress.ip_address(snapshot['address'])
    if address.version != 4 or not address.is_private or address.is_loopback:
        raise ValueError('fixed app private address required')
    token = snapshot['token'].strip()
    chunks = token.split('.')
    if len(chunks) != 3:
        raise ValueError('native token format invalid')
    claims = json.loads(base64.urlsafe_b64decode(chunks[1] + '=' * (-len(chunks[1]) % 4)))
    if claims.get('agent_id') != APP_ID or claims.get('sub') != APP_ID:
        raise ValueError('fixed app token identity invalid')
    if not all(isinstance(snapshot.get(k), str) and snapshot[k] for k in ('containerId', 'imageId')):
        raise ValueError('native runtime identity missing')
    return str(address), token, claims.get('token_version')


def atomic_private(path, raw):
    fd, temporary = tempfile.mkstemp(prefix='.voice-sdk-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    # stdout from this exact SSH command is a protected in-memory transfer.
    # Never forward raw command failures, environment or snapshot to logs.
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                             SSH_HOST, 'python3 -c ' + shlex.quote(REMOTE)],
                            capture_output=True, timeout=20)
    if result.returncode or len(result.stdout) > 32768:
        raise RuntimeError('fixed native app resolution failed')
    snapshot = json.loads(result.stdout)
    address, token, version = validate(snapshot)
    metadata = STATE.stat()
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
        raise PermissionError('voice credential directory must be private and service owned')
    atomic_private(STATE / 'voice.token', (token + '\n').encode())
    receipt = {k: snapshot[k] for k in ('containerId', 'imageId')}
    receipt.update(address=address, tokenVersion=version, resolvedAt=time.time())
    atomic_private(STATE / 'upstream.json', (json.dumps(receipt) + '\n').encode())
    print(json.dumps({'transport': 'private SSH', 'fixedApp': APP_ID, 'tokenVersion': version,
                      'resolved': True}), flush=True)
    command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
               '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=10',
               '-o', 'ServerAliveCountMax=3', '-N', '-L',
               f'127.0.0.1:{LOCAL_PORT}:{address}:8080', SSH_HOST]
    os.execvp(command[0], command)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'resolved': False, 'errorClass': type(error).__name__}), flush=True)
        raise SystemExit(1)
