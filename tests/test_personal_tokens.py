"""Focused integration: named token lifecycle and real HTTP/MCP user isolation.

Expected 3 seconds; maximum 30 seconds. No provider calls or real credentials.
"""
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from universal_userio.http_api import handler
from universal_userio.personal_tokens import PersonalTokens
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


def test_personal_tokens_multiple_clients_revocation_and_user_isolation(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "tokens.sqlite")
    alice, login = store.create_user("alice", "a correct test password")
    bob, bob_login = store.create_user("bob", "another correct test password")
    service = UserIOService(store, object(), object())
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="test-service"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(method, path, bearer="", payload=None):
        request = Request(f"http://127.0.0.1:{server.server_port}" + path,
                          method=method, data=None if payload is None else json.dumps(payload).encode(),
                          headers={"Authorization": "Bearer " + bearer, "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read()), dict(response.headers)
        except HTTPError as response:
            return response.code, json.loads(response.read()), dict(response.headers)

    try:
        assert call("GET", "/v1/personal-tokens")[0] == 401
        assert call("POST", "/v1/personal-tokens", login, {"name": " "})[0] == 400
        status, first, headers = call("POST", "/v1/personal-tokens", login, {"name": "ZCode"})
        assert status == 201 and headers["Cache-Control"] == "no-store"
        assert first["token_returned_once"] is True and first["expires_at"] is None
        _, second, _ = call("POST", "/v1/personal-tokens", login, {"name": "Laptop"})
        assert first["token"] != second["token"]
        status, listing, _ = call("GET", "/v1/personal-tokens", first["token"])
        assert status == 200 and len(listing["tokens"]) == 2
        assert {t["name"] for t in listing["tokens"]} == {"ZCode", "Laptop"}
        assert all("token" not in t and "token_hash" not in t for t in listing["tokens"])
        assert call("GET", "/v1/personal-tokens", bob_login)[1] == {"tokens": []}
        assert call("DELETE", "/v1/personal-tokens/" + first["id"], bob_login)[1] == {"revoked": False}
        assert store.authenticate_token(first["token"]).user_id == alice.user_id
        status, initialize, _ = call("POST", "/mcp", first["token"],
                                    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                     "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                                "clientInfo": {"name": "token-test", "version": "1"}}})
        assert status == 200 and "result" in initialize
        status, tools, _ = call("POST", "/mcp", first["token"],
                               {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert status == 200 and tools["result"]["tools"]
        assert call("DELETE", "/v1/personal-tokens/" + first["id"], login)[1] == {"revoked": True}
        assert call("GET", "/v1/accounts", first["token"])[0] == 401
        assert call("GET", "/v1/accounts", second["token"])[0] == 200
        assert store.authenticate_token(login).user_id == alice.user_id
        # A new handler preserves names/revocation and cannot recover plaintext.
        assert len(PersonalTokens(store).list(alice.user_id)) == 2
        assert store.authenticate_token(first["token"]) is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_token_names_are_text_and_legacy_login_tokens_are_preserved(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "legacy.sqlite")
    user, login = store.create_user("legacy", "a correct test password")
    tokens = PersonalTokens(store)
    assert tokens.list(user.user_id) == []
    named = tokens.create(user.user_id, "  Desktop  ")
    assert named["name"] == "Desktop"
    assert store.authenticate_token(login).user_id == user.user_id
    for name in (None, "", "x" * 81):
        try:
            tokens.create(user.user_id, name)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid token name accepted")
