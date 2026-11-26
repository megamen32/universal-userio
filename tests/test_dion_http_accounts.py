"""The installed extension's first request must reach the native account API."""
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

from universal_userio.http_api import handler
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


def test_extension_account_probe_uses_authenticated_http(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "db.sqlite3")
    _, token = store.create_user("dion-http", "correct-horse-battery-staple")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(
        UserIOService(store, object(), object()), token="owner-token",
    ))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(f"http://127.0.0.1:{server.server_port}/v1/dion/accounts",
                          headers={"Authorization": "Bearer " + token})
        with urlopen(request, timeout=5) as response:
            assert response.status == 200
            assert json.load(response) == {"accounts": []}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        store.close()
