"""Focused integration: real WAV bytes, account binding and HTTP seek ranges (5s/30s)."""
import json
import threading
import uuid
import wave
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest
from universal_userio import call_audio
from universal_userio.contracts import InboxMessage
from universal_userio.http_api import handler
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


def archive(root, call_id, peer='phone:v1:' + 'a' * 64):
    folder = root / call_id
    folder.mkdir()
    for name, sample in [('caller', b'\x11\x01'), ('assistant', b'\x33\x02')]:
        with wave.open(str(folder / (name + '.wav')), 'wb') as audio:
            audio.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            audio.writeframes(sample * 8000)
    (folder / 'manifest.json').write_text(json.dumps({'callId': call_id, 'complete': True,
        'peerFields': {'accountRef': 'phone100', 'peerRef': peer}}))


def test_call_audio_real_ranges_ownership_and_reload(tmp_path, monkeypatch):
    store = SQLiteUserIOStore(tmp_path / 'db')
    service = UserIOService(store, None, None)
    call_id = str(uuid.uuid4())
    peer = 'phone:v1:' + 'a' * 64
    service.receive(InboxMessage(source='phone', message_id='end', sender=call_id,
        body='Звонок завершён', direction='system', received_at=1, peer_id=peer),
        route_id='phone', account_ref='phone100')
    conv_id = store.message('end', source='phone')['conversation_id']
    monkeypatch.setattr(call_audio, 'ARCHIVE', tmp_path)
    archive(tmp_path, call_id)
    _, other = store.create_user('another', 'correct horse battery staple')
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler(service, token='owner'))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    def read(path, token='owner', **headers):
        return urlopen(Request(base + path, headers={'Authorization': 'Bearer ' + token, **headers}))
    try:
        for _ in range(2):
            with read('/v1/conversations/' + conv_id) as response:
                conv = json.load(response)
            recording = conv['messages'][0]['call_audio']
            assert recording['complete'] and recording['duration'] == 1
            assert [t['label'] for t in recording['tracks']] == ['Собеседник', 'Телефон']
        for track, expected in zip(recording['tracks'], [b'\x11\x01', b'\x33\x02']):
            with read(track['url'], Range='bytes=44-47') as response:
                assert response.status == 206
                assert response.headers['Content-Range'] == 'bytes 44-47/16044'
                assert response.read() == expected * 2
            with pytest.raises(HTTPError) as error:
                read(track['url'], token=other)
            assert error.value.code == 404
        with pytest.raises(HTTPError) as error:
            read(track['url'], Range='bytes=99999-')
        assert error.value.code == 416
        manifest = tmp_path / call_id / 'manifest.json'
        data = json.loads(manifest.read_text());data['peerFields']['accountRef'] = 'wrong'
        manifest.write_text(json.dumps(data))
        with pytest.raises(HTTPError) as error:
            read(track['url'])
        assert error.value.code == 404
    finally:
        server.shutdown();server.server_close();thread.join()


def test_incomplete_recording_stays_honest_and_invalid_call_has_no_audio(tmp_path):
    call_id = str(uuid.uuid4());archive(tmp_path, call_id)
    conv = {'id': 'conv', 'source': 'phone', 'account_ref': 'phone100', 'peer_id': 'phone:v1:' + 'a' * 64}
    message = {'source': 'phone', 'conversation_id': 'conv', 'sender': call_id, 'message_id': 'end'}
    path = tmp_path / call_id / 'manifest.json'
    data = json.loads(path.read_text());data['complete'] = False;path.write_text(json.dumps(data))
    assert call_audio.recording(conv, message, tmp_path)['complete'] is False
    assert call_audio.recording(conv, dict(message, sender='../private'), tmp_path) is None
    (tmp_path / call_id / 'caller.wav').unlink()
    assert call_audio.recording(conv, message, tmp_path) is None


@pytest.mark.parametrize('value,expected', [('', (0,99,200)), ('bytes=10-',(10,99,206)),
    ('bytes=-10',(90,99,206)), ('bytes=0-999',(0,99,206))])
def test_single_range(value, expected):
    assert call_audio.byte_range(value, 100) == expected


@pytest.mark.parametrize('value', ['bytes=100-', 'bytes=-0', 'bytes=2-1', 'bytes=0-1,2-3'])
def test_bad_range(value):
    with pytest.raises(ValueError):call_audio.byte_range(value, 100)
