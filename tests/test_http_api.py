from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from universal_userio.contracts import InboxMessage
from universal_userio.http_api import handler
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def suggest(self, *, conversation_id: str, latest_message: InboxMessage) -> str:
        return "draft for " + latest_message.body


class Outbox:
    def __init__(self) -> None:
        self.calls = []

    def send_reply(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return "event_1"


def test_http_business_path_requires_auth_and_only_sends_after_approval(tmp_path) -> None:
    outbox = Outbox()
    service = UserIOService(SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Generator(), outbox)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="test-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        payload = {"route_id": "telegram-reply", "message": {"schema": "universal.inbox.message.v1", "source": "telegram", "message_id": "1", "sender": "chat", "body": "hello"}}
        request = Request(base + "/v1/messages", data=json.dumps(payload).encode(), method="POST", headers={"Content-Type": "application/json"})
        try:
            urlopen(request)
        except HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("unauthenticated ingress was accepted")

        request.add_header("Authorization", "Bearer test-token")
        with urlopen(request) as response:
            accepted = json.loads(response.read())
        assert accepted["accepted"] is True
        assert outbox.calls == []

        approve = Request(base + f"/v1/drafts/{accepted['draft']['id']}/approve", data=b"{}", method="POST", headers={"Authorization": "Bearer test-token"})
        with urlopen(approve) as response:
            assert json.loads(response.read())["status"] == "approved"
        assert outbox.calls[0]["route_id"] == "telegram-reply"
    finally:
        server.shutdown()
        server.server_close()
