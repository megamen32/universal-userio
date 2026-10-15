"""Fast unit: lease refusal, no stale effects, once-only call job admission.

Expected2s/emergency20s. No phone, real lease or STT request is performed.
Uses the standard library so the same checks run on a source-only runner.
"""
import json
import fcntl
import io
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock
import uuid

from universal_userio import container_platform as platform


class PlatformTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for name, path in [('STATE', self.root), ('RECORDINGS', self.root),
                           ('JOBS', self.root / '.jobs')]:
            patch = mock.patch.object(platform, name, path)
            patch.start(); self.addCleanup(patch.stop)

    def test_lease_adapter_restricts_operations_and_preserves_arguments(self):
        args = ['acquire', '--actor-type', 'system', '--owner', 'system:agentcall-tts-google',
                '--purpose', 'Android warm voice synthesis', '--minutes', '3']
        self.assertEqual(platform.lease_arguments(args), args)
        for bad in [['free'], ['release', 'foreign'], ['acquire', '--actor-type', 'agent'],
                    args[:-1] + ['240'], args[:4] + ['foreign'] + args[5:]]:
            with self.assertRaises(ValueError): platform.lease_arguments(bad)

    def test_duplicate_transcription_submission_preserves_unknown_claim(self):
        identity = str(uuid.uuid4())
        folder = self.root / identity; folder.mkdir(); (folder / 'manifest.json').write_text('{}')
        args = ['start', '--no-block', 'agentcall-transcribe@' + identity + '.service']
        self.assertEqual(platform.submit(args), 0)
        path = platform.JOBS / (identity + '.json')
        record = json.loads(path.read_text()); record['status'] = 'UNKNOWN'
        path.write_text(json.dumps(record))
        self.assertEqual(platform.submit(args), 0)
        self.assertEqual(json.loads(path.read_text())['status'], 'UNKNOWN')
        with self.assertRaises(ValueError): platform.submit(['restart', 'ssh.service'])

    def test_native_busy_returns_without_retry_or_replay(self):
        args = ['release', 'a' * 32]
        (self.root / 'request.json').write_text(json.dumps({'args':args,'created_at':time.time()}))
        from subprocess import CompletedProcess
        with mock.patch.object(platform.subprocess, 'run',
                return_value=CompletedProcess([], 75, '{"status":"busy"}', '')) as run:
            self.assertEqual(platform.native_lease(), 0)
            self.assertEqual(json.loads((self.root / 'response.json').read_text())['returncode'], 75)
            with self.assertRaises(FileNotFoundError): platform.native_lease()
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0], ['/usr/local/bin/s21-lease', *args])

    def test_expired_request_is_consumed_without_guard_action(self):
        (self.root / 'request.json').write_text(json.dumps({'args':['release','a'*32],
                                                         'created_at':time.time()-60}))
        with mock.patch.object(platform.subprocess, 'run', side_effect=AssertionError('stale effect')):
            with self.assertRaisesRegex(ValueError, 'expired'): platform.native_lease()
        self.assertFalse((self.root / 'request.json').exists())

    def test_contended_adapter_does_not_queue_late_lease_effect(self):
        with (self.root / 'lease.lock').open('a') as holder:
            fcntl.flock(holder, fcntl.LOCK_EX)
            stdin = mock.Mock(buffer=io.BytesIO(json.dumps(['release','a'*32]).encode()))
            with mock.patch.object(platform.sys, 'stdin', stdin), mock.patch.object(
                    platform.subprocess, 'run', side_effect=AssertionError('late lease effect')):
                with self.assertRaisesRegex(RuntimeError, 'busy'): platform.host_lease()
        self.assertFalse((self.root / 'request.json').exists())


if __name__ == '__main__':
    unittest.main()
