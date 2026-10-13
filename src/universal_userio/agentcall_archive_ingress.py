"""Import completed reserve-node call archives into the existing phone inbox.

Uses the operator's existing SSH identity and UserIO ingress credential. No call,
speech inference or device operation is performed. A completed archive is made
visible atomically before its stable inbox message is published.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
import uuid

from .agentcall_ingress import UserIOIngressClient, userio_url_from_environment
from .call_audio import ARCHIVE, recording

LOG = logging.getLogger(__name__)
FILES = {'manifest.json', 'caller.wav', 'assistant.wav', 'transcript.json',
         'transcript.txt', 'timeline.md', 'transcription-job.json'}
MAX_CALL_BYTES = 512 * 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024

# Sent on stdin to the existing remote Python, never installed or daemonized.
REMOTE = r'''
import hashlib, json, os, pathlib, stat, sys, tarfile, uuid
ROOT = pathlib.Path('/var/lib/agentcall/recordings')
FILES = ('manifest.json','caller.wav','assistant.wav','transcript.json',
         'transcript.txt','timeline.md','transcription-job.json')
def metadata(call_id):
    folder = ROOT / call_id
    if folder.is_symlink(): raise ValueError('linked archive')
    stats = {}
    for name in FILES:
        p = folder / name
        s = p.lstat()
        if not stat.S_ISREG(s.st_mode): raise ValueError('not a regular file')
        limit = 256*1024*1024 if name.endswith('.wav') else 131072
        if s.st_size > limit: raise ValueError('archive file exceeds limit')
        stats[name] = [s.st_size,s.st_mtime_ns]
    manifest = json.loads((folder/'manifest.json').read_text())
    job = json.loads((folder/'transcription-job.json').read_text())
    transcript = json.loads((folder/'transcript.json').read_text())
    if (manifest.get('callId') != call_id or manifest.get('complete') is not True
        or job.get('callId') != call_id or job.get('status') != 'COMPLETE'
        or transcript.get('callId') != call_id
        or not isinstance(transcript.get('tracks'),dict)):
        return None
    fingerprint = hashlib.sha256(json.dumps(stats,sort_keys=True).encode()).hexdigest()
    return dict(call_id=call_id, fingerprint=fingerprint, bytes=sum(x[0] for x in stats.values()),
                manifest=manifest, manifest_sha256=hashlib.sha256((folder/'manifest.json').read_bytes()).hexdigest(),
                transcript_sha256=hashlib.sha256((folder/'transcript.json').read_bytes()).hexdigest())
if REQUEST['operation'] == 'list':
    result = []
    with os.scandir(ROOT) as entries:
        for i, entry in enumerate(entries):
            if i >= 4096: raise ValueError('source archive directory budget exceeded')
            if not entry.is_dir(follow_symlinks=False): continue
            try:
                call_id = str(uuid.UUID(entry.name))
                if call_id != entry.name: continue
                item = metadata(call_id)
                if item: result.append(item)
            except (OSError, ValueError, KeyError): continue
    raw = json.dumps(result).encode()
    if len(raw) > 2*1024*1024: raise ValueError('source metadata budget exceeded')
    sys.stdout.buffer.write(raw)
elif REQUEST['operation'] == 'export':
    call_id = str(uuid.UUID(REQUEST['call_id']))
    item = metadata(call_id)
    if not item or item['fingerprint'] != REQUEST['fingerprint']: raise ValueError('archive changed')
    with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as tar:
        for name in FILES:
            path = ROOT/call_id/name
            with path.open('rb') as stream:
                size = os.fstat(stream.fileno()).st_size
                info = tarfile.TarInfo(name); info.size = size; info.mode = 0o600
                tar.addfile(info,stream)
else: raise ValueError('unsupported archive operation')
'''


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.pending')
    with temporary.open('w') as stream:
        os.chmod(temporary, 0o600)
        json.dump(data, stream, ensure_ascii=False, sort_keys=True)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def deadline(seconds):
    def expired(*_): raise TimeoutError('archive transfer deadline exceeded')
    prior = signal.signal(signal.SIGALRM, expired)
    signal.alarm(seconds)
    try: yield
    finally:
        signal.alarm(0); signal.signal(signal.SIGALRM, prior)


class SSHArchives:
    def __init__(self, host, *, user='roomhacker'):
        if not re.fullmatch(r'[a-zA-Z0-9.-]+', host) or not re.fullmatch(r'[a-zA-Z0-9_-]+', user):
            raise ValueError('configured SSH host/user required')
        self.command = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                        '-o', 'ConnectTimeout=5', '-o', 'ServerAliveInterval=10',
                        '-o', 'ServerAliveCountMax=1', '-o', 'ControlPath=none',
                        '-o', 'ClearAllForwardings=yes', user+'@'+host, 'sudo -n python3 -']
        if os.getuid() == 0:
            self.command = ['runuser', '-u', user, '--'] + self.command

    def script(self, request):
        return ('REQUEST = '+repr(request)+'\n'+REMOTE).encode()

    def list(self):
        with deadline(25):
            with subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL) as process:
                try:
                    process.stdin.write(self.script({'operation':'list'})); process.stdin.close()
                    raw = process.stdout.read(MAX_METADATA_BYTES + 1)
                    if len(raw) > MAX_METADATA_BYTES: raise ValueError('source metadata exceeded limit')
                    if process.wait(timeout=5): raise ConnectionError('source archive read failed')
                finally:
                    if process.poll() is None: process.kill(); process.wait()
        result = json.loads(raw)
        if not isinstance(result, list): raise ValueError('source archive list required')
        return result

    def copy(self, item, destination):
        request = {'operation':'export', 'call_id':item['call_id'], 'fingerprint':item['fingerprint']}
        with deadline(120):
            with subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL) as process:
                try:
                    process.stdin.write(self.script(request)); process.stdin.close()
                    receive_tar(process.stdout, destination)
                    if process.wait(timeout=5): raise ConnectionError('source archive export failed')
                finally:
                    if process.poll() is None: process.kill(); process.wait()


def receive_tar(stream, destination):
    seen, total = set(), 0
    with tarfile.open(fileobj=stream, mode='r|') as tar:
        for member in tar:
            if (member.name not in FILES or member.name in seen or not member.isreg()
                or member.size < 0 or member.size > (256*1024*1024 if member.name.endswith('.wav') else 131072)):
                raise ValueError('invalid archive member')
            seen.add(member.name); total += member.size
            if total > MAX_CALL_BYTES: raise ValueError('call archive exceeds byte budget')
            with (destination/member.name).open('xb') as output:
                os.chmod(output.name, 0o600)
                with tar.extractfile(member) as source:
                    shutil.copyfileobj(source, output, length=65536)
                output.flush(); os.fsync(output.fileno())
    if seen != FILES: raise ValueError('incomplete call archive transfer')


def archive_message(item, account_ref):
    manifest = item['manifest']; call_id = str(uuid.UUID(item['call_id']))
    peer = manifest.get('peerFields', {})
    if (manifest.get('callId') != call_id or manifest.get('complete') is not True
        or peer.get('accountRef') != account_ref
        or not re.fullmatch(r'phone:v1:[0-9a-f]{64}', str(peer.get('peerRef','')))):
        raise ValueError('archive does not belong to configured phone account/peer')
    at = datetime.fromisoformat(manifest['finishedAt'])
    if at.tzinfo is None: raise ValueError('archive timestamp needs timezone')
    return {'schema':'universal.inbox.message.v1', 'source':'phone',
            'message_id':'agentcall-archive-'+call_id, 'sender':call_id,
            'sender_name':'ИИ-помощник', 'body':'Звонок завершён', 'direction':'system',
            'peer_id':peer['peerRef'], 'conversation_title':str(peer.get('peerLabel',''))[:200],
            'received_at':at.timestamp()}


class ArchiveIngress:
    def __init__(self, source, publisher, *, archive=ARCHIVE,
                 state=Path('/var/lib/universal-userio/agentcall-reserve44.json'),
                 account_ref='phone100', disk_budget=5*1024**3):
        self.source, self.publisher = source, publisher
        self.archive, self.state = Path(archive), Path(state)
        self.account_ref, self.disk_budget = account_ref, disk_budget

    def sync_once(self):
        state = json.loads(self.state.read_text()) if self.state.exists() else {'calls':{}}
        calls = state['calls']; imported = 0
        self.archive.mkdir(parents=True, exist_ok=True, mode=0o700)
        for item in self.source.list():
            call_id = str(uuid.UUID(item['call_id']))
            message = archive_message(item, self.account_ref)
            previous = calls.get(call_id, {})
            if previous.get('fingerprint') == item['fingerprint'] and previous.get('published'):
                continue
            folder = self.archive/call_id
            if not folder.exists():
                if (sum(c.get('bytes',0) for c in calls.values()) + item['bytes'] > self.disk_budget
                    or shutil.disk_usage(self.archive).free < 20*1024**3 + item['bytes']):
                    raise OSError('archive disk reserve/budget unavailable')
                temporary = Path(tempfile.mkdtemp(prefix='.reserve44-', dir=self.archive))
                try:
                    staged = temporary/call_id
                    staged.mkdir(mode=0o700)
                    self.source.copy(item, staged)
                    for name, key in [('manifest.json','manifest_sha256'),('transcript.json','transcript_sha256')]:
                        if hashlib.sha256((staged/name).read_bytes()).hexdigest() != item[key]:
                            raise ValueError('archive changed during transfer')
                    save_json(staged/'reserve-source.json', {'host':'server-44','fingerprint':item['fingerprint']})
                    # Existing identity/sample/frame checks also protect the live HTTP path.
                    conv = {'source':'phone','id':'archive-check','account_ref':self.account_ref,'peer_id':message['peer_id']}
                    audio = recording(conv, dict(message, conversation_id=conv['id']), root=temporary)
                    if not audio or not audio['complete']: raise ValueError('source recording is invalid')
                    os.rename(staged, folder)
                finally:
                    if temporary.exists(): shutil.rmtree(temporary)
            receipt = folder/'reserve-source.json'
            if (folder.is_symlink() or not receipt.is_file()
                or json.loads(receipt.read_text()).get('fingerprint') != item['fingerprint']):
                raise ValueError('existing call archive collision; preserve both sources')
            conv = {'source':'phone','id':'archive-check','account_ref':self.account_ref,'peer_id':message['peer_id']}
            audio = recording(conv, dict(message, conversation_id=conv['id']), root=self.archive)
            if not audio or not audio['complete']: raise ValueError('imported recording is invalid')
            if len(calls) >= 10000 and call_id not in calls: raise ValueError('archive checkpoint budget exceeded')
            calls[call_id] = {'fingerprint':item['fingerprint'], 'bytes':item['bytes'], 'published':False}
            save_json(self.state, state)
            self.publisher.publish(message)
            calls[call_id]['published'] = True
            save_json(self.state, state); imported += 1
            if imported >= 8: break
        return imported


def main():
    logging.basicConfig(level=logging.INFO)
    token = os.environ.get('USERIO_BEARER_TOKEN') or os.environ.get('USERIO_API_TOKEN')
    if not token: raise SystemExit('existing UserIO ingress credential required')
    account = os.environ.get('USERIO_ACCOUNT_ID','phone100')
    ingress = ArchiveIngress(SSHArchives(os.environ['USERIO_ARCHIVE_SSH_HOST']),
        UserIOIngressClient(userio_url_from_environment(), token, account_id=account), account_ref=account)
    while True:
        try:
            count = ingress.sync_once()
            if count: LOG.info('Completed phone archives imported: %s', count)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError, tarfile.TarError) as error:
            LOG.warning('Reserve archive sync deferred: %s', type(error).__name__)
        time.sleep(15)


if __name__ == '__main__': main()
