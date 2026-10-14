"""One call's warm Google engine. Every initialization/render has an S21 lease.

The idle child accepts no work until the lease-owning parent writes a request.
EOF, a ten-minute device deadline and 128 requests bound its lifetime. The caller
owns close(); errors produce no playable partial audio and retain normal fallback.
"""
import base64
from contextlib import contextmanager
import json
import select
import os
import subprocess
import threading
import time
import uuid


class WarmPhoneTts:
    def __init__(self):
        self._lock = threading.RLock()
        self._process = None
        self._buffer = bytearray()
        self._sequence = 0
        self._tag = ''
        self._closed = False

    @contextmanager
    def _lease(self):
        result = subprocess.run(
            ['/usr/local/bin/s21-lease', 'acquire', '--actor-type', 'system',
             '--owner', 'system:agentcall-tts-google', '--purpose',
             'Android warm voice synthesis', '--minutes', '3'],
            capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise RuntimeError('Android TTS deferred: S21 lease is busy')
        lease = json.loads(result.stdout)['lease_id']
        try:
            yield lease
        finally:
            subprocess.run(['/usr/local/bin/s21-lease', 'release', lease],
                           capture_output=True, timeout=10)

    def _event(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            end = self._buffer.find(b'\n')
            if end >= 0:
                line = bytes(self._buffer[:end])
                del self._buffer[:end + 1]
                return json.loads(line)
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self._process.stdout], [], [], remaining)[0]:
                raise RuntimeError('Android TTS response deadline')
            data = os.read(self._process.stdout.fileno(), 65536)
            if not data:
                raise RuntimeError('Android TTS worker closed')
            self._buffer.extend(data)
            if len(self._buffer) > 12 * 1024 * 1024:
                raise RuntimeError('Android TTS response capacity')

    def _ensure(self):
        if self._closed:
            raise RuntimeError('Android TTS call is closed')
        if self._process is not None and self._process.poll() is None and self._sequence < 128:
            return
        self._dispose()
        self._tag = uuid.uuid4().hex
        self._sequence = 0
        self._buffer.clear()
        self._process = subprocess.Popen(
            ['ssh', '-i', '/home/roomhacker/.ssh/phone_gateway_ed25519',
             '-p', '22220', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
             '-o', 'LogLevel=ERROR', 'phone@127.0.0.1',
             "su -c 'CLASSPATH=/data/local/tmp/phone-tts.dex "
             "app_process /system/bin PhoneTts --stream " + self._tag + "'"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        event = self._event(22)
        if event.get('event') != 'ready' or event.get('run_tag') != self._tag:
            raise RuntimeError('Android TTS readiness response mismatch')

    def warm(self):
        with self._lock:
            try:
                with self._lease():
                    self._ensure()
            except Exception:
                self._dispose()
                raise

    def synthesize(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError('Android TTS text capacity')
        with self._lock:
            try:
                with self._lease() as lease:
                    self._ensure()
                    valid = subprocess.run(['/usr/local/bin/s21-lease', 'check', lease],
                                           capture_output=True, timeout=5)
                    if valid.returncode:
                        raise RuntimeError('Android TTS lease expired before render')
                    self._sequence += 1
                    request = {'seq': self._sequence,
                               'text': base64.b64encode(text.encode()).decode()}
                    self._process.stdin.write(json.dumps(request).encode() + b'\n')
                    self._process.stdin.flush()
                    event = self._event(22)
                    if (event.get('event') != 'wave' or type(event.get('seq')) is not int
                            or event['seq'] != self._sequence):
                        raise RuntimeError('Android TTS response mismatch')
                    wave = base64.b64decode(event['wave'], validate=True)
                    if len(wave) < 100 or not wave.startswith(b'RIFF'):
                        raise RuntimeError('Android TTS response has no WAV')
                    return wave
            except Exception:
                self._dispose()
                raise

    def _dispose(self):
        process, self._process = self._process, None
        self._buffer.clear()
        if process is None:
            return
        try:
            # Closing the own input stops synthesis on the device. Drain output
            # as well, so a completed WAV cannot hold the SSH child on a full pipe.
            if process.stdin:
                process.stdin.close()
                process.stdin = None
            process.communicate(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            process.terminate()
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=2)

    def close(self):
        with self._lock:
            self._closed = True
            self._dispose()
