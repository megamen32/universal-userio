import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

import pytest

from universal_userio import chatgpt_sessions
from universal_userio.contracts import InboxMessage
from universal_userio.http_api import handler
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


def test_registered_chatgpt_can_sync_only_to_its_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(chatgpt_sessions, 'SESSION_DIR', tmp_path / 'sessions')
    store = SQLiteUserIOStore(tmp_path / 'db.sqlite')
    owner, token = store.create_user('airlock@example.com', 'test-password')
    other, _ = store.create_user('other@example.com', 'test-password')
    service = UserIOService(store, None, None)
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler(service, token='service-token'))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        request = Request(f'http://127.0.0.1:{server.server_port}/v1/chatgpt/sessions',
            data=json.dumps({'email': 'chat@example.com', 'session_token': 'test'}).encode(),
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, method='POST')
        with urlopen(request) as response:
            assert response.status == 202
        message = InboxMessage('chatgpt:chat-example.com', 'm1', 'chat1', 'hello', 1.0)
        _, accepted = service.receive(message, route_id='chatgpt', user_id=owner.user_id)
        assert accepted
        with pytest.raises(ValueError, match='route is not assigned'):
            service.receive(message, route_id='chatgpt', user_id=other.user_id)
    finally:
        server.shutdown()
        server.server_close()
