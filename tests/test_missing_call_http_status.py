from __future__ import annotations

import json
import threading
import uuid
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from universal_userio.http_api import handler
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def suggest(self, *, conversation_id, latest_message):
        return ""


class Outbox:
    pass


@pytest.mark.parametrize("channel", ["matrix", "telegram"])
def test_missing_call_receipt_returns_404_without_closing_connection(tmp_path, channel, monkeypatch):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    if channel == "matrix":
        from universal_userio import matrix_calls
        monkeypatch.setattr(
            matrix_calls, "calls_for",
            lambda _service: matrix_calls.MatrixCalls(service, root=tmp_path / "matrix-calls"),
        )
    else:
        from universal_userio import telegram_calls
        monkeypatch.setattr(
            telegram_calls, "calls_for",
            lambda _service: telegram_calls.TelegramCalls(service, root=tmp_path / "telegram-calls"),
        )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), handler(service, token="owner-token")
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    call_id = str(uuid.uuid4())
    request = Request(
        f"http://127.0.0.1:{server.server_port}/v1/calls/{channel}/{call_id}",
        headers={"Authorization": "Bearer owner-token"},
    )
    try:
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=3)
        assert error.value.code == 404
        assert json.loads(error.value.read()) == {"error": "call_not_found"}
    finally:
        server.shutdown()
        server.server_close()

