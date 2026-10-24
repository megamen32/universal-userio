#!/usr/bin/env python3
"""Prepare and switch the existing UserIO installation to three containers.

Secrets stay in a root-only runtime configuration, outside Git and build input.
The original units and deployment files remain available for rollback.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path('/etc/userio-containers')
BACKUP = Path('/var/backups/universal-userio/containers-20261015')
VOICE_ARCHIVE_ROOT = Path('/var/backups/universal-userio')
VOICE_ARCHIVE_LOCK = Path('/run/lock/userio-voice-archive.lock')
VOICE_PROC_ROOT = Path('/proc')
VOICE_CONTAINER = 'userio-voice'
VOICE_IMAGE_REPOSITORY = 'ghcr.io/megamen32/universal-userio-voice'
VOICE_ROLLBACK_PREFIX = 'userio-voice-prev-'
VOICE_CONFIG_FILES = ('compose.host.json', 'voice.json', 'processes.json')
VOICE_CONFIG_DESTINATION = '/etc/userio-containers'
VOICE_SECRETARY_CONFIGS = {
    '/etc/agentcall-secretary-voice.json',
    '/etc/agentcall-secretary-identities.json',
}
SECRET_FIELD = re.compile(r'(?:pass(?:word)?|secret|token|api[_-]?key|credential|authorization|private[_-]?key|access[_-]?key|client[_-]?secret|(?:^|[_-])key(?:$|[_-]))', re.I)
REMOTE = 'roomhacker@192.168.2.5'
ASTERISK_HOSTS = (
    ('roomhacker@192.168.2.75', 'roomhacker-server-88'),
    ('roomhacker@192.168.2.5', 'server-44'),
)
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
VOICE = {
    'agentcall-gatewayd.service': ('gateway', ['python', '/usr/local/lib/agentcall/agentcall_realtime_bridge.py',
        '--host', '127.0.0.1', '--port', '9092', '--event-socket', '/run/agentcall/gatewayd.sock',
        '--control-socket', '/run/agentcall/control.sock']),
    'agentcall-secretary-api.service': ('voice-api', ['python', '/usr/local/lib/agentcall/agentcall_secretary_api.py']),
    'agentcall-tts-local.service': ('tts', ['/opt/tts/bin/python', '/usr/local/lib/agentcall/tts_local.py']),
    'agentcall-tts-shim.service': ('tts-fallback', ['python', '/usr/local/lib/agentcall/tts_shim.py']),
}
PLATFORM_DROPIN = Path('/etc/systemd/system/agentcall-gatewayd.service.d/userio-container-platform.conf')
BACKEND_SOURCE_HASH_PATHS = tuple('src/universal_userio/' + f for f in (
    'messenger.py', 'mcp_catalog.py', 'mcp_surface.py', 'service.py', 'http_api.py',
    'messenger_max.py', 'messenger_vk.py', 'scoped_permissions.py',
    'delivery_reconciliation.py', 'store.py', 'adapters.py', 'runtime.py',
    'voice/agentcall_phone_tts.py', 'messenger_telegram_mirror.py',
    'relay_account_bridge.py', 'contact_start.py', 'mcp_dispatch.py',
    'matrix_calls.py', 'noticeplace_calls.py', 'telemost/meetings.py', 'static/telemost.html')) + tuple(
    'deploy/telegram-qr-connector/' + f for f in ('server.mjs', 'account-card.mjs'))


def install_platform_helper():
    if PLATFORM_DROPIN.exists():
        raise RuntimeError('platform helper already installed; reconcile before cutover')
    payload = subprocess.check_output(['git', '-C', ROOT, 'show',
        'HEAD:src/universal_userio/container_platform.py'])
    helper = Path('/usr/local/lib/userio-container-platform.py')
    if helper.exists():
        raise RuntimeError('unexpected existing platform helper')
    helper.write_bytes(payload); helper.chmod(0o755)
    PLATFORM_DROPIN.parent.mkdir(exist_ok=True)
    PLATFORM_DROPIN.write_text('[Unit]\nDescription=UserIO phone lease adapter (one request, no daemon)\n'
        'ConditionPathExists=/opt/universal-userio/.userio-containerized\n'
        '[Service]\nType=oneshot\nExecStart=\n'
        'ExecStart=/usr/bin/python3 /usr/local/lib/userio-container-platform.py native-lease\n'
        'Restart=no\nTimeoutStartSec=10\nMemoryHigh=128M\nMemoryMax=128M\n'
        'MemorySwapMax=0\nCPUQuota=100%\nTasksMax=32\n')
    command(['systemctl', 'daemon-reload'])


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
    env.update(PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', PYTHONUNBUFFERED='1')
    for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
        env.setdefault(key, '1')
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
    for spec in processes:
        spec['environment'].setdefault('PATH', '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin')
        for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
            spec['environment'].setdefault(key, '1')
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


def prepare_voice():
    """Extend an existing sealed preparation; never replace its baseline."""
    baseline = json.loads((CONFIG / 'baseline.json').read_text())
    if 'voice_units' in baseline:
        raise RuntimeError('voice preparation already exists; reuse it')
    config = json.loads((CONFIG / 'processes.json').read_text())
    processes = [p for p in config['processes'] if p['name'] == 'telemost']
    mounts = {'/opt/noticeplace', '/opt/exmanager-voice/b57ae06', '/run/noticeplace-mesh',
              '/home/roomhacker/.ssh', '/root/.ssh'}
    units = []
    for unit, (name, argv) in VOICE.items():
        pid = int(output(['systemctl', 'show', unit, '-pMainPID', '--value']))
        if not pid:
            raise RuntimeError('required installed voice process is not running: ' + unit)
        env = proc_environment(pid)
        if name == 'gateway':
            env['AGENTCALL_FAST_AGENT_PYTHON'] = '/opt/voice-legacy-sdk/bin/python'
        status = Path(f'/proc/{pid}/status').read_text()
        spec = dict(name=name, command=argv, environment=env, cwd='/opt/universal-userio',
            uid=int(status.split('Uid:')[1].split()[0]), gid=int(status.split('Gid:')[1].split()[0]),
            groups=list(map(int, status.split('Groups:')[1].splitlines()[0].split())))
        processes.append(spec); units.append(unit)
        mounts.update(v for k, v in env.items() if v.startswith('/') and Path(v).is_file()
                      and any(part in k for part in ('FILE', 'CONFIG', 'IDENTITIES', 'MAP')))
    # Protected SDK settings reference separate protected key files. Preserve
    # those paths without serializing their values into images or Git.
    pending = [Path('/etc/agentcall-secretary-voice.json')]
    seen = set()
    while pending:
        path = pending.pop()
        if not path.is_file() or str(path) in seen:
            continue
        seen.add(str(path))
        mounts.add(str(path))
        if path.suffix == '.json':
            data = json.loads(path.read_text())
            def paths(value):
                if isinstance(value, dict):
                    for v in value.values(): yield from paths(v)
                elif isinstance(value, list):
                    for v in value: yield from paths(v)
                elif isinstance(value, str) and value.startswith('/'):
                    yield Path(value)
            pending.extend(paths(data))
    config['processes'] = [p for p in config['processes'] if p['name'] != 'telemost']
    gateway_env = next(p['environment'] for p in processes if p['name'] == 'gateway')
    processes.append(dict(name='transcriptions', command=['python', '-m',
        'universal_userio.container_platform', 'transcriptions'], environment=gateway_env,
        cwd='/opt/universal-userio', uid=0, gid=0))
    private_json(CONFIG / 'processes.json', config)
    private_json(CONFIG / 'voice.json', dict(primary='tts', ready_url='http://127.0.0.1:30802/health',
        health_urls=['http://127.0.0.1:30802/health', 'http://192.168.2.100:19092/v1/calls/health'],
        health_sockets=['/run/agentcall/gatewayd.sock', '/run/agentcall/control.sock',
                        '/var/lib/universal-userio/telemost-worker/worker.sock'], processes=processes))
    override = json.loads((CONFIG / 'compose.host.json').read_text())
    override['services']['voice'] = {'volumes': [f'{p}:{p}:ro' for p in sorted(mounts) if Path(p).exists()]}
    private_json(CONFIG / 'compose.host.json', override)
    baseline['voice_units'] = units
    baseline['inactive_enabled'] = {'userio-sip-provision.service':
        output(['systemctl', 'show', 'userio-sip-provision.service', '-pUnitFileState', '--value'])}
    baseline['voice_enabled'] = {u: output(['systemctl', 'show', u, '-pUnitFileState', '--value']) for u in units}
    # The former Windows Qwen forwarder is not a UserIO dependency anymore:
    # active voice configs use local30802, and current source has no18018 URL.
    baseline['retired_units'] = ['agentcall-tts-tunnel.service']
    baseline['retired_enabled'] = {u: output(['systemctl', 'show', u, '-pUnitFileState', '--value'])
                                  for u in baseline['retired_units']}
    private_json(CONFIG / 'baseline.json', baseline)
    print(json.dumps({'roles': ['backend', 'frontend', 'voice'], 'voice_processes': [p['name'] for p in processes]}))


def assert_idle():
    for target, expected_hostname in ASTERISK_HOSTS:
        args = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', target,
                "hostname -s; sudo -n asterisk -rx 'core show channels count'"]
        if os.getuid() == 0:
            args = ['runuser', '-u', 'roomhacker', '--', *args]
        lines = output(args).splitlines()
        if not lines or lines[0].strip() != expected_hostname:
            raise RuntimeError('Asterisk host identity changed; telephone-call idle state unknown')
        counts = []
        for line in lines[1:]:
            fields = line.strip().split()
            if len(fields) >= 3 and fields[1].lower() == 'active' and fields[2].lower().startswith('channel'):
                try: counts.append(int(fields[0]))
                except ValueError: pass
        if len(counts) != 1:
            raise RuntimeError('cannot read Asterisk active-call count; telephone-call idle state unknown')
        if counts[0] != 0:
            raise RuntimeError('active telephone call prevents cutover')
    # Call workers are long-lived backend processes, so their presence alone
    # does not mean a call is active. They persist a durable call receipt before
    # starting the worker. Refuse calls within their actual wall bound, including
    # the max_seconds + 240s transport deadline and 70s consumer shutdown grace.
    call_statuses = {'accepted', 'ringing', 'active', 'unknown'}
    now = time.time()
    for name in ('telegram-calls', 'matrix-calls'):
        for path in (Path('/var/lib/universal-userio') / name).glob('*/call.json'):
            try:
                record = json.loads(path.read_text())
                status = record.get('status')
                if status not in call_statuses:
                    continue
                started_at = float(record.get('started_at', record.get('created_at')))
                max_seconds = int(record['max_seconds'])
                if not 30 <= max_seconds <= 600 or now < started_at:
                    raise ValueError('call deadline is invalid')
                if now - started_at <= max_seconds + 310:
                    raise RuntimeError('active or unresolved RTC call prevents cutover')
            except RuntimeError:
                raise
            except Exception as error:
                raise RuntimeError('cannot establish RTC call idle state') from error
    remote_check = "python3 -c \"import pathlib,json; assert not any(json.loads(p.read_text())['status'] in {'accepted','waiting','recording','finalizing'} for p in pathlib.Path('" + TELEMOST_ROOT + "').glob('*/meeting.json')), 'active meeting prevents cutover'\""
    command(ssh_args(remote_check))


def _published_tip() -> str:
    """The fetched published main tip of the canonical repository."""
    return output(['git', '-C', ROOT, 'rev-parse', 'origin/main'])


def _resolve_published_revision(revision):
    """Resolve the source revision of a build context.

    Without an explicit revision the checkout itself must be the published
    main commit, exactly as before. With one, the pinned revision replaces the
    checkout as the source of truth: it must name an immutable commit object
    that is exactly the fetched published tip, so no uncommitted file and no
    older commit can reach the release.
    """
    if revision is None:
        head = output(['git', '-C', ROOT, 'rev-parse', 'HEAD'])
        if head != _published_tip():
            raise RuntimeError('build requires published main')
        return head, False
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise RuntimeError('revision must be an immutable commit id')
    if output(['git', '-C', ROOT, 'cat-file', '-t', revision]) != 'commit':
        raise RuntimeError('revision must name a commit object')
    if revision != _published_tip():
        raise RuntimeError('revision must equal the published origin/main tip')
    return revision, True


def _require_published_tip(revision: str) -> None:
    """The published tip must not move while the context is generated."""
    if _published_tip() != revision:
        raise RuntimeError('origin/main moved while the release context was generated')


def _dispatcher_input():
    """The current published standalone dispatcher, as a pinned input."""
    foreign = Path('/home/roomhacker/agents-projects/hermes-unified-inbox')
    script = 'scripts/userio_hermes_dispatcher.py'
    payload = subprocess.check_output(['git', '-C', foreign, 'show', 'HEAD:' + script])
    if payload != (foreign / script).read_bytes():
        raise RuntimeError('dispatcher has uncommitted changes; its owner must supply a published input')
    return payload, output(['git', '-C', foreign, 'rev-parse', 'HEAD'])


def _host_additions():
    """Host binaries the image carries next to the published source."""
    additions = {
        'deploy/containers/external/himalaya': Path('/home/roomhacker/.cargo/bin/himalaya').read_bytes(),
        'deploy/containers/external/asterisk': Path('/usr/sbin/asterisk').read_bytes(),
    }
    # Preserve only the installed host PBX CLI, not another PBX daemon.
    for row in output(['ldd', '/usr/sbin/asterisk']).splitlines():
        parts = row.split()
        if len(parts) > 2 and parts[1] == '=>' and parts[2].startswith('/'):
            path = Path(parts[2])
            if path.name.split('.so')[0] not in {'libc', 'libm', 'libpthread', 'librt', 'libdl'}:
                additions['deploy/containers/external/asterisk-libs/' + path.name] = path.read_bytes()
    return additions


def build_context(destination: Path, *, portable_voice=False, revision=None):
    revision, pinned = _resolve_published_revision(revision)
    archive = subprocess.check_output(['git', '-C', ROOT, 'archive', '--format=tar', revision])
    if portable_voice:
        from voice_container import build_context as portable_context
        if pinned:
            _require_published_tip(revision)
        portable_context(ROOT, revision, archive, destination)
        return
    # Preserve the current published standalone dispatcher as a pinned input.
    payload, dispatcher_commit = _dispatcher_input()
    sys.path.insert(0, str(ROOT / 'scripts'))
    from runtime_release import build_manifest
    # The manifest describes the very same committed tree the archive carries.
    manifest = build_manifest(ROOT, revision=revision) if pinned else build_manifest(ROOT)
    identity = {key: manifest[key] for key in ('schema_version', 'commit', 'manifest_sha256')}
    additions = {'deploy/containers/external/userio_hermes_dispatcher.py': payload,
                 'deploy/containers/release.json': json.dumps(identity).encode()}
    additions.update(_host_additions())
    if pinned:
        _require_published_tip(revision)
    mode = 'w:gz' if destination.suffix == '.gz' else 'w'
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as source, tarfile.open(destination, mode) as target:
        for member in source:
            if member.name.startswith(('src/', 'deploy/', 'scripts/')) or member.name in ('pyproject.toml', 'Dockerfile', '.dockerignore'):
                target.addfile(member, source.extractfile(member) if member.isfile() else None)
        for name, data in additions.items():
            info = tarfile.TarInfo(name); info.size = len(data); info.mode = 0o755 if name.endswith('/himalaya') else 0o644
            target.addfile(info, io.BytesIO(data))
    print(json.dumps({'context': str(destination), 'commit': revision,
        'dispatcher_commit': dispatcher_commit,
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
    if not baseline.get('voice_units'):
        raise RuntimeError('prepare all three roles before cutover')
    assert_idle()
    if (CONFIG / 'cutover.json').exists():
        raise RuntimeError('cutover was already attempted; inspect receipt before any further action')
    # Record ownership before the first service stop. Retrying this function
    # cannot silently replay the transition or change its baseline.
    private_json(CONFIG / 'cutover.json', {'started_at': time.time(), 'status': 'switching'})
    BACKUP.mkdir(mode=0o700, parents=True, exist_ok=True)
    proxy = Path('/etc/nginx/sites-available/msg.bezrabotnyi.com')
    shutil.copy2(proxy, BACKUP / 'nginx.conf')
    try:
        command(['systemctl', 'stop', *baseline['voice_units'], *baseline['retired_units']])
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
        install_platform_helper()
        marker = Path('/opt/universal-userio/.userio-containerized')
        marker.write_text('UserIO backend/frontend/voice are managed by Docker Compose.\n')
        marker.chmod(0o644)
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
        marker.write_text('UserIO backend/frontend/voice are managed by Docker Compose.\n')
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
    if PLATFORM_DROPIN.exists():
        PLATFORM_DROPIN.unlink()
        Path('/usr/local/lib/userio-container-platform.py').unlink(missing_ok=True)
        command(['systemctl', 'daemon-reload'])
    proxy = BACKUP / 'nginx.conf'
    if proxy.exists():
        shutil.copy2(proxy, '/etc/nginx/sites-available/msg.bezrabotnyi.com')
        command(['nginx', '-t']); command(['systemctl', 'reload', 'nginx'])
    # State is shared with the containers. Never restore an old database over
    # newly accepted messages/receipts; these backups are forensic recovery only.
    command(['systemctl', 'start', 'universal-userio.service'])
    command(['systemctl', 'start', *[u for u in baseline['units'] if u != 'universal-userio.service']])
    if baseline.get('voice_units'):
        command(['systemctl', 'start', *baseline['voice_units'], *baseline.get('retired_units', [])])
    if baseline['rtc_active']:
        target = Path('/var/lib/universal-userio/telemost-worker')
        if target.exists():
            data = subprocess.check_output(['tar', '--exclude=worker.sock', '-cf', '-', '-C', str(target), '.'])
            command(ssh_args('tar -xf - -C ' + TELEMOST_ROOT), input=data)
        command(ssh_args('systemctl --user start userio-telemost.service'))
        if (CONFIG / 'accepted.json').exists():
            command(ssh_args('systemctl --user enable userio-telemost.service'))
    if (CONFIG / 'accepted.json').exists():
        for key in ('enabled', 'voice_enabled', 'retired_enabled', 'inactive_enabled'):
            units = [u for u, value in baseline.get(key, {}).items() if value == 'enabled']
            if units: command(['systemctl', 'enable', *units])
    private_json(CONFIG / 'cutover.json', {'status': 'rolled_back', 'at': time.time()})


def finalize():
    baseline = json.loads((CONFIG / 'baseline.json').read_text())
    if json.loads((CONFIG / 'cutover.json').read_text())['status'] != 'containers_running':
        raise RuntimeError('only an accepted active migration can be finalized')
    compose('ps')
    units = [*baseline['units'], *baseline['voice_units'], *baseline['retired_units'],
             *baseline.get('inactive_enabled', {})]
    for unit in units:
        if unit == 'agentcall-gatewayd.service':
            continue  # Retained only as the native one-shot phone lease adapter.
        directory = Path('/etc/systemd/system') / (unit + '.d')
        directory.mkdir(exist_ok=True)
        (directory / 'userio-containerized.conf').write_text(
            '[Unit]\nConditionPathExists=!/opt/universal-userio/.userio-containerized\n')
    command(['systemctl', 'daemon-reload'])
    command(['systemctl', 'disable', *units])
    if baseline['rtc_active']:
        command(ssh_args('systemctl --user disable userio-telemost.service'))
    private_json(CONFIG / 'accepted.json', {'at': time.time(), 'containers': ['backend', 'frontend', 'voice']})


def replace_backend(*, image: str, revision: str, expected_container: str, receipt_directory: Path):
    import fcntl
    with open('/run/lock/userio-backend-release.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('another routine backend replacement is active') from error
        _replace_backend_locked(image=image, revision=revision, expected_container=expected_container,
                                receipt_directory=receipt_directory)


def _backend_replacement_config(previous: dict, *, image: str, revision: str) -> dict:
    """Keep runtime settings exact while recording the reviewed image revision."""
    labels = dict(previous.get('Labels') or {})
    labels['org.opencontainers.image.revision'] = revision
    return dict(previous, Image=image, Labels=labels)


def _archive_docker_json(args, description):
    """Read one Docker JSON response without ever surfacing daemon error text."""
    try:
        result = subprocess.run(['docker', *args], capture_output=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f'cannot read {description} from Docker') from error
    if result.returncode:
        raise RuntimeError(f'cannot read {description} from Docker')
    try:
        return result.stdout, json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f'invalid Docker {description} response') from error


def _archive_docker_text(args, description):
    """Read bounded plain-text Docker output without exposing daemon errors."""
    try:
        result = subprocess.run(['docker', *args], capture_output=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f'cannot read {description} from Docker') from error
    if result.returncode:
        raise RuntimeError(f'cannot read {description} from Docker')
    return result.stdout


def _archive_container(name):
    raw, rows = _archive_docker_json(['inspect', '--type', 'container', name], f'container {name}')
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise RuntimeError(f'container {name} is missing or ambiguous')
    return raw, rows[0]


def _archive_image(image_id):
    raw, rows = _archive_docker_json(['image', 'inspect', image_id], 'image metadata')
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise RuntimeError('image metadata is missing or ambiguous')
    return raw, rows[0]


def _redacted_config(value, key=None):
    """Build a non-secret projection for the manifest fingerprint only."""
    if key and SECRET_FIELD.search(str(key)):
        return '<redacted>'
    if isinstance(value, dict):
        return {str(k): _redacted_config(v, str(k)) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            if isinstance(item, str) and '=' in item:
                name, raw = item.split('=', 1)
                result.append(name + ('=<redacted>' if SECRET_FIELD.search(name) else '=' + raw))
            else:
                result.append(_redacted_config(item))
        return result
    if isinstance(value, str):
        text = re.sub(r'(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+', r'\1<redacted>', value)
        text = re.sub(r'(://)[^/@\s]+@', r'\1<redacted>@', text)
        return text
    return value


def _archive_hash(data):
    return hashlib.sha256(data).hexdigest()


def _archive_canonical_mounts(container):
    """Normalize Docker's unordered Mounts array without dropping any fields."""
    mounts = container.get('Mounts')
    if not isinstance(mounts, list) or any(not isinstance(mount, dict) for mount in mounts):
        raise RuntimeError('live Voice container mount metadata is invalid')
    try:
        return sorted(mounts, key=lambda mount: json.dumps(
            mount, sort_keys=True, ensure_ascii=False, separators=(',', ':')))
    except (TypeError, ValueError) as error:
        raise RuntimeError('live Voice container mount metadata is invalid') from error


def _archive_read_file(path: Path, *, max_bytes=512 * 1024 * 1024):
    """Read through the process mount namespace and bind the bytes to an inode."""
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise RuntimeError('mounted archive input is not a regular file')
            data = stream.read(max_bytes + 1)
    except OSError as error:
        raise RuntimeError('mounted archive input is unavailable') from error
    if len(data) > max_bytes:
        raise RuntimeError('mounted archive input exceeds archive bounds')
    identity = {'device': info.st_dev, 'inode': info.st_ino, 'size': info.st_size, 'mtime_ns': info.st_mtime_ns}
    return data, identity


def _archive_read_container_file(process_identity, container_path, *, max_bytes=512 * 1024 * 1024):
    """Read a container path with openat-style traversal confined to its root."""
    logical = PurePosixPath(str(container_path))
    if not logical.is_absolute() or any(part == '..' for part in logical.parts):
        raise RuntimeError('container file path is not a safe absolute path')
    root_path = VOICE_PROC_ROOT / str(process_identity['pid']) / 'root'
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, 'O_CLOEXEC', 0)
    nofollow = getattr(os, 'O_NOFOLLOW', 0)
    try:
        root_fd = os.open(root_path, directory_flags)
    except OSError as error:
        raise RuntimeError('container root is unavailable') from error

    dir_fds = [root_fd]
    resolved = []
    pending = [part for part in logical.parts[1:] if part not in {'', '.'}]
    links = []
    link_count = 0
    try:
        if not pending:
            raise RuntimeError('container file path has no filename')
        while pending:
            component = pending.pop(0)
            if component in {'', '.'}:
                continue
            if component == '..' or '/' in component:
                raise RuntimeError('container symlink escapes its root')
            parent_fd = dir_fds[-1]
            try:
                before = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            except OSError as error:
                raise RuntimeError('container mounted file is unavailable') from error
            if stat.S_ISLNK(before.st_mode):
                link_count += 1
                if link_count > 40:
                    raise RuntimeError('container path has too many symlinks')
                target = os.readlink(component, dir_fd=parent_fd)
                if not target or '\x00' in target:
                    raise RuntimeError('container symlink target is invalid')
                links.append({'path': '/' + '/'.join([*resolved, component]), 'target': target})
                target_path = PurePosixPath(target)
                target_parts = [part for part in target_path.parts if part not in {'', '.', '/'}]
                if any(part == '..' for part in target_parts):
                    raise RuntimeError('container symlink escapes its root')
                if target_path.is_absolute():
                    for descriptor in dir_fds[1:]:
                        os.close(descriptor)
                    dir_fds = [root_fd]
                    resolved = []
                pending = target_parts + pending
                continue

            final = not pending
            if final:
                if not stat.S_ISREG(before.st_mode):
                    raise RuntimeError('container mounted input is not a regular file')
                flags = os.O_RDONLY | nofollow | getattr(os, 'O_CLOEXEC', 0)
            else:
                if not stat.S_ISDIR(before.st_mode):
                    raise RuntimeError('container path parent is not a directory')
                flags = directory_flags | nofollow
            try:
                descriptor = os.open(component, flags, dir_fd=parent_fd)
                opened = os.fstat(descriptor)
            except OSError as error:
                raise RuntimeError('container mounted path changed during traversal') from error
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                os.close(descriptor)
                raise RuntimeError('container mounted path changed during traversal')
            if final:
                try:
                    with os.fdopen(descriptor, 'rb') as stream:
                        data = stream.read(max_bytes + 1)
                        after = os.fstat(stream.fileno())
                except OSError as error:
                    raise RuntimeError('container mounted file is unavailable') from error
                if len(data) > max_bytes:
                    raise RuntimeError('container mounted file exceeds archive bounds')
                identity = {'device': opened.st_dev, 'inode': opened.st_ino, 'size': opened.st_size,
                            'mtime_ns': opened.st_mtime_ns,
                            'resolved_container_path': '/' + '/'.join([*resolved, component])}
                if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
                        opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns):
                    raise RuntimeError('container mounted file changed while being read')
                return data, identity, links
            dir_fds.append(descriptor)
            resolved.append(component)
        raise RuntimeError('container mounted file path did not resolve to a file')
    finally:
        for descriptor in reversed(dir_fds):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _archive_directory_identity(path: Path):
    try:
        info = path.lstat()
    except OSError as error:
        raise RuntimeError('mounted archive directory is unavailable') from error
    if not stat.S_ISDIR(info.st_mode):
        raise RuntimeError('mounted archive path is not a directory')
    return {'device': info.st_dev, 'inode': info.st_ino}


def _archive_verify_file(path: Path, expected_hash: str):
    try:
        data, _ = _archive_read_file(path)
    except RuntimeError:
        return False
    return _archive_hash(data) == expected_hash


def _archive_rename_noreplace(source: Path, destination: Path):
    """Publish a completed archive atomically, refusing any existing target."""
    import ctypes
    import errno

    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, 'renameat2', None)
    if renameat2 is None:
        raise RuntimeError('atomic no-overwrite archive publication is unsupported')
    renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    renameat2.restype = ctypes.c_int
    result = renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1)  # AT_FDCWD, RENAME_NOREPLACE
    if result != 0:
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            raise RuntimeError('archive destination already exists; reconcile before retrying')
        raise RuntimeError('cannot atomically publish completed Voice archive')


def _archive_process_identity(container):
    """Identify the exact init process and mount namespace exposed by Docker."""
    pid = container.get('State', {}).get('Pid')
    if type(pid) is not int or pid <= 1:
        raise RuntimeError('running Voice container has no usable init PID')
    proc = VOICE_PROC_ROOT / str(pid)
    try:
        raw = (proc / 'stat').read_bytes()
        close = raw.rfind(b')')
        fields = raw[close + 2:].split()
        start_time = int(fields[19])
        mount_namespace_inode = (proc / 'ns' / 'mnt').stat().st_ino
        if not (proc / 'root').is_dir():
            raise OSError('container root is unavailable')
    except (OSError, ValueError, IndexError) as error:
        raise RuntimeError('cannot identify the live Voice mount namespace') from error
    return {'pid': pid, 'start_time': start_time, 'mount_namespace_inode': mount_namespace_inode}


def _archive_container_view_path(container, destination):
    """Return a lexical /proc/PID/root path; never realpath proc's magic link."""
    identity = _archive_process_identity(container)
    logical = PurePosixPath(str(destination))
    if not logical.is_absolute() or any(part in {'.', '..'} for part in logical.parts):
        raise RuntimeError('container mount destination is not a safe absolute path')
    return VOICE_PROC_ROOT / str(identity['pid']) / 'root' / Path(*logical.parts[1:])


def _archive_has_owner_mode(path: Path, mode: int):
    info = path.stat()
    return info.st_uid == 0 and info.st_gid == 0 and stat.S_IMODE(info.st_mode) == mode


def _archive_root_is_safe(path: Path):
    info = path.stat()
    return info.st_uid == 0 and info.st_gid == 0 and not (info.st_mode & 0o022)


def _archive_write(root: Path, relative: str, data: bytes):
    path = root / relative
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(path.parent, 0, 0)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.fchown(descriptor, 0, 0)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, 0o600)
    return _archive_hash(data)


def _archive_write_json(root: Path, relative: str, value):
    data = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    return _archive_write(root, relative, data)


def _archive_tree(root: Path):
    """Hash a mounted source tree without copying its source or executing it."""
    if not root.is_dir():
        raise RuntimeError('mounted SDK source is not a directory')
    entries = []
    byte_count = 0
    root_identity = _archive_directory_identity(root)
    for path in sorted(root.rglob('*')):
        if '.git' in path.relative_to(root).parts:
            continue
        if len(entries) >= 10000:
            raise RuntimeError('mounted SDK source exceeds archive bounds')
        if path.is_symlink():
            entries.append({'path': path.relative_to(root).as_posix(), 'symlink': os.readlink(path)})
        elif path.is_file():
            data, identity = _archive_read_file(path)
            byte_count += len(data)
            if byte_count > 512 * 1024 * 1024:
                raise RuntimeError('mounted SDK source exceeds archive bounds')
            entries.append({'path': path.relative_to(root).as_posix(), 'sha256': _archive_hash(data),
                            'size': len(data), 'device': identity['device'], 'inode': identity['inode']})
    if not entries:
        raise RuntimeError('mounted SDK source is empty')
    encoded = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return entries, _archive_hash(encoded), byte_count, root_identity


def _archive_mount(container, destination_prefix, *, exact=False):
    mounts = [m for m in container.get('Mounts', []) if isinstance(m, dict)
              and (str(m.get('Destination', '')) == destination_prefix if exact
                   else str(m.get('Destination', '')).startswith(destination_prefix))]
    if len(mounts) != 1:
        raise RuntimeError(f'expected one mounted source for {destination_prefix}')
    mount = mounts[0]
    source = Path(str(mount.get('Source', '')))
    if not source.is_absolute():
        raise RuntimeError(f'mounted source unavailable for {destination_prefix}')
    return mount, source


def _archive_config_sources(container):
    sources = {}
    for mount in container.get('Mounts', []):
        if not isinstance(mount, dict):
            continue
        destination = str(mount.get('Destination', ''))
        source = Path(str(mount.get('Source', '')))
        if not source.is_absolute():
            continue
        if destination == VOICE_CONFIG_DESTINATION:
            mounted_root = _archive_container_view_path(container, destination)
            _archive_directory_identity(mounted_root)
            for name in VOICE_CONFIG_FILES:
                path = mounted_root / name
                if not path.is_file() or path.is_symlink():
                    raise RuntimeError(f'required mounted config is missing: {name}')
                sources[f'userio-containers/{name}'] = {
                    'path': path, 'mount_source': str(source), 'mount_destination': destination}
        elif destination in VOICE_SECRETARY_CONFIGS:
            path = _archive_container_view_path(container, destination)
            if not path.is_file() or path.is_symlink():
                raise RuntimeError('mounted Secretary configuration is unavailable')
            sources[destination.rsplit('/', 1)[1]] = {
                'path': path, 'mount_source': str(source), 'mount_destination': destination}
    if not all(f'userio-containers/{name}' in sources for name in VOICE_CONFIG_FILES):
        raise RuntimeError('effective UserIO Voice config mount is incomplete')
    return sources


def archive_voice(receipt_directory: Path):
    """Serialize archive-only snapshots; never stop or recreate userio-voice."""
    import fcntl

    if os.geteuid() != 0:
        raise RuntimeError('archive-voice requires root')
    descriptor = os.open(VOICE_ARCHIVE_LOCK, os.O_WRONLY | os.O_CREAT, 0o600)
    os.fchown(descriptor, 0, 0)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, 'w') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('another Voice archive operation is active') from error
        return _archive_voice_locked(receipt_directory)


def _archive_voice_locked(receipt_directory: Path):
    """Archive the live Voice image/config/source identities without stopping it."""
    import datetime
    import tempfile

    receipt_directory = Path(receipt_directory)
    archive_root = VOICE_ARCHIVE_ROOT.resolve(strict=True)
    if receipt_directory.parent.resolve() != archive_root or not receipt_directory.name.startswith('voice-'):
        raise RuntimeError('archive directory must be a direct voice-* child of the canonical archive root')
    if not _archive_root_is_safe(archive_root):
        raise RuntimeError('canonical voice archive root is not protected root-owned storage')
    if receipt_directory.exists() or receipt_directory.is_symlink():
        raise RuntimeError('archive directory already exists; reconcile before retrying')

    container_raw, container = _archive_container(VOICE_CONTAINER)
    if container.get('State', {}).get('Running') is not True:
        raise RuntimeError('canonical userio-voice container is not running')
    process_identity = _archive_process_identity(container)
    container_id = str(container.get('Id', ''))
    image_id = str(container.get('Image', ''))
    if not container_id or not image_id.startswith('sha256:'):
        raise RuntimeError('live voice container identity is incomplete')
    image_raw, image = _archive_image(image_id)
    image_reference = container.get('Config', {}).get('Image')
    if not isinstance(image_reference, str) or not image_reference.startswith(
            (VOICE_IMAGE_REPOSITORY + ':', VOICE_IMAGE_REPOSITORY + '@')):
        raise RuntimeError('live voice container does not use the canonical UserIO Voice image')
    repo_digests = sorted(d for d in image.get('RepoDigests', [])
                          if isinstance(d, str) and d.startswith(VOICE_IMAGE_REPOSITORY + '@sha256:'))
    if not repo_digests:
        raise RuntimeError('live voice image has no immutable registry digest')
    labels = image.get('Config', {}).get('Labels') or container.get('Config', {}).get('Labels') or {}
    revision = labels.get('org.opencontainers.image.revision') if isinstance(labels, dict) else None
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise RuntimeError('live voice image has no source revision label')

    bridge_mount, bridge_source = _archive_mount(container, '/usr/local/lib/agentcall', exact=True)
    bridge_root = _archive_container_view_path(container, bridge_mount.get('Destination'))
    bridge_root_identity = _archive_directory_identity(bridge_root)
    bridge_path = str(PurePosixPath(str(bridge_mount.get('Destination'))) / 'agentcall_realtime_bridge.py')
    bridge_bytes, bridge_identity, bridge_symlinks = _archive_read_container_file(process_identity, bridge_path)
    bridge_hash = _archive_hash(bridge_bytes)

    sdk_mount, sdk_source = _archive_mount(container, '/opt/exmanager-voice/')
    sdk_root = _archive_container_view_path(container, sdk_mount.get('Destination'))
    sdk_root_identity = _archive_directory_identity(sdk_root)
    sdk_candidates = [p for p in sdk_root.rglob('agentcall_secretary_voice.py')
                      if p.is_file() and not p.is_symlink()]
    if len(sdk_candidates) != 1:
        raise RuntimeError('mounted Secretary SDK entrypoint is missing or ambiguous')
    sdk_entry = sdk_candidates[0]
    try:
        sdk_entry.relative_to(sdk_root)
    except ValueError as error:
        raise RuntimeError('mounted Secretary SDK entrypoint escapes its source tree') from error
    sdk_tree, sdk_tree_hash, sdk_tree_bytes, sdk_tree_root_identity = _archive_tree(sdk_root)
    sdk_entry_bytes, sdk_entry_identity = _archive_read_file(sdk_entry)
    sdk_hash = _archive_hash(sdk_entry_bytes)

    config_sources = _archive_config_sources(container)
    config_bytes = {}
    config_identities = {}
    config_mount_views = {}
    for logical, mounted in sorted(config_sources.items()):
        data, identity = _archive_read_file(mounted['path'])
        config_bytes[logical] = data
        config_identities[logical] = identity
        config_mount_views[logical] = {
            'mount_source_name': Path(mounted['mount_source']).name,
            'mount_destination': mounted['mount_destination'],
            'consumer_device': identity['device'],
            'consumer_inode': identity['inode'],
        }
    try:
        redacted_files = {name: _redacted_config(json.loads(data)) for name, data in config_bytes.items()}
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError('mounted Voice config is not valid JSON') from error
    runtime_fields = {key: container.get(key) for key in ('Config', 'HostConfig')}
    runtime_fields['Mounts'] = _archive_canonical_mounts(container)
    runtime_fields['network_names'] = sorted((container.get('NetworkSettings', {}).get('Networks') or {}).keys())
    safe_config = _redacted_config({'runtime': runtime_fields, 'mounted_config': redacted_files})
    config_fingerprint = _archive_hash(json.dumps(safe_config, sort_keys=True, separators=(',', ':')).encode())

    rollback_ids_raw = _archive_docker_text(
        ['ps', '-aq', '--no-trunc', '--filter', f'name=^/{VOICE_ROLLBACK_PREFIX}'], 'rollback container list')
    rollback_names = sorted(set(line.strip() for line in rollback_ids_raw.decode().splitlines() if line.strip()))
    if not rollback_names:
        raise RuntimeError('no retained previous userio-voice container is available for rollback')
    rollback = []
    rollback_raw = {}
    for index, identifier in enumerate(rollback_names):
        raw, old = _archive_container(identifier)
        if old.get('State', {}).get('Running') is True:
            raise RuntimeError('previous userio-voice rollback container is unexpectedly running')
        old_image_id = str(old.get('Image', ''))
        if not old_image_id.startswith('sha256:'):
            raise RuntimeError('rollback voice image identity is incomplete')
        old_image_raw, old_image = _archive_image(old_image_id)
        rollback_raw[f'rollback/container-{index}.json'] = raw
        rollback_raw[f'rollback/image-{index}.json'] = old_image_raw
        old_digests = sorted(d for d in old_image.get('RepoDigests', []) if isinstance(d, str) and '@sha256:' in d)
        rollback.append({'container_name': old.get('Name', '').lstrip('/'), 'container_id': old.get('Id'),
                         'image': old.get('Config', {}).get('Image'), 'image_id': old_image_id,
                         'repo_digests': old_digests, 'state': old.get('State', {}).get('Status')})

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    temporary = Path(tempfile.mkdtemp(prefix='.voice-archive-', dir=archive_root))
    os.chmod(temporary, 0o700)
    os.chown(temporary, 0, 0)
    try:
        file_hashes = {}
        file_hashes['container-inspect.json'] = _archive_write(temporary, 'container-inspect.json', container_raw)
        file_hashes['image-inspect.json'] = _archive_write(temporary, 'image-inspect.json', image_raw)
        file_hashes['sdk-tree.json'] = _archive_write_json(temporary, 'sdk-tree.json', sdk_tree)
        for relative, raw in sorted(rollback_raw.items()):
            file_hashes[relative] = _archive_write(temporary, relative, raw)
        for logical, raw in sorted(config_bytes.items()):
            file_hashes['config/' + logical.replace('/', '--')] = _archive_write(
                temporary, 'config/' + logical.replace('/', '--'), raw)

        # Re-read mutable inputs before publishing the completed directory.
        _, current = _archive_container(VOICE_CONTAINER)
        if (current.get('Id') != container_id or current.get('Image') != image_id
                or current.get('State', {}).get('Running') is not True
                or current.get('State', {}).get('Pid') != process_identity['pid']
                or current.get('Config') != container.get('Config')
                or current.get('HostConfig') != container.get('HostConfig')
                or _archive_canonical_mounts(current) != _archive_canonical_mounts(container)):
            raise RuntimeError('live Voice container changed during archive capture')
        if _archive_process_identity(current) != process_identity:
            raise RuntimeError('live Voice process or mount namespace changed during archive capture')
        bridge_now, bridge_identity_now, bridge_symlinks_now = _archive_read_container_file(
            process_identity, bridge_path)
        if (_archive_hash(bridge_now) != bridge_hash or bridge_identity_now != bridge_identity
                or bridge_symlinks_now != bridge_symlinks):
            raise RuntimeError('mounted AgentCall bridge changed during archive capture')
        _, verify_tree_hash, _, verify_sdk_root_identity = _archive_tree(sdk_root)
        sdk_entry_now, sdk_entry_identity_now = _archive_read_file(sdk_entry)
        if (verify_tree_hash != sdk_tree_hash or verify_sdk_root_identity != sdk_tree_root_identity
                or _archive_hash(sdk_entry_now) != sdk_hash or sdk_entry_identity_now != sdk_entry_identity):
            raise RuntimeError('mounted Secretary SDK changed during archive capture')
        for logical, mounted in config_sources.items():
            config_now, identity_now = _archive_read_file(mounted['path'])
            if (_archive_hash(config_now) != _archive_hash(config_bytes[logical])
                    or identity_now != config_identities[logical]):
                raise RuntimeError('mounted Voice config changed during archive capture')

        manifest = {
            'schema': 'userio-voice-archive/v1',
            'status': 'archive_only',
            'captured_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'container': {'name': VOICE_CONTAINER, 'id': container_id, 'state': 'running'},
            'image': {'reference': container.get('Config', {}).get('Image'), 'image_id': image_id,
                      'immutable_repo_digests': repo_digests, 'revision': revision},
            'container_process': process_identity,
            'bridge': {'mount_source_name': bridge_source.name, 'mount_destination': bridge_mount.get('Destination'),
                       'consumer_device': bridge_identity['device'], 'consumer_inode': bridge_identity['inode'],
                       'file': 'agentcall_realtime_bridge.py',
                       'resolved_container_path': bridge_identity['resolved_container_path'],
                       'symlinks': bridge_symlinks, 'sha256': bridge_hash},
            'secretary_sdk': {'mount_source_name': sdk_source.name, 'mount_destination': sdk_mount.get('Destination'),
                              'consumer_device': sdk_root_identity['device'], 'consumer_inode': sdk_root_identity['inode'],
                              'entrypoint': sdk_entry.relative_to(sdk_root).as_posix(), 'entrypoint_sha256': sdk_hash,
                              'tree_sha256': sdk_tree_hash, 'tree_files': len(sdk_tree), 'tree_bytes': sdk_tree_bytes},
            'effective_config': {'redacted_fingerprint_sha256': config_fingerprint,
                                 'captured_files': sorted(config_bytes),
                                 'mount_views': config_mount_views,
                                 'secret_values_stored_only_in_root_snapshot': True},
            'rollback': rollback,
            'snapshot_files_sha256': file_hashes,
        }
        manifest_sha = _archive_write_json(temporary, 'manifest.json', manifest)
        receipt = {'status': 'archived_only', 'archive': str(receipt_directory), 'container_id': container_id,
                   'image_id': image_id, 'image_digest': repo_digests[0], 'source_revision': revision,
                   'bridge_sha256': bridge_hash, 'sdk_entrypoint_sha256': sdk_hash,
                   'config_fingerprint_sha256': config_fingerprint, 'rollback_container_ids': [r['container_id'] for r in rollback],
                   'manifest_sha256': manifest_sha, 'no_runtime_changes': True}
        receipt_sha = _archive_write_json(temporary, 'receipt.json', receipt)
        file_hashes['manifest.json'] = manifest_sha
        file_hashes['receipt.json'] = receipt_sha
        sums = ''.join(f'{digest}  {name}\n' for name, digest in sorted(file_hashes.items())).encode()
        sums_sha = _archive_write(temporary, 'SHA256SUMS', sums)
        for relative, expected_hash in file_hashes.items():
            if not _archive_verify_file(temporary / relative, expected_hash):
                raise RuntimeError('archive snapshot readback hash mismatch')
        for path in temporary.rglob('*'):
            expected_mode = 0o700 if path.is_dir() else 0o600
            if not _archive_has_owner_mode(path, expected_mode):
                raise RuntimeError('archive snapshot is not root-only')
        if receipt_directory.exists() or receipt_directory.is_symlink():
            raise RuntimeError('archive destination appeared during capture')
        _archive_rename_noreplace(temporary, receipt_directory)
        directory = os.open(archive_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        os.chmod(receipt_directory, 0o700)
        if not _archive_has_owner_mode(receipt_directory, 0o700):
            raise RuntimeError('archive ownership is not root:root')
        for relative in [*file_hashes, 'manifest.json', 'receipt.json', 'SHA256SUMS']:
            if not _archive_has_owner_mode(receipt_directory / relative, 0o600):
                raise RuntimeError('archive file permissions or ownership are not root-only')
        return dict(receipt, archive=str(receipt_directory), sums_sha256=sums_sha)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def _replace_backend_locked(*, image: str, revision: str, expected_container: str, receipt_directory: Path):
    """Routine replacement of the canonical backend; never replay migration cutover."""
    import http.client
    import socket
    import tempfile
    import urllib.parse
    import urllib.request

    class DockerConnection(http.client.HTTPConnection):
        def connect(self):
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.connect('/var/run/docker.sock')

    def api(method, route, body=None):
        connection = DockerConnection('localhost', timeout=90)
        connection.request(method, '/v1.55/' + route,
                           body=None if body is None else json.dumps(body),
                           headers={'Content-Type': 'application/json'})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        if response.status >= 300 and not (response.status == 304 and method == 'POST' and route.split('?')[0].endswith(('/start', '/stop'))):
            # Docker error payloads can contain configuration: keep them private.
            raise RuntimeError(f'Docker {method} {route.split("?")[0]} returned {response.status}')
        return json.loads(data) if data else None

    def save(path, value):
        private_json(path, value)
        with open(path, 'rb') as stream:
            os.fsync(stream.fileno())
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def inspect(name):
        return api('GET', 'containers/' + name + '/json')

    def normalized_host(value):
        value = dict(value)
        if value.get('OomKillDisable') is None:
            value['OomKillDisable'] = False
        return value

    def atomic_config(data):
        descriptor, temporary = tempfile.mkstemp(prefix='.compose.backend-', dir=CONFIG)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, CONFIG / 'compose.host.json')
            directory = os.open(CONFIG, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def health():
        for _ in range(30):
            try:
                result = subprocess.run(['docker', 'exec', 'userio-backend', 'python', '-m',
                                         'universal_userio.container_runtime', '--health'],
                                        capture_output=True, timeout=25)
                if result.returncode == 0:
                    return
            except subprocess.TimeoutExpired:
                pass
            time.sleep(2)
        raise RuntimeError('backend supervisor readiness failed')

    def accounts():
        processes = json.loads((CONFIG / 'processes.json').read_text())['processes']
        telegram = next(p for p in processes if p['name'] == 'telegram')
        environment = telegram.get('environment', telegram.get('env', {}))
        request = urllib.request.Request('http://127.0.0.1:18093/v1/accounts',
            headers={'Authorization': 'Bearer ' + environment['USERIO_API_TOKEN']})
        with urllib.request.urlopen(request, timeout=20) as response:
            value = json.load(response)
        rows = value['accounts'] if isinstance(value, dict) else value
        return sorted(row['id'] for row in rows)

    if os.getuid() != 0:
        raise RuntimeError('replace-backend requires root')
    full_revision = output(['git', '-C', ROOT, 'rev-parse', revision])
    command(['git', '-C', ROOT, 'merge-base', '--is-ancestor', full_revision, 'origin/main'])
    image_id = api('GET', 'images/' + urllib.parse.quote(image, safe='') + '/json')['Id']
    receipt_file = receipt_directory / 'receipt.json'
    if receipt_file.exists():
        previous = json.loads(receipt_file.read_text())
        current = inspect('userio-backend')
        if (previous.get('status') == 'installed' and previous.get('revision') == full_revision
                and previous.get('image_id') == image_id and current['Id'] == previous.get('container_id')
                and current['State']['Running'] and hashlib.sha256((CONFIG / 'compose.host.json').read_bytes()).hexdigest() == previous.get('compose_sha256')):
            health()
            print(json.dumps(previous))
            return
        raise RuntimeError('release receipt already exists; reconcile before another replacement')
    if receipt_directory.exists():
        raise RuntimeError('release directory already exists; reconcile incomplete intent')
    paths = BACKEND_SOURCE_HASH_PATHS
    expected_hashes = {}
    for path in paths:
        pinned = subprocess.check_output(['git', '-C', ROOT, 'show', full_revision + ':' + path])
        if pinned != subprocess.check_output(['git', '-C', ROOT, 'show', 'origin/main:' + path]):
            raise RuntimeError('candidate source changed since pin: ' + path)
        destination = ('/opt/universal-userio/' + path if path.startswith('src/')
                       else '/opt/userio-telegram-qr/' + path.rsplit('/', 1)[1])
        expected_hashes[destination] = hashlib.sha256(pinned).hexdigest()
    code = ('import pathlib,hashlib,json; print(json.dumps({"release":json.loads(pathlib.Path('
            '"/opt/universal-userio/.userio-release.json").read_text()),"hashes":{p:hashlib.sha256('
            'pathlib.Path(p).read_bytes()).hexdigest() for p in ' + repr(list(expected_hashes)) + '}}))')
    candidate = json.loads(output(['docker', 'run', '--rm', '--network', 'none', '--entrypoint',
                                  'python', image_id, '-c', code]))
    if candidate['release']['commit'] != full_revision or candidate['hashes'] != expected_hashes:
        raise RuntimeError('candidate provenance differs from pinned published source')
    old = inspect('userio-backend')
    if old['Id'] != expected_container or not old['State']['Running']:
        raise RuntimeError('canonical backend baseline changed')
    if old['HostConfig']['NetworkMode'] != 'host' or old['HostConfig'].get('AutoRemove'):
        raise RuntimeError('unsupported backend network or auto-remove configuration')
    if api('GET', 'images/' + urllib.parse.quote(old['Config']['Image'], safe='') + '/json')['Id'] != old['Image']:
        raise RuntimeError('rollback image tag no longer resolves to baseline image')
    others = {name: inspect(name) for name in ('userio-frontend', 'userio-voice')}
    original = (CONFIG / 'compose.host.json').read_bytes()
    configured = json.loads(original)
    account_ids = accounts()
    health()
    assert_idle()
    receipt_directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    save(receipt_directory / 'baseline.json', {'backend': old, 'others': others,
        'account_ids': account_ids, 'candidate': candidate, 'image_id': image_id,
        'allowed_normalizations': {'HostConfig.OomKillDisable': [None, False]}})
    descriptor = os.open(receipt_directory / 'compose.host.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(original)
        stream.flush()
        os.fsync(stream.fileno())
    save(receipt_directory / 'intent.json', {'revision': full_revision, 'image_id': image_id,
        'expected_container': expected_container, 'started_at': time.time()})
    if inspect('userio-backend')['Id'] != old['Id'] or (CONFIG / 'compose.host.json').read_bytes() != original:
        raise RuntimeError('baseline changed after preflight; no replacement attempted')
    removed = False
    try:
        api('POST', 'containers/userio-backend/stop?t=40')
        api('DELETE', 'containers/userio-backend')
        removed = True
        expected_config = _backend_replacement_config(old['Config'], image=image, revision=full_revision)
        body = dict(expected_config, HostConfig=old['HostConfig'])
        api('POST', 'containers/create?name=userio-backend', body)
        now = inspect('userio-backend')
        save(receipt_directory / 'candidate.json', now)
        if now['Image'] != image_id or normalized_host(now['HostConfig']) != normalized_host(old['HostConfig']):
            raise RuntimeError('replacement image or HostConfig changed')
        if sorted(now['Mounts'], key=lambda mount: mount['Destination']) != sorted(old['Mounts'], key=lambda mount: mount['Destination']):
            raise RuntimeError('replacement mounts changed')
        if now['Config'] != expected_config:
            raise RuntimeError('replacement Config/Env changed')
        api('POST', 'containers/userio-backend/start')
        health()
        if accounts() != account_ids:
            raise RuntimeError('backend account identities changed')
        for name, baseline in others.items():
            if inspect(name) != baseline:
                # State timestamps/counters may evolve; configuration and identity may not.
                current = inspect(name)
                if any(current[key] != baseline[key] for key in ('Id', 'Image', 'Config', 'HostConfig')):
                    raise RuntimeError('unrelated container configuration changed: ' + name)
        configured.setdefault('services', {}).setdefault('backend', {})['image'] = image
        installed_config = json.dumps(configured, indent=2).encode()
        atomic_config(installed_config)
        receipt = {'status': 'installed', 'revision': full_revision, 'image': image,
            'image_id': image_id, 'container_id': now['Id'], 'previous_image_id': old['Image'],
            'compose_sha256': hashlib.sha256(installed_config).hexdigest(),
            'supervisor_healthy': True, 'account_ids_unchanged': True,
            'frontend_voice_configuration_unchanged': True, 'backup': str(receipt_directory)}
        save(receipt_file, receipt)
        print(json.dumps(receipt))
    except BaseException as error:
        # Never restore databases: preserve every delivery/session written during the attempt.
        if removed:
            try:
                api('GET', 'containers/userio-backend/json')
            except RuntimeError as missing:
                if not str(missing).endswith('returned 404'):
                    raise
            else:
                api('POST', 'containers/userio-backend/stop?t=20')
                api('DELETE', 'containers/userio-backend')
            if api('GET', 'images/' + urllib.parse.quote(old['Config']['Image'], safe='') + '/json')['Id'] != old['Image']:
                raise RuntimeError('rollback image tag changed; protected baseline retained') from error
            api('POST', 'containers/create?name=userio-backend', dict(old['Config'], HostConfig=old['HostConfig']))
        api('POST', 'containers/userio-backend/start')
        atomic_config(original)
        health()
        if accounts() != account_ids:
            raise RuntimeError('rollback API account readback differs; protected baseline retained') from error
        save(receipt_file, {'status': 'rolled_back', 'revision': full_revision,
            'previous_image_id': old['Image'], 'error': str(error), 'backup': str(receipt_directory)})
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('prepare', 'prepare_voice', 'context', 'cutover', 'rollback', 'finalize', 'replace-backend', 'archive-voice', 'install-voice'))
    parser.add_argument('--portable-voice', action='store_true')
    parser.add_argument('--role', choices=('standby', 'calls-primary'))
    parser.add_argument('--name', default='userio-voice')
    parser.add_argument('--config-dir', default='/etc/userio-voice')
    parser.add_argument('--data-dir', default='/var/lib/userio-voice')
    parser.add_argument('--port', type=int, default=19095)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--image')
    parser.add_argument('--revision')
    parser.add_argument('--expected-container')
    parser.add_argument('--receipt-directory', type=Path)
    args = parser.parse_args()
    if args.action == 'install-voice':
        from voice_container import install
        if not args.role or not args.image:
            parser.error('install-voice requires --role and immutable --image')
        print(json.dumps(install(args.image, args.role, args.name, args.config_dir, args.data_dir, args.port)))
        return
    if args.action == 'archive-voice':
        if not args.receipt_directory:
            parser.error('archive-voice requires --receipt-directory')
        print(json.dumps(archive_voice(args.receipt_directory), sort_keys=True))
        return
    if args.action == 'replace-backend':
        if not all((args.image, args.revision, args.expected_container, args.receipt_directory)):
            parser.error('replace-backend requires --image, --revision, --expected-container and --receipt-directory')
        replace_backend(image=args.image, revision=args.revision, expected_container=args.expected_container,
                        receipt_directory=args.receipt_directory)
        return
    if args.action == 'context':
        if not args.output:
            parser.error('context requires --output')
        build_context(args.output, portable_voice=args.portable_voice, revision=args.revision)
    else:
        globals()[args.action]()


if __name__ == '__main__':
    main()
