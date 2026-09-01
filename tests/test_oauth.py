from __future__ import annotations

import base64
import hashlib
import json
import threading
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from universal_userio.http_api import handler
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def suggest(self, **_kwargs) -> str:
        return "draft"


class Outbox:
    def send_reply(self, **_kwargs) -> str:
        return "event"


class Inputs(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "input":
            values = dict(attrs)
            if values.get("name") and values.get("value") is not None:
                self.values[values["name"]] = str(values["value"])


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args):
        return None


def _request(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None):
    return Request(url, data=data, method="POST" if data is not None else "GET", headers=headers or {})


def _json(base: str, path: str, payload: dict) -> dict:
    with urlopen(_request(
        base + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )) as response:
        return json.loads(response.read())


def _pkce(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def test_oauth_discovery_and_mcp_challenge(tmp_path) -> None:
    service = UserIOService(SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Generator(), Outbox())
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="legacy-service-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/.well-known/oauth-protected-resource") as response:
            resource = json.loads(response.read())
        assert resource == {"resource": base + "/mcp", "authorization_servers": [base]}
        with urlopen(base + "/.well-known/oauth-authorization-server") as response:
            metadata = json.loads(response.read())
        assert metadata["issuer"] == base
        assert metadata["grant_types_supported"] == ["authorization_code", "refresh_token"]
        assert metadata["code_challenge_methods_supported"] == ["S256"]
        try:
            urlopen(_request(base + "/mcp", data=b"{}", headers={"Content-Type": "application/json"}))
        except HTTPError as error:
            assert error.code == 401
            assert error.headers["WWW-Authenticate"] == (
                f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource"'
            )
        else:
            raise AssertionError("MCP accepted an anonymous request")
    finally:
        server.shutdown()
        server.server_close()


def test_oauth_pkce_dance_refresh_rotation_and_legacy_service_token(tmp_path) -> None:
    service = UserIOService(SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Generator(), Outbox())
    user, _ = service._store.create_user("connector-user", "long-password")
    service._store.register_account(
        account_id="only-mine", provider="telegram", display_name="Only mine",
        can_read=True, can_reply=False, credential_ref="secret://mine", user_id=user.user_id,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="legacy-service-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        registration = _json(base, "/register", {
            "redirect_uris": ["https://client.example/callback"],
            "token_endpoint_auth_method": "none",
        })
        assert "client_secret" not in registration
        verifier = "verifier-with-enough-entropy-for-a-test-0123456789"
        authorize = base + "/authorize?" + urlencode({
            "response_type": "code", "client_id": registration["client_id"],
            "redirect_uri": "https://client.example/callback", "scope": "userio",
            "state": "opaque-state", "code_challenge": _pkce(verifier),
            "code_challenge_method": "S256",
        })
        with urlopen(authorize) as response:
            page = response.read().decode()
        form = Inputs()
        form.feed(page)
        assert form.values["code_challenge_method"] == "S256"
        form.values.update({"username": "connector-user", "password": "long-password", "consent": "allow"})
        try:
            build_opener(NoRedirect()).open(_request(
                base + "/authorize", data=urlencode(form.values).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ))
        except HTTPError as error:
            assert error.code == 302
            redirect = error.headers["Location"]
        else:
            raise AssertionError("authorization did not redirect")
        values = parse_qs(urlparse(redirect).query)
        assert values["state"] == ["opaque-state"]
        code = values["code"][0]

        token_request = {
            "grant_type": "authorization_code", "client_id": registration["client_id"],
            "code": code, "redirect_uri": "https://client.example/callback",
            "code_verifier": "wrong-verifier",
        }
        try:
            urlopen(_request(base + "/token", data=urlencode(token_request).encode()))
        except HTTPError as error:
            assert error.code == 400
            assert json.loads(error.read())["error"] == "invalid_grant"
        else:
            raise AssertionError("wrong PKCE verifier was accepted")

        token_request["code_verifier"] = verifier
        with urlopen(_request(base + "/token", data=urlencode(token_request).encode())) as response:
            tokens = json.loads(response.read())
        assert tokens["token_type"] == "Bearer"
        assert tokens["expires_in"] == 3600

        call = _request(base + "/mcp", data=json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "userio.accounts.list", "arguments": {}},
        }).encode(), headers={"Authorization": "Bearer " + tokens["access_token"], "Content-Type": "application/json"})
        with urlopen(call) as response:
            result = json.loads(response.read())["result"]["structuredContent"]
        assert [account["id"] for account in result["accounts"]] == ["only-mine"]

        try:
            urlopen(_request(base + "/token", data=urlencode(token_request).encode()))
        except HTTPError as error:
            assert json.loads(error.read())["error"] == "invalid_grant"
        else:
            raise AssertionError("authorization code was reusable")

        refresh_request = {
            "grant_type": "refresh_token", "client_id": registration["client_id"],
            "refresh_token": tokens["refresh_token"],
        }
        with urlopen(_request(base + "/token", data=urlencode(refresh_request).encode())) as response:
            rotated = json.loads(response.read())
        assert rotated["refresh_token"] != tokens["refresh_token"]
        try:
            urlopen(_request(base + "/token", data=urlencode(refresh_request).encode()))
        except HTTPError as error:
            assert json.loads(error.read())["error"] == "invalid_grant"
        else:
            raise AssertionError("old refresh token was reusable")

        confidential = _json(base, "/register", {
            "redirect_uris": ["https://client.example/other"],
            "token_endpoint_auth_method": "client_secret_post",
        })
        try:
            urlopen(_request(base + "/token", data=urlencode({
                "grant_type": "refresh_token", "client_id": confidential["client_id"],
                "client_secret": "wrong", "refresh_token": "not-a-token",
            }).encode()))
        except HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("bad client secret was accepted")

        try:
            urlopen(base + "/authorize?" + urlencode({
                "response_type": "code", "client_id": registration["client_id"],
                "redirect_uri": "https://attacker.example/callback",
                "code_challenge": _pkce(verifier), "code_challenge_method": "S256",
            }))
        except HTTPError as error:
            assert error.code == 400
        else:
            raise AssertionError("unknown redirect URI was accepted")

        with urlopen(_request(base + "/mcp", data=b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}',
                              headers={"Authorization": "Bearer legacy-service-token", "Content-Type": "application/json"})) as response:
            assert response.status == 200
    finally:
        server.shutdown()
        server.server_close()
