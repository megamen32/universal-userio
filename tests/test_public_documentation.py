"""Focused integration: public docs, private data and normal navigation (max 10s)."""
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen

from universal_userio.http_api import handler
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


def test_public_documentation_does_not_expose_private_routes(tmp_path):
    service = UserIOService(SQLiteUserIOStore(tmp_path / 'userio.sqlite3'), object(), object())
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler(service, token='test-only'))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        for path in ('/docs', '/docs/', '/docs/index.html'):
            with urlopen(base + path, timeout=2) as response:
                assert response.status == 200
                assert 'text/html' in response.headers['Content-Type']
                body = response.read().decode()
                assert 'id="pages-data"' in body
                assert 'Android SMS' in body
                assert 'id="search-dialog"' in body
        for path in ('/v1/accounts', '/v1/inbox', '/docs/../../.env', '/docs/README.md'):
            try:
                urlopen(base + path, timeout=2)
            except HTTPError as error:
                assert error.code == 401
            else:
                raise AssertionError(f'private/unpublished route exposed: {path}')
        with urlopen(base + '/welcome', timeout=2) as response:
            assert 'href="/docs/"' in response.read().decode()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
