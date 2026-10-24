"""An explicit published revision must build the release context from Git objects.

The release helper used to require the canonical checkout to be exactly the
published main commit, so a checkout that is behind or carries unrelated work
could not produce a safe release context. An explicit immutable revision must
package the published commit itself: the archive bytes, the release manifest
and the stamped release identity all come from that committed tree, and the
published tip may not move while the context is generated.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATHS = (
    'deploy/universal-userio.service',
    'deploy/userio-agentcall-ingress.service',
    'deploy/userio-gmail-ingress.service',
    'deploy/userio-matrix-ingress.service',
    'deploy/userio-max-ingress.service',
    'deploy/userio-sms-ingress.service',
)


def _release_module():
    path = PROJECT_ROOT / 'scripts/container_release.py'
    spec = importlib.util.spec_from_file_location('source_revision_release', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repository, *args):
    return subprocess.run(
        ['git', '-C', str(repository), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


def _publish(repository, message):
    _git(repository, 'add', '-A')
    _git(repository, 'commit', '-qm', message)
    _git(repository, 'push', '-q', 'origin', 'main')
    _git(repository, 'fetch', '-q', 'origin')


def _published_repository(tmp_path):
    """One published commit on main, pushed to a bare origin."""
    repository = tmp_path / 'repo'
    repository.mkdir()
    _git(repository, 'init', '-q', '-b', 'main')
    _git(repository, 'config', 'user.email', 'release@example.invalid')
    _git(repository, 'config', 'user.name', 'Release Fixture')
    (repository / 'pyproject.toml').write_text('[project]\nname = "userio"\n')
    package = repository / 'src/universal_userio'
    package.mkdir(parents=True)
    (package / 'service.py').write_text('COMMITTED = True\n')
    for relative in SERVICE_PATHS:
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('[Unit]\n')
    remote = tmp_path / 'origin.git'
    subprocess.run(['git', 'init', '-q', '--bare', str(remote)], check=True)
    _git(repository, 'remote', 'add', 'origin', str(remote))
    _publish(repository, 'published source')
    return repository


def _release_helper(tmp_path, monkeypatch, repository):
    """The release helper bound to a temporary published repository."""
    module = _release_module()
    monkeypatch.setattr(module, 'ROOT', repository)
    monkeypatch.setattr(module, '_dispatcher_input', lambda: (b'dispatcher\n', 'dispatcher-sha'))
    monkeypatch.setattr(module, '_host_additions', lambda: {})
    scripts = str(PROJECT_ROOT / 'scripts')
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return module


def test_an_explicit_published_revision_builds_from_the_committed_tree(tmp_path, monkeypatch):
    repository = _published_repository(tmp_path)
    module = _release_helper(tmp_path, monkeypatch, repository)
    revision = _git(repository, 'rev-parse', 'origin/main')
    # Unrelated work in the checkout must not reach the release context.
    (repository / 'src/universal_userio/service.py').write_text('UNCOMMITTED = True\n')
    destination = tmp_path / 'context.tar'

    module.build_context(destination, revision=revision)

    with tarfile.open(destination) as archive:
        names = archive.getnames()
        body = archive.extractfile('src/universal_userio/service.py').read().decode()
        identity = json.loads(archive.extractfile('deploy/containers/release.json').read())
    assert 'src/universal_userio/service.py' in names
    assert 'COMMITTED' in body and 'UNCOMMITTED' not in body
    assert identity['commit'] == revision


def test_a_stale_or_non_tip_revision_is_refused(tmp_path, monkeypatch):
    repository = _published_repository(tmp_path)
    module = _release_helper(tmp_path, monkeypatch, repository)
    stale = _git(repository, 'rev-parse', 'origin/main')
    (repository / 'pyproject.toml').write_text('[project]\nname = "userio-2"\n')
    _publish(repository, 'newer published source')
    assert _git(repository, 'rev-parse', 'origin/main') != stale

    with pytest.raises(RuntimeError):
        module.build_context(tmp_path / 'context.tar', revision=stale)


def test_a_symbolic_or_non_commit_revision_is_refused(tmp_path, monkeypatch):
    repository = _published_repository(tmp_path)
    module = _release_helper(tmp_path, monkeypatch, repository)
    tree = _git(repository, 'rev-parse', 'origin/main^{tree}')

    with pytest.raises(RuntimeError):
        module.build_context(tmp_path / 'context.tar', revision='main')
    with pytest.raises(RuntimeError):
        module.build_context(tmp_path / 'context.tar', revision=tree)


def test_a_moving_origin_main_is_refused_before_the_context_is_written(tmp_path, monkeypatch):
    repository = _published_repository(tmp_path)
    module = _release_helper(tmp_path, monkeypatch, repository)
    revision = _git(repository, 'rev-parse', 'origin/main')

    def advance_origin_main():
        # The published tip moves while the context is being generated.
        (repository / 'pyproject.toml').write_text('[project]\nname = "userio-moved"\n')
        _publish(repository, 'published while building')
        return b'dispatcher\n', 'dispatcher-sha'

    monkeypatch.setattr(module, '_dispatcher_input', advance_origin_main)
    destination = tmp_path / 'context.tar'

    with pytest.raises(RuntimeError):
        module.build_context(destination, revision=revision)
    assert not destination.exists()


def test_the_default_context_still_requires_the_published_checkout(tmp_path, monkeypatch):
    repository = _published_repository(tmp_path)
    module = _release_helper(tmp_path, monkeypatch, repository)
    destination = tmp_path / 'context.tar'

    # The checkout itself is the published commit: the default path is unchanged.
    module.build_context(destination)
    assert destination.exists()
    with tarfile.open(destination) as archive:
        identity = json.loads(archive.extractfile('deploy/containers/release.json').read())
    assert identity['commit'] == _git(repository, 'rev-parse', 'origin/main')

    # A checkout that is behind the published tip stays refused.
    (repository / 'pyproject.toml').write_text('[project]\nname = "userio-later"\n')
    _git(repository, 'add', '-A')
    _git(repository, 'commit', '-qm', 'unpublished local commit')
    with pytest.raises(RuntimeError):
        module.build_context(tmp_path / 'later.tar')
