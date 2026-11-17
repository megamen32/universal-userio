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
        return latest_message.body


class Outbox:
    def send_reply(self, **kwargs) -> str:
        return "event-1"


def test_anonymous_visitor_is_durable_and_isolated(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")

    first, created = store.create_anonymous_visitor("A" * 43)
    same, existing = store.create_anonymous_visitor("A" * 43)
    other, other_created = store.create_anonymous_visitor("B" * 43)

    assert created is True
    assert existing is False
    assert other_created is True
    assert first == same
    assert first.user_id != other.user_id
    assert first.username != other.username
    assert store.authenticate_credentials(first.username, "A" * 43) is None
    rows = store._connection.execute(
        "SELECT auth_kind FROM users WHERE id IN (?,?) ORDER BY id",
        (first.user_id, other.user_id),
    ).fetchall()
    assert [str(row["auth_kind"]) for row in rows] == ["anonymous", "anonymous"]
    assert store._connection.execute("SELECT COUNT(*) FROM visitor_sessions").fetchone()[0] == 2


def test_auto_account_http_is_allowlisted_and_sets_opaque_cookie(tmp_path, monkeypatch):
    monkeypatch.setenv("USERIO_AUTO_ACCOUNT_HOSTS", "dmo.bezrabotnyi.com")
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), handler(service, token="owner-token")
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def request(host: str, cookie: str = ""):
        headers = {"Host": host}
        if cookie:
            headers["Cookie"] = cookie
        req = Request(base + "/v1/runtime", headers=headers)
        try:
            with urlopen(req) as response:
                return response.status, response.headers, json.loads(response.read())
        except HTTPError as error:
            return error.code, error.headers, json.loads(error.read())

    try:
        status, headers, payload = request("dmo.bezrabotnyi.com")
        assert status == 200
        assert payload["schema_version"] == 1
        cookies = headers.get_all("Set-Cookie") or []
        assert len(cookies) == 1
        cookie = cookies[0].split(";", 1)[0]
        name, value = cookie.split("=", 1)
        assert name == "userio_visitor"
        assert 32 <= len(value) <= 128
        assert value.replace("-", "").replace("_", "").isalnum()
        assert "HttpOnly" in cookies[0]
        assert "Secure" not in cookies[0]

        status, headers, _ = request("dmo.bezrabotnyi.com", cookie)
        assert status == 200
        assert not (headers.get_all("Set-Cookie") or [])

        status, headers, payload = request("msg.bezrabotnyi.com")
        assert status == 401
        assert payload["error"] == "unauthorized"
        assert not (headers.get_all("Set-Cookie") or [])
        assert store._connection.execute(
            "SELECT COUNT(*) FROM visitor_sessions"
        ).fetchone()[0] == 1
    finally:
        server.shutdown()
        server.server_close()
