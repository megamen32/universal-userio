"""Exact Git-blob overlay and same-config backend replacement; no provider actions.

prepare/build are read-only toward the running backend. activate is explicit.
Manifest entries: {revision, path, sha256}; messenger is pinned automatically.
Additional connector/bridge entries require the Lead's reviewed manifest.
"""
import argparse
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import time

ROOT = Path(__file__).resolve().parents[3]
AREA = Path(__file__).resolve().parent / 'private'
BASE = 'sha256:27549e5190b4537e148eb278eca3e00160537a784e33ef817e3278b918fa8f05'
PIN = {'revision': '975fcc5b3235c9ba5725a1b8f659cb0ba7eedf9c',
       'path': 'src/universal_userio/messenger.py',
       'sha256': 'ff6dd08e4e42b0385a0dab16ee3396599ad2287c56111906d65859804002f365'}
CONFIG = Path('/etc/userio-containers/compose.host.json')
PROCESSES = Path('/etc/userio-containers/processes.json')
NAME = 'userio-backend'


def digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(value).hexdigest()


def command(args, timeout=60):
    result = subprocess.run([str(x) for x in args], capture_output=True, timeout=timeout)
    if result.returncode:
        # Docker/API diagnostics can contain credentials: never echo stderr/argv.
        raise RuntimeError('command failed: ' + str(args[0]) + ', exit=' + str(result.returncode))
    return result.stdout


def inspect(name):
    return json.loads(command(['docker', 'inspect', name]))[0]


def save(name, data):
    AREA.mkdir(mode=0o700, parents=True, exist_ok=True)
    AREA.chmod(0o700)
    with os.fdopen(os.open(AREA / (name + '.json'), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as handle:
        json.dump(data, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())


def load(name):
    return json.loads((AREA / (name + '.json')).read_text())


def protected_write(path, content):
    stat = path.stat()
    temporary = path.with_name('.' + path.name + '.history-repair')
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.st_mode & 0o777), 'wb') as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chown(temporary, stat.st_uid, stat.st_gid)
    temporary.replace(path)


def retained_rows():
    with sqlite3.connect('file:/var/lib/universal-userio/userio.sqlite3?mode=ro', uri=True) as db:
        rows = db.execute('SELECT message_id,body,direction,received_at FROM messages '
                          'WHERE conversation_id=? ORDER BY received_at,message_id',
                          ('conv_05b6c8996307c403fdb31851',)).fetchall()
    return {'count': len(rows), 'fingerprint': hashlib.sha256(
        json.dumps(rows, ensure_ascii=False).encode()).hexdigest(),
        'rows': {row[0]: digest(row) for row in rows}}


def process_config(original, changes):
    value = json.loads(original)
    matches = [p for p in value['processes'] if p['name'] == 'telegram']
    assert len(matches) == 1
    env = matches[0]['environment']
    assert len({c['key'] for c in changes}) == len(changes)
    for change in changes:
        assert change['key'].startswith('TELEGRAM_'), 'override outside Telegram configuration'
        assert env.get(change['key']) == change['before'], 'process setting baseline mismatch'
        env[change['key']] = change['after']
    return json.dumps(value, indent=2).encode()


def helper():
    return RuntimeHelpers()


class RuntimeHelpers:
    @staticmethod
    def hashes(container, root, pattern, relative):
        code = ('import pathlib,hashlib,json;r=pathlib.Path(' + repr(root) + ');'
                'print(json.dumps({str(p.relative_to(r)) if ' + repr(relative) + ' else p.name:'
                'hashlib.sha256(p.read_bytes()).hexdigest() for p in r.' +
                ('rglob' if relative else 'glob') + '(' + repr(pattern) + ')}))')
        return json.loads(command(['docker', 'exec', container, 'python', '-c', code]))

    def runtime_hashes(self, container):
        return self.hashes(container, '/opt/universal-userio/src', '*.py', True)

    def connector_hashes(self, container):
        return self.hashes(container, '/opt/userio-telegram-qr', '*.mjs', False)

    @staticmethod
    def unknown_draft_hash(container):
        code = ('import sqlite3,json,hashlib;d=sqlite3.connect('
                '"file:/var/lib/universal-userio/userio.sqlite3?mode=ro",uri=True);'
                'r=d.execute("select id,status,body,outbox_receipt from drafts '
                'where status=\'sending\' order by id").fetchall();'
                'print(hashlib.sha256(json.dumps(r,sort_keys=True,default=str).encode()).hexdigest())')
        return command(['docker', 'exec', container, 'python', '-c', code]).decode().strip()

    @staticmethod
    def health():
        for _ in range(40):
            try:
                command(['docker', 'exec', NAME, 'python', '-m',
                         'universal_userio.container_runtime', '--health'], timeout=25)
                return True
            except (RuntimeError, subprocess.TimeoutExpired):
                time.sleep(2)
        return False

    @staticmethod
    def idle():
        path = ROOT / 'scripts/container_release.py'
        expected = 'd6c110daa57eb1e667ddcbb2af12ce6c706fc791917737cca10378f8b767cdb0'
        assert digest(path.read_bytes()) == expected, 'idle-check helper changed'
        assert digest(command(['git', '-C', ROOT, 'show', 'origin/main:scripts/container_release.py'])) == expected
        spec = importlib.util.spec_from_file_location('history_idle_helper', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.assert_idle()
        for name in (NAME, 'userio-voice'):
            processes = command(['docker', 'top', name, '-eo', 'pid,args']).decode()
            assert not any(x in processes for x in ('universal_userio.matrix_call_worker',
                                                   'universal_userio.telegram_call_worker')), 'active RTC call'
        code = ('import pathlib,json;r=[json.loads(p.read_text()) for p in '
                'pathlib.Path("/var/lib/universal-userio/telemost").glob("*/meeting.json")];'
                'print(sum(x.get("status") in {"accepted","waiting","recording",'
                '"finalizing","submitting"} for x in r))')
        assert int(command(['docker', 'exec', NAME, 'python', '-c', code])) == 0, 'active meeting'


def host_config(spec):
    return {**spec['HostConfig'], 'OomKillDisable': bool(spec['HostConfig'].get('OomKillDisable'))}


def config_fingerprint(spec):
    return digest({'Config': spec['Config'], 'HostConfig': host_config(spec)})


def destination(path):
    if path.startswith('src/universal_userio/'):
        return '/opt/universal-userio/' + path
    if path.startswith('deploy/telegram-qr-connector/') and path.count('/') == 2:
        return '/opt/userio-telegram-qr/' + path.rsplit('/', 1)[1]
    raise ValueError('source path outside reviewed overlay boundary')


def entries(extra):
    values = [PIN] + (json.loads(Path(extra).read_text()) if extra else [])
    assert len({v['path'] for v in values}) == len(values), 'duplicate overlay path'
    for value in values:
        path, revision = value['path'], value['revision']
        assert '..' not in Path(path).parts and '\n' not in path and ' ' not in path
        destination(path)
        assert len(revision) == 40 and all(c in '0123456789abcdef' for c in revision)
        command(['git', '-C', ROOT, 'merge-base', '--is-ancestor', revision, 'origin/main'])
        blob = command(['git', '-C', ROOT, 'show', revision + ':' + path])
        assert digest(blob) == value['sha256'], 'reviewed blob hash mismatch'
    return values


def prepare(extra, preflight=False, process_overrides=None):
    values = entries(extra) if not preflight else []
    live = inspect(NAME)
    assert live['Image'] == BASE and live['State']['Running'], 'accepted backend changed'
    assert live['HostConfig']['NetworkMode'] == 'host'
    assert not any(k.startswith('com.docker.compose') for k in live['Config'].get('Labels', {}))
    m = helper()
    before = {'backend_id': live['Id'], 'base_image': BASE,
              'config_fingerprint': config_fingerprint(live),
              'host_fingerprint': digest(host_config(live)),
              'environment_fingerprint': digest(live['Config'].get('Env')),
              'mounts_fingerprint': digest(live['Mounts']),
              'frontend_id': inspect('userio-frontend')['Id'],
              'voice_id': inspect('userio-voice')['Id'],
              'python_hashes': m.runtime_hashes(NAME),
              'connector_hashes': m.connector_hashes(NAME),
              'unknown_draft_hash': m.unknown_draft_hash(NAME),
              'compose_fingerprint': digest(CONFIG.read_bytes()),
              'processes_fingerprint': digest(PROCESSES.read_bytes()),
              'retained': retained_rows(), 'entries': values,
              'process_changes': json.loads(Path(process_overrides).read_text()) if process_overrides else []}
    process_config(PROCESSES.read_bytes(), before['process_changes'])
    save('preflight' if preflight else 'baseline', before)
    print(json.dumps({'prepared': True, 'base_image': BASE, 'files': len(values),
                      'config_fingerprint': before['config_fingerprint']}))


def unchanged(before):
    live = inspect(NAME)
    assert live['Id'] == before['backend_id'] and live['Image'] == BASE, 'backend changed'
    assert config_fingerprint(live) == before['config_fingerprint'], 'runtime config changed'
    assert digest(CONFIG.read_bytes()) == before['compose_fingerprint'], 'compose config changed'
    assert digest(PROCESSES.read_bytes()) == before['processes_fingerprint'], 'process config changed'
    m = helper()
    assert m.runtime_hashes(NAME) == before['python_hashes'], 'runtime Python changed'
    assert m.connector_hashes(NAME) == before['connector_hashes'], 'runtime connector changed'
    return live


def build():
    before = load('baseline')
    unchanged(before)
    context = AREA / 'context'
    context.mkdir(mode=0o700)
    for value in before['entries']:
        target = context / value['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        blob = command(['git', '-C', ROOT, 'show', value['revision'] + ':' + value['path']])
        assert digest(blob) == value['sha256']
        target.write_bytes(blob)
        target.chmod(0o644)
    key = digest(before['entries'])[:12]
    image = 'universal-userio-history-repair:' + key
    base_tag = 'userio-history-parent:' + BASE.split(':')[1][:12]
    command(['docker', 'tag', BASE, base_tag])
    assert inspect(base_tag)['Id'] == BASE
    (context / 'Dockerfile').write_text('FROM ' + base_tag + '\n' + ''.join(
        'COPY --chmod=644 ' + v['path'] + ' ' + destination(v['path']) + '\n' for v in before['entries'])
        + 'LABEL io.userio.history-repair.manifest="' + digest(before['entries']) + '"\n')
    command(['docker', 'buildx', 'build', '--builder', 'default', '--load', '--pull=false',
             '--network=none', '-t', image, context], timeout=600)
    result = inspect(image)
    assert result['Config']['Labels']['io.userio.history-repair.manifest'] == digest(before['entries'])
    save('build', {'image': image, 'image_id': result['Id'], 'manifest': digest(before['entries'])})
    print(json.dumps({'built': True, 'image': image, 'image_id': result['Id']}))


class DockerConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect('/var/run/docker.sock')


def activate():
    before, built = load('baseline'), load('build')
    assert not (AREA / 'activation-intent.json').exists(), 'prior attempt exists; reconcile before retry'
    live = unchanged(before)
    assert inspect(built['image'])['Id'] == built['image_id'], 'built tag changed'
    m = helper()
    m.idle()
    assert inspect('userio-frontend')['Id'] == before['frontend_id']
    assert inspect('userio-voice')['Id'] == before['voice_id']
    assert m.unknown_draft_hash(NAME) == before['unknown_draft_hash']
    # SQLite's online backup includes WAL. Keep it outside the live data mount.
    if (AREA / 'backup.json').exists():
        backup = Path(load('backup')['path'])
    elif (AREA / 'candidate.json').exists():
        # Reconcile the successful backup from the pre-mutation metadata check.
        stamp = (AREA / 'candidate.json').stat().st_mtime
        matches = [p for p in Path('/var/backups/universal-userio').glob('history-repair-*/userio.sqlite3')
                   if 0 <= stamp - p.stat().st_mtime <= 300]
        assert len(matches) == 1, 'prior backup identity requires reconciliation'
        backup = matches[0]
    else:
        backup_dir = Path('/var/backups/universal-userio') / ('history-repair-' + str(int(time.time())))
        backup_dir.mkdir(mode=0o700)
        backup = backup_dir / 'userio.sqlite3'
        with sqlite3.connect('file:/var/lib/universal-userio/userio.sqlite3?mode=ro', uri=True) as source:
            with sqlite3.connect(backup) as target:
                backup.chmod(0o600)
                source.backup(target)
    with sqlite3.connect('file:' + str(backup) + '?mode=ro', uri=True) as target:
        assert target.execute('pragma quick_check').fetchone()[0] == 'ok'
        assert target.execute('pragma integrity_check').fetchone()[0] == 'ok'
        rows = target.execute('SELECT message_id,body,direction,received_at FROM messages '
                              'WHERE conversation_id=? ORDER BY received_at,message_id',
                              ('conv_05b6c8996307c403fdb31851',)).fetchall()
        assert {r[0]: digest(r) for r in rows} == before['retained']['rows'], 'backup source rows differ'
    if not (AREA / 'backup.json').exists():
        save('backup', {'path': str(backup), 'quick_check': 'ok', 'integrity_check': 'ok',
                        'retained_rows': len(rows)})
    unchanged(before)
    client = DockerConnection('localhost', timeout=60)
    client.request('GET', '/version')
    response = client.getresponse()
    assert response.status == 200
    api = '/v' + json.loads(response.read())['ApiVersion']
    candidate = 'userio-history-candidate-' + built['manifest'][:12]
    wanted_config = {**live['Config'], 'Image': built['image'],
                     'Labels': {**(live['Config'].get('Labels') or {}),
                                'io.userio.history-repair.manifest': built['manifest']}}
    config = {**wanted_config, 'HostConfig': live['HostConfig']}
    if (AREA / 'candidate.json').exists():
        prior = load('candidate')
        assert prior['name'] == candidate, 'candidate receipt name mismatch'
        created = prior['id']
    else:
        client.request('POST', api + '/containers/create?name=' + candidate,
                       body=json.dumps(config), headers={'Content-Type': 'application/json'})
        response = client.getresponse()
        body = response.read()
        assert response.status == 201, 'candidate create outcome requires exact-name reconciliation'
        created = json.loads(body)['Id']
        save('candidate', {'id': created, 'name': candidate})
    spec = inspect(candidate)
    assert spec['Id'] == created and spec['State']['Status'] == 'created'
    assert spec['Image'] == built['image_id'], 'candidate image mismatch'
    assert host_config(spec) == host_config(live), 'candidate host config mismatch'
    assert spec['Config'] == wanted_config, 'candidate config mismatch'
    original = CONFIG.read_bytes()
    original_processes = PROCESSES.read_bytes()
    modified_processes = process_config(original_processes, before['process_changes'])
    fd = os.open(AREA / 'processes-before.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(original_processes)
        handle.flush()
        os.fsync(handle.fileno())
    previous = 'userio-backend-prev-history-' + str(int(time.time()))
    save('activation-intent', {'previous': previous, 'previous_id': live['Id'],
                             'candidate_id': created, 'backup': str(backup)})
    stopped, renamed, promoted, process_changed = False, False, False, False
    try:
        command(['docker', 'stop', '-t', '40', NAME], timeout=180)
        stopped = True
        if before['process_changes']:
            protected_write(PROCESSES, modified_processes)
            process_changed = True
        command(['docker', 'rename', NAME, previous])
        renamed = True
        command(['docker', 'rename', candidate, NAME])
        promoted = True
        command(['docker', 'start', NAME], timeout=180)
        assert m.health(), 'backend health failed'
        wanted_py, wanted_node = dict(before['python_hashes']), dict(before['connector_hashes'])
        for value in before['entries']:
            path = value['path']
            if path.startswith('src/'):
                wanted_py[path.removeprefix('src/')] = value['sha256']
            else:
                wanted_node[path.rsplit('/',1)[1]] = value['sha256']
        assert m.runtime_hashes(NAME) == wanted_py, 'unexpected Python source delta'
        assert m.connector_hashes(NAME) == wanted_node, 'unexpected connector delta'
        after = inspect(NAME)
        assert host_config(after) == host_config(live), 'installed host config changed'
        assert after['Config'] == spec['Config'], 'installed config changed'
        assert m.unknown_draft_hash(NAME) == before['unknown_draft_hash'], 'UNKNOWN receipts changed'
        assert inspect('userio-frontend')['Id'] == before['frontend_id']
        assert inspect('userio-voice')['Id'] == before['voice_id']
        retained = retained_rows()
        assert all(retained['rows'].get(key) == value for key,value in before['retained']['rows'].items()), 'retained source rows changed'
        assert digest(PROCESSES.read_bytes()) == digest(modified_processes if process_changed else original_processes)
        configured = json.loads(original)
        configured.setdefault('services', {}).setdefault('backend', {})['image'] = built['image']
        temporary = CONFIG.with_name('.compose.history-repair.json')
        temporary.write_text(json.dumps(configured, indent=2))
        temporary.chmod(CONFIG.stat().st_mode & 0o777)
        temporary.replace(CONFIG)
    except BaseException:
        if process_changed:
            protected_write(PROCESSES, original_processes)
        if CONFIG.read_bytes() != original:
            protected_write(CONFIG, original)
        if promoted:
            assert inspect(NAME)['Id'] == created, 'foreign backend replaced candidate; do not remove'
            command(['docker', 'rm', '-f', NAME], timeout=90)
        if renamed:
            command(['docker', 'rename', previous, NAME])
        if stopped:
            command(['docker', 'start', NAME], timeout=180)
            assert m.health(), 'rollback health failed'
        save('rollback', {'restored': inspect(NAME)['Id'] == live['Id']})
        raise
    save('installed', {'image_id': after['Image'], 'container_id': after['Id'],
                       'manifest': built['manifest'], 'previous': previous,
                       'config_preserved': True, 'other_containers_preserved': True})
    print(json.dumps({'installed': True, 'image_id': after['Image'], 'manifest': built['manifest']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['preflight', 'prepare', 'build', 'activate'])
    parser.add_argument('--extra-manifest')
    parser.add_argument('--process-overrides')
    args = parser.parse_args()
    if args.action == 'preflight':
        prepare(None, preflight=True)
    elif args.action == 'prepare':
        prepare(args.extra_manifest, process_overrides=args.process_overrides)
    elif args.action == 'build':
        build()
    else:
        activate()
