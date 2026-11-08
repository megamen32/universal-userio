"""Portable recordings use the existing durable transcription queue."""
import importlib
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/universal_userio/voice'))
recording = importlib.import_module('agentcall_recording')
transcribe = importlib.import_module('agentcall_recording_transcribe')


def test_portable_submission_does_not_need_systemd(tmp_path, monkeypatch):
    from universal_userio import container_platform
    rec = recording.CallRecorder(str(uuid.uuid4()), 'incoming', {'peerRef':'fixture'}, tmp_path)
    rec.incoming(b'\x01\x01' * 160)
    manifest = rec.finish()
    monkeypatch.setenv('AGENTCALL_RECORDING_DIRECTORY', str(tmp_path))
    monkeypatch.setenv('USERIO_VOICE_ROLE', 'calls-primary')
    monkeypatch.setattr(container_platform, 'RECORDINGS', tmp_path)
    monkeypatch.setattr(container_platform, 'JOBS', tmp_path / '.container-jobs')
    monkeypatch.setattr(transcribe.subprocess, 'run', lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError('systemctl')))
    receipt = transcribe.submit_transcription(manifest)
    assert receipt['status'] == 'QUEUED'
    queued = container_platform.JOBS / (rec.call_id + '.json')
    assert json.loads(queued.read_text())['status'] == 'QUEUED'
    assert transcribe.submit_transcription(manifest) == receipt
