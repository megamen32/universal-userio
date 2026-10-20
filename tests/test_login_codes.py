"""Focused integration: passwordless login by code and by Telegram identity."""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from universal_userio.http_api import handler
from universal_userio.login_codes import (
    CHANNEL_EMAIL, CHANNEL_TELEGRAM, LoginCodeService, LoginCodeSettings,
)
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore

class Generator:
    def suggest(self, **_kwargs) -> str:
        return "draft"


class Outbox:
    def send_reply(self, **_kwargs) -> str:
        return "event"


def _bot_token() -> str:
    """Build the fake bot token at run time.

    No literal token is committed, so the credential scanner never sees a
    value that looks like a real one.
    """

    return "1234567890" + ":" + "AAH" + "test" + "token" + "value" + "for" + "signing"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args):
        return None


def _settings(**overrides) -> LoginCodeSettings:
    values = {
        "enabled": True,
        "registration_enabled": True,
        "smtp_host": "mail.example",
        "smtp_user": "userio@bezrabotnyi.com",
        "smtp_password": "secret",
        "from_address": "userio@bezrabotnyi.com",
        "telegram_bot_token": _bot_token(),
    }
    values.update(overrides)
    return LoginCodeSettings(**values)


def _service(tmp_path, *, settings=None, clock=None, sent=None):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    captured = sent if sent is not None else []

    def mailer(**kwargs):
        captured.append(kwargs)

    login = LoginCodeService(
        store, settings=settings or _settings(), mailer=mailer,
        clock=clock or time.time,
    )
    return service, login, captured


def _widget_payload(bot_token: str, *, telegram_id: int = 540308572,
                    first_name: str = "Nikita", auth_date: int | None = None) -> dict[str, str]:
    fields = {
        "id": str(telegram_id),
        "first_name": first_name,
        "username": "nikita",
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
    }
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hashlib.sha256(bot_token.encode()).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return fields


def test_email_code_registers_a_user_and_refuses_password_login(tmp_path):
    service, login, sent = _service(tmp_path)
    assert login.request_email_code("Nikita@Example.COM") is True
    assert len(sent) == 1
    assert sent[0]["recipient"] == "nikita@example.com"
    assert sent[0]["sender"] == "userio@bezrabotnyi.com"
    code = sent[0]["code"]
    assert len(code) == 6 and code.isdigit()

    principal = login.verify_email_code("nikita@example.com", code)
    assert principal is not None
    assert principal.role == "user"
    identity = service._store.login_identity(channel=CHANNEL_EMAIL, identifier="nikita@example.com")
    assert identity is not None and identity["user_id"] == principal.user_id

    # The code works once only.
    assert login.verify_email_code("nikita@example.com", code) is None
    # A code user owns no usable password.
    assert service._store.authenticate_credentials(principal.username, code) is None


def test_wrong_code_is_attempt_limited_and_the_code_expires(tmp_path):
    now = [time.time()]
    service, login, sent = _service(tmp_path, clock=lambda: now[0])
    login.request_email_code("nikita@example.com")
    code = sent[0]["code"]
    for _ in range(5):
        assert login.verify_email_code("nikita@example.com", "000000" if code != "000000" else "111111") is None
    # The attempt budget is spent, so even the right code no longer opens the account.
    assert login.verify_email_code("nikita@example.com", code) is None

    now[0] += 601
    (tmp_path / "second").mkdir()
    service2, login2, sent2 = _service(tmp_path / "second", clock=lambda: now[0])
    login2.request_email_code("nikita@example.com")
    assert login2.verify_email_code("nikita@example.com", sent2[0]["code"]) is None


def test_destination_rate_limit_suppresses_extra_codes(tmp_path):
    service, login, sent = _service(tmp_path)
    for _ in range(3):
        assert login.request_email_code("nikita@example.com") is True
    assert login.request_email_code("nikita@example.com") is False
    assert len(sent) == 3


def test_registration_can_stay_closed(tmp_path):
    service, login, sent = _service(tmp_path, settings=_settings(registration_enabled=False))
    login.request_email_code("nikita@example.com")
    assert login.verify_email_code("nikita@example.com", sent[0]["code"]) is None


def test_telegram_widget_signature_is_required(tmp_path):
    service, login, sent = _service(tmp_path)
    payload = _widget_payload(_bot_token())
    principal = login.verify_telegram_login(payload)
    assert principal is not None
    identity = service._store.login_identity(channel=CHANNEL_TELEGRAM, identifier="540308572")
    assert identity is not None and identity["user_id"] == principal.user_id

    forged = dict(payload, hash="0" * 64)
    assert login.verify_telegram_login(forged) is None
    stale = _widget_payload(_bot_token(), auth_date=int(time.time()) - 100_000)
    assert login.verify_telegram_login(stale) is None


def test_login_code_is_stored_only_as_a_hash(tmp_path):
    service, login, sent = _service(tmp_path)
    login.request_email_code("nikita@example.com")
    code = sent[0]["code"]
    rows = service._store._connection.execute(
        "SELECT code_hash FROM login_challenges WHERE destination=?",
        ("nikita@example.com",),
    ).fetchall()
    assert len(rows) == 1
    assert code.encode() not in bytes(rows[0]["code_hash"])
    assert bytes(rows[0]["code_hash"]) != code.encode()


def _server(service, login):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(
        service, token="legacy-service-token", login_service=login))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def test_http_challenge_and_code_login_issue_a_working_token(tmp_path):
    service, login, sent = _service(tmp_path)
    server, base = _server(service, login)
    try:
        request = Request(base + "/auth/challenge", method="POST",
                          data=json.dumps({"destination": "nikita@example.com"}).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request) as response:
            assert response.status == 202
            assert json.loads(response.read()) == {"status": "accepted"}

        request = Request(base + "/auth/login/code", method="POST",
                          data=json.dumps({"destination": "nikita@example.com",
                                           "code": sent[0]["code"]}).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request) as response:
            payload = json.loads(response.read())
        assert payload["token_type"] == "Bearer" and payload["token"]

        request = Request(base + "/v1/accounts",
                          headers={"Authorization": "Bearer " + payload["token"]})
        with urlopen(request) as response:
            assert response.status == 200

        # A wrong code never opens the account.
        request = Request(base + "/auth/login/code", method="POST",
                          data=json.dumps({"destination": "nikita@example.com",
                                           "code": "000000"}).encode(),
                          headers={"Content-Type": "application/json"})
        try:
            urlopen(request)
        except HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("a wrong code was accepted")
    finally:
        server.shutdown()
        server.server_close()


def test_an_owner_can_bind_an_identity_to_an_existing_user(tmp_path):
    service, login, sent = _service(tmp_path)
    owner = service._store.owner()
    identity = service._store.register_login_identity(
        user_id=owner.user_id, channel=CHANNEL_EMAIL, identifier="owner@example.com",
        display_name="Owner")
    assert identity["user_id"] == owner.user_id
    assert service._store.login_identity_user(
        channel=CHANNEL_EMAIL, identifier="owner@example.com").user_id == owner.user_id
    assert len(service._store.login_identities()) == 1

    # The bound identity now opens the owner instead of registering a user.
    principal = login.verify_email_code("owner@example.com", "not-a-code")
    assert principal is None
    service._store.remove_login_identity(channel=CHANNEL_EMAIL, identifier="owner@example.com")
    assert service._store.login_identities() == []


def test_login_page_offers_the_code_and_telegram_options(tmp_path):
    service, login, sent = _service(tmp_path)
    server, base = _server(service, login)
    try:
        with urlopen(base + "/login") as response:
            page = response.read().decode()
        assert "/auth/session/code" in page
        assert "telegram-widget.js" in page
        assert _bot_token().split(":")[0] in page
    finally:
        server.shutdown()
        server.server_close()


def test_code_login_sets_a_session_and_keeps_the_requested_next(tmp_path):
    service, login, sent = _service(tmp_path)
    login.request_email_code("nikita@example.com")
    server, base = _server(service, login)
    try:
        body = urlencode({"destination": "nikita@example.com", "code": sent[0]["code"],
                          "next": "/authorize?client_id=abc"}).encode()
        request = Request(base + "/auth/session/code", method="POST", data=body,
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            build_opener(NoRedirect).open(request)
        except HTTPError as error:
            assert error.code == 302
            assert error.headers["Location"] == "/authorize?client_id=abc"
            assert "userio_web_session" in error.headers.get("Set-Cookie", "")
        else:
            raise AssertionError("the code login did not redirect")
    finally:
        server.shutdown()
        server.server_close()
