#!/usr/bin/env python3
"""Regression checks for the public VK Inbox extension package."""
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def test_public_archives_never_embed_api_token():
    marker = "REGRESSION_TEST_TOKEN_MUST_NOT_SHIP"
    with tempfile.TemporaryDirectory(prefix="vk-inbox-build-") as temp:
        output = Path(temp)
        env = os.environ.copy()
        env.update(USERIO_STATIC_DIR=str(output), USERIO_API_TOKEN=marker)
        rejected = subprocess.run([str(ROOT / "scripts/build.sh")], cwd=ROOT, env=env, capture_output=True)
        assert rejected.returncode != 0
        env.pop("USERIO_API_TOKEN")
        subprocess.run([str(ROOT / "scripts/build.sh")], cwd=ROOT, env=env, check=True)

        archives = [output / name for name in ("vk-userio-extension.zip", "vk-userio-extension-mv3.zip")]
        for archive_path in archives:
            with zipfile.ZipFile(archive_path) as package:
                contents = {name: package.read(name) for name in package.namelist()}
                assert all(marker.encode() not in data for data in contents.values()), (
                    f"{archive_path.name} contains the supplied token"
                )
                assert b'"token": ""' in contents["lib/config.js"], (
                    f"{archive_path.name} does not use an empty token"
                )
        assert archives[0].read_bytes() == archives[1].read_bytes(), "ZIP aliases differ"


def test_release_archives_match_extension_source():
    """A rebuilt container must not revert the downloadable extension."""
    import json
    static = ROOT.parents[1] / 'src' / 'universal_userio' / 'static'
    manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    artifacts = [static / 'vk-userio-extension.zip', static / 'vk-userio-extension-mv3.zip']
    assert artifacts[0].read_bytes() == artifacts[1].read_bytes()
    for artifact in artifacts:
        with zipfile.ZipFile(artifact) as package:
            shipped = json.loads(package.read('manifest.json'))
            assert shipped['version'] == manifest['version'], 'shipped extension version is stale'
            for name in ('background.js', 'popup.js', 'popup.html', 'options.js', 'options.html',
                         'lib/oauth.js', 'lib/update.js', 'lib/chatgpt.js'):
                assert package.read(name) == (ROOT / name).read_bytes(), f'stale release asset: {name}'
