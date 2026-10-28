"""One-file mirror alias overlay; immutable reviewed release engine, new receipts."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import types

ROOT = Path(__file__).resolve().parents[3]
ENGINE_REVISION = '8bd382c334f9b06e3da2c0b05355324a9ebff7fd'
ENGINE_PATH = '.agents/tasks/20261028-history-repair/release.py'
ENGINE_SHA256 = '30cbd148b29a40a94fe46ac912c3ead63c2e78987c549ae3260e61ac52ba6a66'


def engine():
    blob = subprocess.check_output(['git', '-C', str(ROOT), 'show', ENGINE_REVISION + ':' + ENGINE_PATH])
    assert hashlib.sha256(blob).hexdigest() == ENGINE_SHA256, 'release engine hash mismatch'
    module = types.ModuleType('reviewed_fresh_history_release')
    module.__file__ = str(ROOT / ENGINE_PATH)
    exec(compile(blob, module.__file__, 'exec'), module.__dict__)
    module.AREA = Path(__file__).resolve().parent / 'private'
    module.BASE = 'sha256:fa31b8129a83b1b1eadbf31655fe21beb17e3e8f5275e0c14ec1797c95726c17'
    module.PIN = {'revision': '6718f8688b8dece758819c2885bacf694170adab',
                  'path': 'src/universal_userio/messenger_telegram_mirror.py',
                  'sha256': 'b91f09aad45dbfaaa3bf45789118f925ce1665ffe8bd6e0dfe80c1e7e92c439a'}
    return module


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'build', 'activate'])
    args = parser.parse_args()
    release = engine()
    if args.action == 'prepare':
        release.prepare(None)
    elif args.action == 'build':
        release.build()
    else:
        release.activate()
