from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import universal_userio.http_api as http_api
from universal_userio.http_api import handler
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def suggest(self, **_kwargs):
        return "draft"


class Outbox:
    def send_reply(self, **_kwargs):
        return "receipt"


def test_account_page_uses_server_disconnect_capability_and_shared_card_styles():
    page = Path("src/universal_userio/static/connect-accounts.html").read_text()
    assert "if(account.can_disconnect===true)" in page
    assert "account.disconnect_reason" in page
    assert ".userio-setup-page .userio-choice" in page
    assert "const managed=" not in page


def test_account_list_marks_user_disconnects_and_keeps_shared_accounts_protected(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    rows = [
        ("telegram:123", "telegram", "telegram-qr:account-1"),
        ("gmail:careviolan", "gmail", "himalaya:careviolan"),
        ("chatgpt:owner", "chatgpt", "chatgpt-session:owner"),
        ("yandex:42", "yandex", "connector:yandex-account"),
        ("whatsapp:79200000000:account-1", "whatsapp", "whatsapp-qr:account-1"),
        ("phone:sip:alice", "phone", "sip-hosted:alice"),
        ("sms:user-device", "sms", "sms-agent:user-device"),
        ("sms-native", "sms", "sms-native:default"),
        ("max", "max", "env:USERIO_MAX_TOKEN"),
        ("telemost", "telemost", "telemost:guest-recorder"),
    ]
    for account_id, provider, credential_ref in rows:
        store.register_account(account_id=account_id, provider=provider,
            display_name=account_id, can_read=True, can_reply=False,
            credential_ref=credential_ref)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="test-token"))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        request = Request(f"http://127.0.0.1:{server.server_port}/v1/accounts",
            headers={"Authorization": "Bearer test-token"})
        with urlopen(request) as response:
            accounts = json.loads(response.read())["accounts"]
        by_id = {row["id"]: row for row in accounts}
        assert by_id["telegram:123"]["can_disconnect"] is True
        assert by_id["gmail:careviolan"]["can_disconnect"] is True
        assert by_id["chatgpt:owner"]["can_disconnect"] is True
        assert by_id["yandex:42"]["can_disconnect"] is True
        assert by_id["whatsapp:79200000000:account-1"]["can_disconnect"] is True
        assert by_id["phone:sip:alice"]["can_disconnect"] is True
        assert by_id["sms:user-device"]["can_disconnect"] is False
        assert "SMS Agent" in by_id["sms:user-device"]["disconnect_reason"]
        assert by_id["sms-native"]["can_disconnect"] is False
        assert by_id["max"]["can_disconnect"] is False
        assert by_id["telemost"]["can_disconnect"] is False
        assert all("credential_ref" not in row for row in accounts)
    finally:
        server.shutdown()
        server.server_close()


def test_telegram_disconnect_logs_out_exact_owner_session_before_removing_account(tmp_path, monkeypatch):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    store.register_account(account_id="telegram:123", provider="telegram", display_name="TG",
        can_read=True, can_reply=True, credential_ref="telegram-qr:account-1")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="test-token"))
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    called = []
    monkeypatch.setattr(http_api, "_disconnect_telegram_qr",
        lambda **kwargs: called.append(kwargs))
    try:
        request = Request(f"http://127.0.0.1:{server.server_port}/v1/accounts/telegram%3A123",
            method="DELETE", headers={"Authorization": "Bearer test-token"})
        with urlopen(request) as response:
            assert json.loads(response.read()) == {"deleted": True}
        assert called == [{"token": "test-token", "user_id": store.default_user_id,
            "account_id": "telegram:123", "credential_ref": "telegram-qr:account-1"}]
        assert store.accounts() == []
    finally:
        server.shutdown(); server.server_close()


def test_telegram_account_stays_registered_when_logout_is_not_confirmed(tmp_path, monkeypatch):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    store.register_account(account_id="telegram:123", provider="telegram", display_name="TG",
        can_read=True, can_reply=True, credential_ref="telegram-qr:account-1")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="test-token"))
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    monkeypatch.setattr(http_api, "_disconnect_telegram_qr",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("not confirmed")))
    try:
        request = Request(f"http://127.0.0.1:{server.server_port}/v1/accounts/telegram%3A123",
            method="DELETE", headers={"Authorization": "Bearer test-token"})
        with pytest.raises(HTTPError) as error:
            urlopen(request)
        assert error.value.code == 503
        assert [row["id"] for row in store.accounts()] == ["telegram:123"]
    finally:
        server.shutdown(); server.server_close()
