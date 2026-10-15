"""Focused integration: saved folders must retain old chats without crossing users.

Expected 4 seconds; emergency wall 40 seconds. No provider calls or sends.
"""
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from universal_userio.contracts import InboxMessage
from universal_userio.http_api import handler
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def suggest(self, **kwargs):
        return 'offline fixture'


class Outbox:
    def send_reply(self, **kwargs):
        raise AssertionError('folder reads must never send')


def test_saved_folder_reads_old_cross_channel_chats_and_preserves_user_boundary(tmp_path):
    store = SQLiteUserIOStore(tmp_path / 'folders.sqlite3')
    service = UserIOService(store, Generator(), Outbox())
    old, _ = service.receive(InboxMessage('gmail', 'old', 'old@example.test', 'Old mail', 1), route_id='gmail-read-only')
    sms, _ = service.receive(InboxMessage('sms', 'sms', 'fixture-phone', 'Old SMS', 2), route_id='sms-read-only')
    for i in range(101):
        service.receive(InboxMessage('gmail', str(i), f'{i}@example.test', 'New mail', 100 + i), route_id='gmail-read-only')
    assert old not in {row['id'] for row in store.conversations()}
    other, _ = store.create_user('folder-other', 'correct horse battery staple')
    store.bind_channel_route(user_id=other.user_id, source='gmail', route_id='gmail-read-only')
    foreign, _ = service.receive(InboxMessage('gmail', 'foreign', 'private@example.test', 'Other user', 300), route_id='gmail-read-only', user_id=other.user_id)
    assert store.conversations(conversation_ids=[]) == []

    server = ThreadingHTTPServer(('127.0.0.1', 0), handler(service, token='folder-test-token'))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}/v1/conversations'

    def read(params, token='folder-test-token'):
        request = Request(base + '?' + urlencode(params), headers={'Authorization': f'Bearer {token}'})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    try:
        status, result = read({'conversation_ids': ','.join([old, sms, foreign])})
        assert status == 200
        assert {row['id'] for row in result['conversations']} == {old, sms}
        assert all('analysis_policy' in row for row in result['conversations'])
        status, result = read({'conversation_ids': ','.join([old, sms]), 'source': 'gmail', 'exact_source': '1'})
        assert status == 200
        assert [row['id'] for row in result['conversations']] == [old]
        assert read({'conversation_ids': old}, token='')[0] == 401
        for invalid in ['', 'x,,y', ','.join(f'c{i}' for i in range(101)), 'x' * 129, "x') OR 1=1"]:
            assert read({'conversation_ids': invalid})[0] == 400
        assert read({'conversation_ids': 'missing'}) == (200, {'conversations': []})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
