"""A revision label is intentional; secret runtime settings must stay exact."""
import copy
import hashlib
import importlib.util
import json
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
        (config_root / name).write_text(json.dumps({'mode': 'safe', 'api_key': 'private-sentinel'}))
    secretary_config = tmp_path / 'etc' / 'agentcall-secretary-voice.json'
    secretary_config.write_text(json.dumps({'model': 'qa-model', 'token': 'private-sentinel'}))
    identity_map = tmp_path / 'etc' / 'agentcall-secretary-identities.json'
    identity_map.write_text(json.dumps({'matrix:test': 'qa-user'}))
    bridge_root = tmp_path / 'usr' / 'local' / 'lib' / 'agentcall'
    bridge_root.mkdir(parents=True)
    (bridge_root / 'agentcall_realtime_bridge.py').write_text('bridge marker\n')
    sdk_root = tmp_path / 'opt' / 'exmanager-voice' / '1db611b'
    sdk_file = sdk_root / 'integrations' / 'agentcall' / 'agentcall_secretary_voice.py'
    sdk_file.parent.mkdir(parents=True)
    sdk_file.write_text('sdk marker\n')
    config_mount = {'Source': str(config_root), 'Destination': str(config_root)}
    mounts = [
        config_mount,
        {'Source': str(bridge_root), 'Destination': '/usr/local/lib/agentcall'},
        {'Source': str(sdk_root), 'Destination': '/opt/exmanager-voice/b57ae06'},
        {'Source': str(secretary_config), 'Destination': '/etc/agentcall-secretary-voice.json'},
        {'Source': str(identity_map), 'Destination': '/etc/agentcall-secretary-identities.json'},
    ]
    current = {
        'Id': 'container-live-id', 'Name': '/userio-voice', 'Image': 'sha256:current-image',
        'State': {'Running': True, 'Status': 'running'},
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
        if args[0] == 'ps':
            return b'container-rollback-id\n', ['container-rollback-id']
        if args[0] == 'image':
            image = images[args[-1]]
            return json.dumps([image]).encode(), [image]
        identifier = args[-1]
        row = current if identifier == module.VOICE_CONTAINER else old
        return json.dumps([row]).encode(), [row]

    monkeypatch.setattr(module, '_archive_docker_json', fake_docker)
    monkeypatch.setattr(module, 'VOICE_ARCHIVE_ROOT', archive_root)
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
    return module, archive_root, current, images, calls


def test_archive_voice_writes_secret_bearing_snapshot_root_only_without_runtime_mutation(tmp_path, monkeypatch):
    module, archive_root, _, _, calls = _voice_archive_fixture(tmp_path, monkeypatch)
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
    assert b'private-sentinel' in (target / 'config' / 'userio-containers--voice.json').read_bytes()
    for row in (target / 'SHA256SUMS').read_text().splitlines():
        digest, name = row.split('  ', 1)
        assert hashlib.sha256((target / name).read_bytes()).hexdigest() == digest
    assert all(args[0] in {'inspect', 'image', 'ps'} for args in calls)


def test_archive_voice_fails_closed_without_immutable_image_digest(tmp_path, monkeypatch):
    module, archive_root, _, images, _ = _voice_archive_fixture(tmp_path, monkeypatch)
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
    module, archive_root, current, _, _ = _voice_archive_fixture(tmp_path, monkeypatch)
    current['State']['Running'] = False

    try:
        module.archive_voice(archive_root / 'voice-not-running')
    except RuntimeError as error:
        assert str(error) == 'canonical userio-voice container is not running'
    else:
        raise AssertionError('archive should reject a stopped runtime')
    assert not (archive_root / 'voice-not-running').exists()


def test_archive_voice_fails_closed_without_rollback_container(tmp_path, monkeypatch):
    module, archive_root, _, _, _ = _voice_archive_fixture(tmp_path, monkeypatch)
    original = module._archive_docker_json

    def without_rollback(args, description):
        if args[0] == 'ps':
            return b'', []
        return original(args, description)

    monkeypatch.setattr(module, '_archive_docker_json', without_rollback)
    try:
        module.archive_voice(archive_root / 'voice-no-rollback')
    except RuntimeError as error:
        assert str(error) == 'no retained previous userio-voice container is available for rollback'
    else:
        raise AssertionError('archive should require a retained rollback container')
    assert not (archive_root / 'voice-no-rollback').exists()


def test_archive_voice_rejects_non_root_before_reading_runtime(tmp_path, monkeypatch):
    module, archive_root, _, _, calls = _voice_archive_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(module.os, 'geteuid', lambda: 1000)
    try:
        module.archive_voice(archive_root / 'voice-unprivileged')
    except RuntimeError as error:
        assert str(error) == 'archive-voice requires root'
    else:
        raise AssertionError('archive should require effective root')
    assert calls == []
    assert not (archive_root / 'voice-unprivileged').exists()
