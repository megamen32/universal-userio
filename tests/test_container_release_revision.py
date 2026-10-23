"""A revision label is intentional; secret runtime settings must stay exact."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat


def test_reviewed_revision_label_does_not_fail_an_otherwise_identical_cutover():
    path = Path(__file__).resolve().parents[1] / 'scripts/container_release.py'
    spec = importlib.util.spec_from_file_location('revision_release', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert 'src/universal_userio/matrix_calls.py' in module.BACKEND_SOURCE_HASH_PATHS
    previous = {'Image': 'old', 'Labels': {}, 'Env': ['SENTINEL=preserve'],
                'Cmd': ['original-worker'], 'WorkingDir': '/opt/application'}
    frozen = copy.deepcopy(previous)
    expected = module._backend_replacement_config(previous, image='new', revision='reviewed-pin')
    observed = dict(previous, Image='new', Labels={'org.opencontainers.image.revision': 'reviewed-pin'})
    assert observed == expected
    assert previous == frozen
    assert dict(observed, Env=['SENTINEL=changed']) != expected
    assert dict(observed, Cmd=['different-worker']) != expected
    previous['Labels'] = {'owner': 'preserve', 'org.opencontainers.image.revision': 'old-pin'}
    revised = module._backend_replacement_config(previous, image='new', revision='reviewed-pin')
    assert revised['Labels'] == {'owner': 'preserve', 'org.opencontainers.image.revision': 'reviewed-pin'}


def _load_release_module():
    path = Path(__file__).resolve().parents[1] / 'scripts/container_release.py'
    spec = importlib.util.spec_from_file_location('archive_voice_release', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _voice_archive_fixture(tmp_path, monkeypatch):
    module = _load_release_module()
    archive_root = tmp_path / 'backups' / 'universal-userio'
    archive_root.mkdir(parents=True)
    config_root = tmp_path / 'etc' / 'userio-containers'
    config_root.mkdir(parents=True)
    for name in module.VOICE_CONFIG_FILES:
        (config_root / name).write_text(json.dumps({'mode': 'host-replaced', 'api_key': 'host-secret'}))
    secretary_config = tmp_path / 'etc' / 'agentcall-secretary-voice.json'
    secretary_config.write_text(json.dumps({'model': 'host-replaced', 'token': 'host-secret'}))
    identity_map = tmp_path / 'etc' / 'agentcall-secretary-identities.json'
    identity_map.write_text(json.dumps({'matrix:test': 'host-replaced'}))
    bridge_root = tmp_path / 'usr' / 'local' / 'lib' / 'agentcall'
    bridge_root.mkdir(parents=True)
    (bridge_root / 'agentcall_realtime_bridge.py').symlink_to(
        '/opt/universal-userio/src/universal_userio/voice/agentcall_realtime_bridge.py')
    sdk_root = tmp_path / 'opt' / 'exmanager-voice' / '1db611b'
    sdk_file = sdk_root / 'integrations' / 'agentcall' / 'agentcall_secretary_voice.py'
    sdk_file.parent.mkdir(parents=True)
    sdk_file.write_text('host replacement SDK\n')
    config_mount = {'Source': str(config_root), 'Destination': '/etc/userio-containers'}
    mounts = [
        config_mount,
        {'Source': str(bridge_root), 'Destination': '/usr/local/lib/agentcall'},
        {'Source': str(sdk_root), 'Destination': '/opt/exmanager-voice/b57ae06'},
        {'Source': str(secretary_config), 'Destination': '/etc/agentcall-secretary-voice.json'},
        {'Source': str(identity_map), 'Destination': '/etc/agentcall-secretary-identities.json'},
    ]
    proc_root = tmp_path / 'proc'
    container_root = tmp_path / 'consumer-root'
    proc_pid_root = proc_root / '321'
    proc_pid_root.mkdir(parents=True)
    container_root.mkdir()
    (proc_pid_root / 'root').symlink_to(container_root, target_is_directory=True)
    consumer_root = proc_pid_root / 'root'
    for mount in mounts:
        destination = Path(mount['Destination'])
        mounted_path = consumer_root / str(destination).lstrip('/')
        source_path = Path(mount['Source'])
        if source_path.is_dir():
            mounted_path.mkdir(parents=True, exist_ok=True)
            for child in source_path.rglob('*'):
                relative = child.relative_to(source_path)
                target = mounted_path / relative
                if child.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                elif child.is_symlink():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.symlink_to(os.readlink(child))
                elif child.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(child.read_bytes())
        else:
            mounted_path.parent.mkdir(parents=True, exist_ok=True)
            mounted_path.write_bytes(source_path.read_bytes())
    for name in module.VOICE_CONFIG_FILES:
        (consumer_root / 'etc' / 'userio-containers' / name).write_text(
            json.dumps({'mode': 'mounted-old-inode', 'api_key': 'mounted-secret'}))
    (consumer_root / 'etc' / 'agentcall-secretary-voice.json').write_text(
        json.dumps({'model': 'mounted-old-inode', 'token': 'mounted-secret'}))
    (consumer_root / 'etc' / 'agentcall-secretary-identities.json').write_text(
        json.dumps({'matrix:test': 'mounted-old-inode'}))
    bridge_link = consumer_root / 'usr' / 'local' / 'lib' / 'agentcall' / 'agentcall_realtime_bridge.py'
    bridge_link.unlink()
    bridge_target_path = '/opt/universal-userio/src/universal_userio/voice/agentcall_realtime_bridge.py'
    bridge_link.symlink_to(bridge_target_path)
    mounted_bridge_target = consumer_root / bridge_target_path.lstrip('/')
    mounted_bridge_target.parent.mkdir(parents=True, exist_ok=True)
    mounted_bridge_target.write_text('mounted old bridge target inode\n')
    mounted_sdk_file = consumer_root / 'opt' / 'exmanager-voice' / 'b57ae06' / 'integrations' / 'agentcall' / 'agentcall_secretary_voice.py'
    mounted_sdk_file.parent.mkdir(parents=True, exist_ok=True)
    mounted_sdk_file.write_text('mounted old SDK inode\n')
    current = {
        'Id': 'container-live-id', 'Name': '/userio-voice', 'Image': 'sha256:current-image',
        'State': {'Running': True, 'Status': 'running', 'Pid': 321},
        'Config': {'Image': 'ghcr.io/megamen32/universal-userio-voice:sha-f199406',
                   'Env': ['SAFE_MODE=1', 'OMNIROUTE_API_KEY=private-sentinel'],
                   'Labels': {'org.opencontainers.image.revision': 'f199406f8b43dcfd1a2797d04d235bee6b466f66'}},
        'HostConfig': {'NetworkMode': 'host', 'RestartPolicy': {'Name': 'unless-stopped'}},
        'Mounts': mounts, 'NetworkSettings': {'Networks': {'host': {}}},
    }
    old = {
        'Id': 'container-rollback-id', 'Name': '/userio-voice-prev-speech-test', 'Image': 'sha256:old-image',
        'State': {'Running': False, 'Status': 'exited'},
        'Config': {'Image': 'ghcr.io/megamen32/universal-userio-voice@sha256:old'},
        'HostConfig': {'NetworkMode': 'host'}, 'Mounts': [],
    }
    images = {
        'sha256:current-image': {'Id': 'sha256:current-image', 'RepoDigests':
            ['ghcr.io/megamen32/universal-userio-voice@sha256:current'],
            'Config': {'Labels': {'org.opencontainers.image.revision': 'f199406f8b43dcfd1a2797d04d235bee6b466f66'}}},
        'sha256:old-image': {'Id': 'sha256:old-image', 'RepoDigests':
            ['ghcr.io/megamen32/universal-userio-voice@sha256:old'], 'Config': {'Labels': {}}},
    }
    calls = []

    def fake_docker(args, description):
        calls.append(tuple(args))
        if args[0] == 'image':
            image = images[args[-1]]
            return json.dumps([image]).encode(), [image]
        identifier = args[-1]
        row = current if identifier == module.VOICE_CONTAINER else old
        return json.dumps([row]).encode(), [row]

    def fake_docker_text(args, description):
        calls.append(tuple(args))
        assert args[0] == 'ps'
        return b'container-rollback-id\n'

    monkeypatch.setattr(module, '_archive_docker_json', fake_docker)
    monkeypatch.setattr(module, '_archive_docker_text', fake_docker_text)
    monkeypatch.setattr(module, 'VOICE_ARCHIVE_ROOT', archive_root)
    monkeypatch.setattr(module, 'VOICE_PROC_ROOT', proc_root, raising=False)
    monkeypatch.setattr(module, '_archive_process_identity', lambda row: {
        'pid': row['State']['Pid'], 'start_time': 10, 'mount_namespace_inode': 99}, raising=False)
    lock_path = tmp_path / 'run' / 'lock' / 'voice.lock'
    lock_path.parent.mkdir(parents=True)
    monkeypatch.setattr(module, 'VOICE_ARCHIVE_LOCK', lock_path)
    monkeypatch.setattr(module, 'CONFIG', config_root)
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(module.os, 'fchown', lambda *_: None)
    monkeypatch.setattr(module.os, 'chown', lambda *_: None)
    monkeypatch.setattr(module, '_archive_root_is_safe', lambda _: True)
    monkeypatch.setattr(module, '_archive_has_owner_mode',
                        lambda path, mode: stat.S_IMODE(path.stat().st_mode) == mode)
    return module, archive_root, current, old, images, calls, consumer_root


def test_archive_voice_writes_secret_bearing_snapshot_root_only_without_runtime_mutation(tmp_path, monkeypatch):
    module, archive_root, _, _, _, calls, consumer_root = _voice_archive_fixture(tmp_path, monkeypatch)
    target = archive_root / 'voice-20261023-test'

    receipt = module.archive_voice(target)

    assert receipt['status'] == 'archived_only'
    assert receipt['no_runtime_changes'] is True
    assert receipt['image_digest'].endswith('@sha256:current')
    assert receipt['source_revision'] == 'f199406f8b43dcfd1a2797d04d235bee6b466f66'
    assert receipt['rollback_container_ids'] == ['container-rollback-id']
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in target.rglob('*') if path.is_file())
    manifest_bytes = (target / 'manifest.json').read_bytes()
    assert b'private-sentinel' not in manifest_bytes
    assert b'private-sentinel' in (target / 'container-inspect.json').read_bytes()
    archived_voice_config = (target / 'config' / 'userio-containers--voice.json').read_bytes()
    assert archived_voice_config == (consumer_root / 'etc' / 'userio-containers' / 'voice.json').read_bytes()
    assert b'mounted-old-inode' in archived_voice_config
    manifest = json.loads(manifest_bytes)
    expected_bridge = consumer_root / 'opt' / 'universal-userio' / 'src' / 'universal_userio' / 'voice' / 'agentcall_realtime_bridge.py'
    expected_sdk = consumer_root / 'opt' / 'exmanager-voice' / 'b57ae06' / 'integrations' / 'agentcall' / 'agentcall_secretary_voice.py'
    assert manifest['bridge']['sha256'] == hashlib.sha256(expected_bridge.read_bytes()).hexdigest()
    assert manifest['secretary_sdk']['entrypoint_sha256'] == hashlib.sha256(expected_sdk.read_bytes()).hexdigest()
    assert manifest['bridge']['symlinks'] == [{'path': '/usr/local/lib/agentcall/agentcall_realtime_bridge.py',
                                                'target': '/opt/universal-userio/src/universal_userio/voice/agentcall_realtime_bridge.py'}]
    host_sdk = tmp_path / 'opt' / 'exmanager-voice' / '1db611b' / 'integrations' / 'agentcall' / 'agentcall_secretary_voice.py'
    assert manifest['secretary_sdk']['entrypoint_sha256'] != hashlib.sha256(host_sdk.read_bytes()).hexdigest()
    for row in (target / 'SHA256SUMS').read_text().splitlines():
        digest, name = row.split('  ', 1)
        assert hashlib.sha256((target / name).read_bytes()).hexdigest() == digest
    assert all(args[0] in {'inspect', 'image', 'ps'} for args in calls)


def test_archive_voice_keeps_container_symlink_target_when_host_mount_entry_is_atomically_replaced(tmp_path, monkeypatch):
    module, archive_root, _, _, _, _, consumer_root = _voice_archive_fixture(tmp_path, monkeypatch)
    host_entry = tmp_path / 'usr' / 'local' / 'lib' / 'agentcall' / 'agentcall_realtime_bridge.py'
    replacement = host_entry.with_name('bridge.new')
    replacement.write_text('host atomically replaced bridge\n')
    replacement.replace(host_entry)

    target = archive_root / 'voice-container-symlink-view'
    receipt = module.archive_voice(target)
    manifest = json.loads((target / 'manifest.json').read_bytes())
    consumer_target = consumer_root / 'opt' / 'universal-userio' / 'src' / 'universal_userio' / 'voice' / 'agentcall_realtime_bridge.py'

    assert manifest['bridge']['sha256'] == hashlib.sha256(consumer_target.read_bytes()).hexdigest()
    assert manifest['bridge']['sha256'] != hashlib.sha256(host_entry.read_bytes()).hexdigest()
    assert manifest['bridge']['resolved_container_path'] == '/opt/universal-userio/src/universal_userio/voice/agentcall_realtime_bridge.py'
    assert manifest['bridge']['symlinks'][0]['target'] == manifest['bridge']['resolved_container_path']
    assert receipt['bridge_sha256'] == manifest['bridge']['sha256']


def test_archive_container_reader_rejects_symlink_escape_and_broken_target(tmp_path, monkeypatch):
    module, _, _, _, _, _, consumer_root = _voice_archive_fixture(tmp_path, monkeypatch)
    bridge = consumer_root / 'usr' / 'local' / 'lib' / 'agentcall' / 'agentcall_realtime_bridge.py'
    identity = {'pid': 321}

    bridge.unlink()
    bridge.symlink_to('/../../etc/passwd')
    try:
        module._archive_read_container_file(identity, '/usr/local/lib/agentcall/agentcall_realtime_bridge.py')
    except RuntimeError as error:
        assert str(error) == 'container symlink escapes its root'
    else:
        raise AssertionError('symlink with parent traversal must be rejected')

    bridge.unlink()
    bridge.symlink_to('/opt/universal-userio/missing/bridge.py')
    try:
        module._archive_read_container_file(identity, '/usr/local/lib/agentcall/agentcall_realtime_bridge.py')
    except RuntimeError as error:
        assert str(error) == 'container mounted file is unavailable'
    else:
        raise AssertionError('broken symlink must be rejected')


def test_archive_voice_rejects_bridge_symlink_mutated_during_capture(tmp_path, monkeypatch):
    module, archive_root, _, _, _, _, consumer_root = _voice_archive_fixture(tmp_path, monkeypatch)
    bridge = consumer_root / 'usr' / 'local' / 'lib' / 'agentcall' / 'agentcall_realtime_bridge.py'
    alternate = consumer_root / 'opt' / 'universal-userio' / 'alternate' / 'bridge.py'
    alternate.parent.mkdir(parents=True)
    alternate.write_text('different live target\n')
    original = module._archive_docker_json
    reads = 0

    def mutate_link_before_final_inspect(args, description):
        nonlocal reads
        if args[0] == 'inspect' and args[-1] == module.VOICE_CONTAINER:
            reads += 1
            if reads == 2:
                bridge.unlink()
                bridge.symlink_to('/opt/universal-userio/alternate/bridge.py')
        return original(args, description)

    monkeypatch.setattr(module, '_archive_docker_json', mutate_link_before_final_inspect)
    target = archive_root / 'voice-symlink-drift'
    try:
        module.archive_voice(target)
    except RuntimeError as error:
        assert str(error) == 'mounted AgentCall bridge changed during archive capture'
    else:
        raise AssertionError('archive must reject a changed bridge symlink/target')
    assert not target.exists()
    assert not list(archive_root.glob('.voice-archive-*'))


def test_archive_voice_fails_closed_without_immutable_image_digest(tmp_path, monkeypatch):
    module, archive_root, _, _, images, _, _ = _voice_archive_fixture(tmp_path, monkeypatch)
    images['sha256:current-image']['RepoDigests'] = []

    try:
        module.archive_voice(archive_root / 'voice-no-digest')
    except RuntimeError as error:
        assert str(error) == 'live voice image has no immutable registry digest'
    else:
        raise AssertionError('archive should reject a mutable/unpublished image')
    assert not (archive_root / 'voice-no-digest').exists()
    assert not list(archive_root.glob('.voice-archive-*'))


def test_archive_voice_fails_closed_when_running_container_is_missing(tmp_path, monkeypatch):
    module, archive_root, current, _, _, _, _ = _voice_archive_fixture(tmp_path, monkeypatch)
    current['State']['Running'] = False

    try:
        module.archive_voice(archive_root / 'voice-not-running')
    except RuntimeError as error:
        assert str(error) == 'canonical userio-voice container is not running'
    else:
        raise AssertionError('archive should reject a stopped runtime')
    assert not (archive_root / 'voice-not-running').exists()


def test_archive_voice_fails_closed_without_rollback_container(tmp_path, monkeypatch):
    module, archive_root, _, _, _, _, _ = _voice_archive_fixture(tmp_path, monkeypatch)
    original = module._archive_docker_text

    def without_rollback(args, description):
        if args[0] == 'ps':
            return b''
        return original(args, description)

    monkeypatch.setattr(module, '_archive_docker_text', without_rollback)
    try:
        module.archive_voice(archive_root / 'voice-no-rollback')
    except RuntimeError as error:
        assert str(error) == 'no retained previous userio-voice container is available for rollback'
    else:
        raise AssertionError('archive should require a retained rollback container')
    assert not (archive_root / 'voice-no-rollback').exists()


def test_archive_voice_rejects_non_root_before_reading_runtime(tmp_path, monkeypatch):
    module, archive_root, _, _, _, calls, _ = _voice_archive_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(module.os, 'geteuid', lambda: 1000)
    try:
        module.archive_voice(archive_root / 'voice-unprivileged')
    except RuntimeError as error:
        assert str(error) == 'archive-voice requires root'
    else:
        raise AssertionError('archive should require effective root')
    assert calls == []
    assert not (archive_root / 'voice-unprivileged').exists()


def test_archive_voice_owner_mode_guard_requires_root_owned_exact_modes():
    module = _load_release_module()

    class FakeStatPath:
        def __init__(self, uid, gid, mode):
            self.value = type('Stat', (), {'st_uid': uid, 'st_gid': gid, 'st_mode': stat.S_IFREG | mode})()

        def stat(self):
            return self.value

    assert module._archive_has_owner_mode(FakeStatPath(0, 0, 0o600), 0o600)
    assert module._archive_has_owner_mode(FakeStatPath(0, 0, stat.S_IFDIR | 0o700), 0o700)
    assert not module._archive_has_owner_mode(FakeStatPath(1000, 0, 0o600), 0o600)
    assert not module._archive_has_owner_mode(FakeStatPath(0, 1000, 0o600), 0o600)
    assert not module._archive_has_owner_mode(FakeStatPath(0, 0, 0o644), 0o600)
    assert not module._archive_has_owner_mode(FakeStatPath(0, 0, stat.S_IFDIR | 0o755), 0o700)


def test_archive_voice_does_not_touch_an_existing_destination(tmp_path, monkeypatch):
    module, archive_root, _, _, _, calls, _ = _voice_archive_fixture(tmp_path, monkeypatch)
    target = archive_root / 'voice-existing'
    target.mkdir(mode=0o700)
    sentinel = target / 'owner-file'
    sentinel.write_text('preserve')

    try:
        module.archive_voice(target)
    except RuntimeError as error:
        assert str(error) == 'archive directory already exists; reconcile before retrying'
    else:
        raise AssertionError('archive must not overwrite an existing destination')
    assert sentinel.read_text() == 'preserve'
    assert calls == []


def test_archive_voice_atomic_publish_does_not_replace_racing_destination(tmp_path, monkeypatch):
    module, archive_root, _, _, _, _, _ = _voice_archive_fixture(tmp_path, monkeypatch)
    target = archive_root / 'voice-race'
    original = module._archive_rename_noreplace

    def create_competing_target(source, destination):
        destination.mkdir(mode=0o700)
        (destination / 'owner-file').write_text('preserve')
        return original(source, destination)

    monkeypatch.setattr(module, '_archive_rename_noreplace', create_competing_target)
    try:
        module.archive_voice(target)
    except RuntimeError as error:
        assert str(error) == 'archive destination already exists; reconcile before retrying'
    else:
        raise AssertionError('atomic publication must not overwrite a racing destination')
    assert (target / 'owner-file').read_text() == 'preserve'
    assert not list(archive_root.glob('.voice-archive-*'))


def test_archive_voice_fails_closed_on_snapshot_readback_checksum_mismatch(tmp_path, monkeypatch):
    module, archive_root, _, _, _, _, _ = _voice_archive_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(module, '_archive_verify_file', lambda *_: False)

    try:
        module.archive_voice(archive_root / 'voice-bad-readback')
    except RuntimeError as error:
        assert str(error) == 'archive snapshot readback hash mismatch'
    else:
        raise AssertionError('archive must reject a checksum mismatch')
    assert not (archive_root / 'voice-bad-readback').exists()
    assert not list(archive_root.glob('.voice-archive-*'))


def test_archive_voice_rejects_running_rollback_container(tmp_path, monkeypatch):
    module, archive_root, _, old, _, _, _ = _voice_archive_fixture(tmp_path, monkeypatch)
    old['State']['Running'] = True

    try:
        module.archive_voice(archive_root / 'voice-running-rollback')
    except RuntimeError as error:
        assert str(error) == 'previous userio-voice rollback container is unexpectedly running'
    else:
        raise AssertionError('archive must reject an active rollback container')
    assert not (archive_root / 'voice-running-rollback').exists()


def test_archive_voice_rejects_container_process_identity_drift(tmp_path, monkeypatch):
    module, archive_root, current, _, _, _, _ = _voice_archive_fixture(tmp_path, monkeypatch)
    original = module._archive_docker_json
    reads = 0

    def restart_before_final_inspect(args, description):
        nonlocal reads
        if args[0] == 'inspect' and args[-1] == module.VOICE_CONTAINER:
            reads += 1
            if reads == 2:
                current['State']['Pid'] = 322
        return original(args, description)

    monkeypatch.setattr(module, '_archive_docker_json', restart_before_final_inspect)
    try:
        module.archive_voice(archive_root / 'voice-process-drift')
    except RuntimeError as error:
        assert str(error) == 'live Voice container changed during archive capture'
    else:
        raise AssertionError('archive must reject a changed container init process')
    assert not (archive_root / 'voice-process-drift').exists()
    assert not list(archive_root.glob('.voice-archive-*'))


def test_archive_voice_rejects_mounted_config_drift_during_capture(tmp_path, monkeypatch):
    module, archive_root, _, _, _, _, consumer_root = _voice_archive_fixture(tmp_path, monkeypatch)
    original = module._archive_docker_json
    reads = 0

    def replace_consumer_config_before_final_inspect(args, description):
        nonlocal reads
        if args[0] == 'inspect' and args[-1] == module.VOICE_CONTAINER:
            reads += 1
            if reads == 2:
                (consumer_root / 'etc' / 'userio-containers' / 'voice.json').write_text(
                    json.dumps({'mode': 'changed-mid-snapshot'}))
        return original(args, description)

    monkeypatch.setattr(module, '_archive_docker_json', replace_consumer_config_before_final_inspect)
    try:
        module.archive_voice(archive_root / 'voice-config-drift')
    except RuntimeError as error:
        assert str(error) == 'mounted Voice config changed during archive capture'
    else:
        raise AssertionError('archive must reject a changing mounted config')
    assert not (archive_root / 'voice-config-drift').exists()
    assert not list(archive_root.glob('.voice-archive-*'))
