"""Fast unit: preserve lease refusal and once-only transcription admission.

Expected2s/emergency20s. No phone, real lease or STT request is performed.
"""
import json
import uuid

import pytest

from universal_userio import container_platform as platform


def test_lease_adapter_restricts_operations_and_preserves_native_arguments():
    args = ['acquire', '--actor-type', 'system', '--owner', 'system:agentcall-tts-google',
            '--purpose', 'Android warm voice synthesis', '--minutes', '3']
    assert platform.lease_arguments(args) == args
    for bad in [['free'], ['release', 'foreign'], ['acquire', '--actor-type', 'agent'],
                args[:-1] + ['240'], args[:4] + ['foreign'] + args[5:]]:
        with pytest.raises(ValueError): platform.lease_arguments(bad)


def test_duplicate_transcription_submission_does_not_reset_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(platform, 'RECORDINGS', tmp_path)
    monkeypatch.setattr(platform, 'JOBS', tmp_path / '.jobs')
    identity = str(uuid.uuid4())
    folder = tmp_path / identity; folder.mkdir(); (folder / 'manifest.json').write_text('{}')
    args = ['start', '--no-block', 'agentcall-transcribe@' + identity + '.service']
    assert platform.submit(args) == 0
    path = platform.JOBS / (identity + '.json')
    record = json.loads(path.read_text()); record['status'] = 'UNKNOWN'
    path.write_text(json.dumps(record))
    assert platform.submit(args) == 0
    assert json.loads(path.read_text())['status'] == 'UNKNOWN'
    with pytest.raises(ValueError): platform.submit(['restart', 'ssh.service'])


def test_native_busy_is_returned_without_changing_or_retrying_lease(tmp_path, monkeypatch):
    monkeypatch.setattr(platform, 'STATE', tmp_path)
    args = ['release', 'a' * 32]
    (tmp_path / 'request.json').write_text(json.dumps(args))
    seen = []
    def run(argv, **kwargs):
        seen.append(argv)
        from subprocess import CompletedProcess
        return CompletedProcess(argv, 75, '{"status":"busy"}', '')
    monkeypatch.setattr(platform.subprocess, 'run', run)
    assert platform.native_lease() == 0
    assert json.loads((tmp_path / 'response.json').read_text())['returncode'] == 75
    assert seen == [['/usr/local/bin/s21-lease', *args]]
