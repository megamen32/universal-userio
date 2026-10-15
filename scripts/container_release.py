#!/usr/bin/env python3
"""Prepare and switch the existing UserIO installation to two containers.

Secrets stay in a root-only runtime configuration, outside Git and build input.
The original units and deployment files remain available for rollback.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path('/etc/userio-containers')
BACKUP = Path('/var/backups/universal-userio/containers-20261015')
REMOTE = 'roomhacker@192.168.2.5'
TELEMOST_ROOT = '/home/roomhacker/.local/state/userio-telemost'
WORKERS = {
    'universal-userio.service': ('core', ['python', '-m', 'universal_userio.runtime']),
    'userio-gmail-ingress.service': ('gmail', ['python', '-m', 'universal_userio.gmail_ingress']),
    'userio-max-ingress.service': ('max', ['python', '-m', 'universal_userio.max_ingress', '--backfill', '100']),
    'userio-sms-ingress.service': ('sms', ['python', '-m', 'universal_userio.sms_ingress']),
    'userio-agentcall-ingress.service': ('call-events', ['python', '-m', 'universal_userio.agentcall_ingress']),
    'userio-agentcall-archive-ingress.service': ('call-archive', ['python', '-m', 'universal_userio.agentcall_archive_ingress']),
    'userio-matrix-bridge.service': ('matrix-bridge', ['python', '/opt/userio-matrix-bridge/bridge.py', '--config', '/var/lib/userio-matrix-bridge/config.json']),
    'userio-hermes-dispatcher.service': ('secretary', ['python', '/opt/userio-migration-deps/userio_hermes_dispatcher.py']),
    'userio-telegram-ingress.service': ('telegram', ['node', '/opt/userio-telegram-qr/server.mjs']),
    'userio-whatsapp-qr.service': ('whatsapp', ['node', '/opt/userio-whatsapp-qr/server.mjs']),
    'userio-sip-provision.service': ('sip-settings', ['python', '-m', 'universal_userio.sip_connections']),
}


def command(argv, **kwargs):
    return subprocess.run([str(v) for v in argv], check=True, **kwargs)


def output(argv) -> str:
    return command(argv, capture_output=True, text=True).stdout.strip()


def ssh_args(remote_command):
    args = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', REMOTE, remote_command]
    return ['runuser', '-u', 'roomhacker', '--', *args] if os.getuid() == 0 else args


def private_json(path: Path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def proc_environment(pid: int) -> dict[str, str]:
    env = dict(row.decode().split('=', 1) for row in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0') if b'=' in row)
    # systemd's credential mount goes away when its old unit stops. Resolve the
    # same already-installed credentials locally, without printing any value.
    directory = env.pop('CREDENTIALS_DIRECTORY', '')
    if directory:
        for path in Path(directory).iterdir():
            if path.is_file():
                for row in path.read_text().splitlines():
                    key, sep, value = row.partition('=')
                    if sep and key and not key.startswith('#'):
                        env.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    for key in list(env):
        if key.startswith(('INVOCATION_', 'JOURNAL_', 'WATCHDOG_', 'NOTIFY_', 'SYSTEMD_')):
            del env[key]
    env.update(PATH='/usr/local/bin:/usr/bin:/bin', PYTHONUNBUFFERED='1')
    return env


def database_identity():
    db = sqlite3.connect('file:/var/lib/universal-userio/userio.sqlite3?mode=ro', uri=True)
    try:
        result = {}
        for table in ('users', 'accounts', 'drafts', 'messages', 'messenger_actions'):
            columns = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
            if not columns:
                continue
            result[table] = {'count': db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]}
            if 'status' in columns:
                result[table]['states'] = dict(db.execute(f'SELECT status,COUNT(*) FROM {table} GROUP BY status'))
        return result
    finally:
        db.close()


def prepare():
    if os.getuid() != 0:
        raise RuntimeError('prepare requires root to preserve installed credentials')
    if (CONFIG / 'baseline.json').exists():
        raise RuntimeError('prepared migration already exists; reuse it instead of replacing the baseline')
    CONFIG.mkdir(mode=0o700, exist_ok=True)
    CONFIG.chmod(0o700)
    processes, units, mounts = [], [], set()
    for unit, (name, argv) in WORKERS.items():
        values = dict(r.split('=', 1) for r in output(['systemctl', 'show', unit, '-pMainPID', '-pUser', '-pActiveState', '-pWorkingDirectory']).splitlines())
        if unit == 'userio-sip-provision.service' and Path('/etc/asterisk/userio-peers').is_dir():
            processes.append(dict(name=name, command=argv, environment={'HOME': '/root'}, cwd='/opt/universal-userio', uid=0, gid=0))
            continue
        if values['ActiveState'] != 'active' or not int(values['MainPID']):
            continue
        pid = int(values['MainPID'])
        env = proc_environment(pid)
        uid = int(Path(f'/proc/{pid}/status').read_text().split('Uid:')[1].split()[0])
        gid = int(Path(f'/proc/{pid}/status').read_text().split('Gid:')[1].split()[0])
        groups = list(map(int, Path(f'/proc/{pid}/status').read_text().split('Groups:')[1].splitlines()[0].split()))
        cwd = values['WorkingDirectory']
        if name == 'secretary':
            cwd = '/var/lib/hermes-userio-dispatcher'
            env['PYTHONPATH'] = '/opt/universal-userio/src'
        if name == 'core':
            env.update(USERIO_TELEMOST_LOCAL='1', USERIO_TELEMOST_ROOT='/var/lib/universal-userio/telemost-worker')
        for key, value in env.items():
            if value.startswith('/') and Path(value).is_file() and ('FILE' in key or 'CONFIG' in key):
                mounts.add(value)
        processes.append(dict(name=name, command=argv, environment=env, cwd=cwd, uid=uid, gid=gid, groups=groups))
        units.append(unit)
    if not any(p['name'] == 'core' for p in processes):
        raise RuntimeError('the existing core must be running during preparation')
    rtc_active = output(ssh_args('systemctl --user is-active userio-telemost.service')) == 'active'
    if rtc_active:
        processes.append(dict(name='telemost', command=['python', '-m', 'universal_userio.telemost.worker'],
            uid=1000, gid=1000, cwd='/opt/universal-userio', environment={
                'HOME': '/home/roomhacker', 'USERIO_TELEMOST_ROOT': '/var/lib/universal-userio/telemost-worker',
                'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}))
    private_json(CONFIG / 'processes.json', {'ready_url': 'http://127.0.0.1:18093/v1/accounts', 'processes': processes})
    for path in ('/home/roomhacker/.config/himalaya', '/home/roomhacker/.ssh', '/root/.ssh',
                 '/home/roomhacker/.hermes/scripts/himalaya-gmail-password',
                 '/home/roomhacker/.hermes/scripts/himalaya-careviolan-gmail-password',
                 '/home/roomhacker/.hermes/secrets/himalaya-careviolan-gmail-app-password',
                 '/home/roomhacker/.hermes/secrets/himalaya-yandex-app-password',
                 '/home/roomhacker/.hermes/secrets/universal-userio-gmail',
                 '/home/roomhacker/agents-projects/hermes-config/runtime/.env'):
        if Path(path).exists():
            mounts.add(path)
    # Keep mount credentials out of public compose. Only paths, never values,
    # are written to this root-only installation override.
    override = {'services': {'backend': {'volumes': [f'{p}:{p}:ro' for p in sorted(mounts)]}}}
    private_json(CONFIG / 'compose.host.json', override)
    baseline = dict(units=units, rtc_active=rtc_active, database=database_identity(),
                    enabled={u: output(['systemctl', 'show', u, '-pUnitFileState', '--value']) for u in units},
                    prepared_at=time.time())
    private_json(CONFIG / 'baseline.json', baseline)
    print(json.dumps({'prepared': True, 'processes': [p['name'] for p in processes], 'connections_preserved': True}))


def build_context(destination: Path):
    revision = output(['git', '-C', ROOT, 'rev-parse', 'HEAD'])
    if revision != output(['git', '-C', ROOT, 'rev-parse', 'origin/main']):
        raise RuntimeError('build requires published main')
    archive = subprocess.check_output(['git', '-C', ROOT, 'archive', '--format=tar', revision])
    # Preserve the current published standalone dispatcher as a pinned input.
    foreign = Path('/home/roomhacker/agents-projects/hermes-unified-inbox')
    script = 'scripts/userio_hermes_dispatcher.py'
    payload = subprocess.check_output(['git', '-C', foreign, 'show', 'HEAD:' + script])
    if payload != (foreign / script).read_bytes():
        raise RuntimeError('dispatcher has uncommitted changes; its owner must supply a published input')
    sys.path.insert(0, str(ROOT / 'scripts'))
    from runtime_release import build_manifest
    manifest = build_manifest(ROOT)
    identity = {key: manifest[key] for key in ('schema_version', 'commit', 'manifest_sha256')}
    mode = 'w:gz' if destination.suffix == '.gz' else 'w'
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as source, tarfile.open(destination, mode) as target:
        for member in source:
            if member.name.startswith(('src/', 'deploy/', 'scripts/')) or member.name in ('pyproject.toml', 'Dockerfile', '.dockerignore'):
                target.addfile(member, source.extractfile(member) if member.isfile() else None)
        additions = {'deploy/containers/external/userio_hermes_dispatcher.py': payload,
                     'deploy/containers/external/himalaya': Path('/home/roomhacker/.cargo/bin/himalaya').read_bytes(),
                     'deploy/containers/release.json': json.dumps(identity).encode()}
        for name, data in additions.items():
            info = tarfile.TarInfo(name); info.size = len(data); info.mode = 0o755 if name.endswith('/himalaya') else 0o644
            target.addfile(info, io.BytesIO(data))
    print(json.dumps({'context': str(destination), 'commit': revision,
        'dispatcher_commit': output(['git', '-C', foreign, 'rev-parse', 'HEAD']),
        'context_sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}))


def compose(*args):
    return command(['docker', 'compose', '-f', ROOT / 'compose.yaml', '-f', CONFIG / 'compose.host.json', *args])


def backup_sqlite(path: Path):
    original = sqlite3.connect(str(path))
    backup = sqlite3.connect(str(BACKUP / path.name))
    try:
        original.backup(backup)
    finally:
        backup.close(); original.close()


def cutover():
    baseline = json.loads((CONFIG / 'baseline.json').read_text())
    if (CONFIG / 'cutover.json').exists():
        raise RuntimeError('cutover was already attempted; inspect receipt before any further action')
    # Record ownership before the first service stop. Retrying this function
    # cannot silently replay the transition or change its baseline.
    private_json(CONFIG / 'cutover.json', {'started_at': time.time(), 'status': 'switching'})
    BACKUP.mkdir(mode=0o700, parents=True, exist_ok=True)
    proxy = Path('/etc/nginx/sites-available/msg.bezrabotnyi.com')
    shutil.copy2(proxy, BACKUP / 'nginx.conf')
    try:
        command(['systemctl', 'stop', *[u for u in baseline['units'] if u != 'universal-userio.service']])
        command(['systemctl', 'stop', 'universal-userio.service'])
        if baseline['rtc_active']:
            command(ssh_args('systemctl --user stop userio-telemost.service'))
            target = Path('/var/lib/universal-userio/telemost-worker'); target.mkdir(mode=0o700, exist_ok=True)
            data = subprocess.check_output(ssh_args('tar --exclude=worker.sock -cf - -C ' + TELEMOST_ROOT + ' .'))
            with tarfile.open(fileobj=io.BytesIO(data)) as tar:
                # The existing local archive contains owned relative paths only.
                for member in tar.getmembers():
                    if member.name.startswith('/') or '..' in Path(member.name).parts or member.issym() or member.islnk():
                        raise RuntimeError('unexpected RTC archive member')
                tar.extractall(target)
            command(['chown', '-R', '1000:1000', target])
        for path in (Path('/var/lib/universal-userio/userio.sqlite3'),
                     Path('/var/lib/userio-matrix-bridge/bridge.sqlite3'),
                     Path('/var/lib/hermes-userio-dispatcher/pending-requests.sqlite3')):
            if path.exists():
                backup_sqlite(path)
        compose('up', '-d', '--no-build', '--wait', '--wait-timeout', '120')
        text = proxy.read_text()
        old = '    location / {\n        proxy_pass http://127.0.0.1:18093;'
        new = '    location / {\n        proxy_pass http://127.0.0.1:18094;'
        if old not in text:
            raise RuntimeError('the expected owned ingress location changed')
        proxy.write_text(text.replace(old, new))
        command(['nginx', '-t'])
        command(['systemctl', 'reload', 'nginx'])
        marker = Path('/opt/universal-userio/.userio-containerized')
        marker.write_text('UserIO backend/frontend are managed by Docker Compose.\n')
        marker.chmod(0o644)
        private_json(CONFIG / 'cutover.json', {'started_at': time.time(), 'status': 'containers_running',
                                             'rollback': 'sudo python3 scripts/container_release.py rollback'})
        print(json.dumps({'containers_running': True, 'old_units_stopped': len(baseline['units'])}))
    except BaseException:
        rollback()
        raise


def rollback():
    baseline = json.loads((CONFIG / 'baseline.json').read_text())
    compose('down')
    Path('/opt/universal-userio/.userio-containerized').unlink(missing_ok=True)
    proxy = BACKUP / 'nginx.conf'
    if proxy.exists():
        shutil.copy2(proxy, '/etc/nginx/sites-available/msg.bezrabotnyi.com')
        command(['nginx', '-t']); command(['systemctl', 'reload', 'nginx'])
    # State is shared with the containers. Never restore an old database over
    # newly accepted messages/receipts; these backups are forensic recovery only.
    command(['systemctl', 'start', 'universal-userio.service'])
    command(['systemctl', 'start', *[u for u in baseline['units'] if u != 'universal-userio.service']])
    if baseline['rtc_active']:
        target = Path('/var/lib/universal-userio/telemost-worker')
        if target.exists():
            data = subprocess.check_output(['tar', '--exclude=worker.sock', '-cf', '-', '-C', str(target), '.'])
            command(ssh_args('tar -xf - -C ' + TELEMOST_ROOT), input=data)
        command(ssh_args('systemctl --user start userio-telemost.service'))
    private_json(CONFIG / 'cutover.json', {'status': 'rolled_back', 'at': time.time()})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('prepare', 'context', 'cutover', 'rollback'))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.action == 'context':
        if not args.output:
            parser.error('context requires --output')
        build_context(args.output)
    else:
        globals()[args.action]()


if __name__ == '__main__':
    main()
