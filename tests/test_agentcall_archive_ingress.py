"""Fast unit<2s/max20s: archive durability and inbox dedup after interruption."""
import hashlib
import io
import json
import shutil
import tarfile
import wave

import pytest

from universal_userio.agentcall_archive_ingress import ArchiveIngress, FILES, receive_tar
from universal_userio.call_audio import recording

CALL = '22222222-3333-4444-8555-666666666666'
PEER = 'phone:v1:' + 'a'*64


def fixture_source(tmp_path):
    folder = tmp_path/'remote'/CALL; folder.mkdir(parents=True)
    manifest = {'callId':CALL,'complete':True,'sampleRate':8000,
                'finishedAt':'2026-10-14T00:00:00+00:00',
                'peerFields':{'accountRef':'phone100','peerRef':PEER,'peerLabel':'Тест'}}
    transcript = {'callId':CALL,'tracks':{
        'caller':{'segments':[{'text':'Здравствуйте'}]},
        'assistant':{'segments':[{'text':'Я вас слышу'}]}}}
    for name, data in [('manifest.json',manifest),('transcript.json',transcript),
                       ('transcription-job.json',{'callId':CALL,'status':'COMPLETE'})]:
        (folder/name).write_text(json.dumps(data))
    for name in ('transcript.txt','timeline.md'): (folder/name).write_text('Текст')
    for name in ('caller.wav','assistant.wav'):
        with wave.open(str(folder/name),'wb') as audio:
            audio.setparams((1,2,8000,0,'NONE','not compressed')); audio.writeframes(b'\x01\x00'*800)
    item = {'call_id':CALL,'manifest':manifest,'fingerprint':'f'*64,
            'bytes':sum((folder/n).stat().st_size for n in FILES),
            'manifest_sha256':hashlib.sha256((folder/'manifest.json').read_bytes()).hexdigest(),
            'transcript_sha256':hashlib.sha256((folder/'transcript.json').read_bytes()).hexdigest()}
    class Source:
        copies = 0
        def list(self): return [item]
        def copy(self, item, destination):
            self.copies += 1
            for n in FILES: shutil.copyfile(folder/n,destination/n)
    return Source(), item, folder


def test_interrupted_publish_resumes_existing_archive_once_without_duplicate(tmp_path):
    source, _, _ = fixture_source(tmp_path)
    class Publisher:
        attempts = []; delivered = []
        def publish(self, message):
            self.attempts.append(message.copy())
            if len(self.attempts) == 1: raise ConnectionError('inbox temporarily unavailable')
            self.delivered.append(message.copy())
    publisher = Publisher()
    ingress = ArchiveIngress(source,publisher,archive=tmp_path/'archive',state=tmp_path/'state.json')
    with pytest.raises(ConnectionError): ingress.sync_once()
    assert (tmp_path/'archive'/CALL/'caller.wav').is_file()
    assert json.loads((tmp_path/'state.json').read_text())['calls'][CALL]['published'] is False
    assert ingress.sync_once() == 1
    assert ingress.sync_once() == 0
    assert source.copies == 1 and len(publisher.delivered) == 1
    assert publisher.attempts[0]['message_id'] == publisher.attempts[1]['message_id']
    conv = {'id':'accepted','source':'phone','account_ref':'phone100','peer_id':PEER}
    audio = recording(conv,dict(publisher.delivered[0],conversation_id='accepted'),root=tmp_path/'archive')
    assert audio['complete'] and [x['transcript'] for x in audio['tracks']] == ['Здравствуйте','Я вас слышу']


def test_invalid_pcm_is_never_committed_or_published(tmp_path):
    source, _, folder = fixture_source(tmp_path)
    (folder/'caller.wav').write_bytes(b'broken')
    class Publisher:
        def publish(self, message): pytest.fail('invalid audio must not reach inbox')
    ingress = ArchiveIngress(source,Publisher(),archive=tmp_path/'archive',state=tmp_path/'state.json')
    with pytest.raises(ValueError,match='source recording'): ingress.sync_once()
    assert not (tmp_path/'archive'/CALL).exists()
    assert list((tmp_path/'archive').iterdir()) == []


def test_existing_foreign_archive_is_preserved(tmp_path):
    source, _, _ = fixture_source(tmp_path)
    folder = tmp_path/'archive'/CALL; folder.mkdir(parents=True)
    (folder/'caller.wav').write_bytes(b'owned by another producer')
    class Publisher:
        def publish(self, message): pytest.fail('collision must not reach inbox')
    ingress = ArchiveIngress(source,Publisher(),archive=tmp_path/'archive',state=tmp_path/'state.json')
    with pytest.raises(ValueError,match='collision'): ingress.sync_once()
    assert (folder/'caller.wav').read_bytes() == b'owned by another producer'
    assert source.copies == 0


def test_partial_tar_does_not_pass_archive_completeness(tmp_path):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as tar:
        info = tarfile.TarInfo('manifest.json'); info.size = 2
        tar.addfile(info,io.BytesIO(b'{}'))
    stream.seek(0)
    with pytest.raises(ValueError,match='incomplete'): receive_tar(stream,tmp_path)
