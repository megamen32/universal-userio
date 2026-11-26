import json
import tempfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from universal_userio.contracts import InboxMessage
from universal_userio.http_api import handler
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore

with tempfile.TemporaryDirectory(dir='/tmp') as tmp:
    store = SQLiteUserIOStore(Path(tmp) / 'userio.sqlite3')
    user, _ = store.create_user('fixture-user', 'fixture-password')
    store.bind_channel_route(user_id=user.user_id, source='mail', route_id='fixture-mail')
    service = UserIOService(store, object(), object())
    chat, _ = service.receive(InboxMessage('gmail', 'fixture-message', 'fixture@example.test', 'DEEPLINK FIXTURE MESSAGE', 123.0), route_id='fixture-mail', user_id=user.user_id)
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler(service, token='fixture-only'))
    print(json.dumps({'port': server.server_port, 'chat': chat, 'message': 'fixture-message'}), flush=True)
    server.serve_forever()
