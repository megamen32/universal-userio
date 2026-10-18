from __future__ import annotations

import hashlib
import hmac
import threading
import json
import time
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import universal_userio.http_api as http_api
from universal_userio.account_connections import ConnectorSecrets
from universal_userio.contracts import InboxMessage
from universal_userio.http_api import handler
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore

from universal_userio.webhooks import WebhookDeliveryWorker, send_test, sign_payload, validate_webhook_url


class Store:
    def __init__(self):
        self.config = {
            "enabled": True,
            "endpoint": "https://receiver.example/events",
            "secret_ref": "secret-ref",
            "include_message_text": False,
            "cursor": 10,
            "next_attempt_at": 0,
        }
        self.page = {
            "events": [{
                "seq": 11, "source": "telegram", "account_ref": "telegram:1",
                "message_id": "msg-11", "conversation_id": "chat-1", "sender": "Alice",
                "body": "private message", "received_at": 42.5,
            }],
            "cursor": 11,
            "head": 11,
        }
        self.advanced = []
        self.failures = []
        self.read_enabled = True

    def webhook_users(self):
        return ["owner"] if self.config["enabled"] else []

    def webhook_settings(self, *, user_id):
        return dict(self.config)

    def workspace_events(self, *, after, limit, user_id):
        return self.page

    def capability_enabled(self, _capability, *, user_id):
        return self.read_enabled

    def webhook_event_head(self, *, user_id):
        return int(self.page["head"])

    def advance_webhook_cursor(self, *, user_id, cursor, head, delivered=False):
        self.config["cursor"] = cursor
        self.advanced.append(cursor)

    def record_webhook_failure(self, *, user_id, message, retry_at):
        self.failures.append((message, retry_at))


class Vault:
    def __init__(self, user_id="owner"):
        self.user_id = user_id

    def load(self, reference, *, user_id, provider):
        assert (reference, user_id, provider) == ("secret-ref", self.user_id, "webhook")
        return {"signing_secret": "test-signing-secret"}


class Generator:
    def suggest(self, **_kwargs):
        return "draft"


class Outbox:
    def send_reply(self, **_kwargs):
        return "receipt"


def test_signed_payload_is_stable_and_excludes_message_text_by_default():
    payload = {
        "schema": "userio.inbound-message.v1",
        "event_id": "evt-11",
        "event": {"seq": 11, "body": "private message"},
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    headers = sign_payload(body, "test-signing-secret", event_id="evt-11", timestamp=123)
    expected = hmac.new(b"test-signing-secret", b"123." + body, hashlib.sha256).hexdigest()
    assert headers["X-UserIO-Signature"] == "sha256=" + expected
    assert headers["Idempotency-Key"] == "evt-11"


def test_test_webhook_contains_no_message_and_uses_test_event_header():
    sent = []
    status = send_test(
        {"endpoint": "https://receiver.example/events", "secret_ref": "secret-ref", "user_id": "owner"},
        Vault(), sender=lambda url, body, headers: sent.append((url, json.loads(body), headers)) or 204,
    )
    assert status == 204
    assert sent[0][1]["schema"] == "userio.webhook-test.v1"
    assert sent[0][2]["X-UserIO-Event"] == "webhook.test"
    assert "message_id" not in sent[0][1]["event"]


def test_dispatch_sends_user_scoped_signed_event_without_text_and_advances_only_on_success():
    sent = []

    def sender(url, body, headers):
        sent.append((url, json.loads(body), headers))
        return 202

    store = Store()
    worker = WebhookDeliveryWorker(store, vault=Vault(), sender=sender, clock=lambda: 123)
    worker.run_once()

    assert len(sent) == 1
    url, payload, headers = sent[0]
    assert url == "https://receiver.example/events"
    assert payload["event"] == {
        "seq": 11, "source": "telegram", "account_ref": "telegram:1",
        "message_id": "msg-11", "conversation_id": "chat-1", "sender": "Alice",
        "received_at": 42.5,
    }
    assert payload["schema"] == "userio.inbound-message.v1"
    assert headers["Idempotency-Key"] == payload["event_id"]
    assert headers["X-UserIO-Signature"].startswith("sha256=")
    assert store.advanced == [11]
    assert store.failures == []


def test_dispatch_retry_keeps_cursor_and_records_bounded_failure():
    def sender(_url, _body, _headers):
        raise OSError("receiver unavailable")

    store = Store()
    worker = WebhookDeliveryWorker(store, vault=Vault(), sender=sender, clock=lambda: 123)
    worker.run_once()

    assert store.config["cursor"] == 10
    assert store.advanced == []
    assert len(store.failures) == 1
    assert store.failures[0][0] == "Webhook недоступен; событие будет повторено"
    assert store.failures[0][1] == 123


def test_webhook_url_requires_public_https_endpoint(monkeypatch):
    import socket

    def dns(host, *_args, **_kwargs):
        address = "8.8.8.8" if host == "receiver.example" else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]

    monkeypatch.setattr(socket, "getaddrinfo", dns)
    assert validate_webhook_url("https://receiver.example/events") == "https://receiver.example/events"
    try:
        validate_webhook_url("http://receiver.example/hook")
        assert False, "plaintext webhook URL should fail closed"
    except ValueError:
        pass
    try:
        validate_webhook_url("https://localhost/hook")
        assert False, "private resolver result should fail closed"
    except ValueError:
        pass


def test_saving_webhook_starts_at_current_head_without_replaying_old_messages(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    owner = store.owner().user_id
    with store._lock, store._connection:
        store._connection.executemany(
            "INSERT INTO workspace_events(user_id,source,message_id,conversation_id,eligible) VALUES (?,?,?,?,?)",
            [(owner, "telegram", "old-1", "chat", 1), (owner, "telegram", "old-2", "chat", 1)],
        )
    saved = store.save_webhook_settings(
        endpoint="https://receiver.example/events", secret_ref="connector:test",
        include_message_text=False, enabled=True, user_id=owner,
    )
    assert saved["cursor"] == 2
    assert saved["enabled"] == 1
    store.close()


def test_webhook_failure_uses_bounded_retry_backoff(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    owner = store.owner().user_id
    store.save_webhook_settings(
        endpoint="https://receiver.example/events", secret_ref="connector:test",
        include_message_text=False, enabled=True, user_id=owner,
    )
    before = time.time()
    store.record_webhook_failure(
        user_id=owner, message="Webhook недоступен; событие будет повторено", retry_at=before,
    )
    saved = store.webhook_settings(user_id=owner)
    assert saved["attempt_count"] == 1
    assert 4 <= saved["next_attempt_at"] - before <= 10
    assert saved["last_error"] == "Webhook недоступен; событие будет повторено"
    store.close()


def test_new_message_delivery_reads_durable_event_without_marking_message_seen(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    owner = store.owner().user_id
    store.save_webhook_settings(
        endpoint="https://receiver.example/events", secret_ref="secret-ref",
        include_message_text=True, enabled=True, user_id=owner,
    )
    service.receive(
        InboxMessage("gmail:self", "new-message", "alice@example.test", "Привет", 100.0),
        route_id="workspace-owner",
    )
    sent = []
    worker = WebhookDeliveryWorker(
        store, vault=Vault(owner), sender=lambda url, body, headers: sent.append((json.loads(body), headers)) or 202,
    )
    assert worker.run_once() == 1
    assert len(sent) == 1
    assert sent[0][0]["event"]["text"] == "Привет"
    assert sent[0][0]["event"]["message_id"] == "new-message"
    assert store._connection.execute(
        "SELECT seen_at FROM messages WHERE user_id=? AND message_id=?", (owner, "new-message"),
    ).fetchone()[0] is None
    assert store.workspace_events(after=0, user_id=owner)["events"][0]["message_id"] == "new-message"
    store.close()


def test_disabling_read_capability_skips_pending_webhook_events_without_later_replay(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    owner = store.owner().user_id
    store.save_webhook_settings(
        endpoint="https://receiver.example/events", secret_ref="secret-ref",
        include_message_text=False, enabled=True, user_id=owner,
    )
    service.receive(
        InboxMessage("gmail:self", "held-message", "alice@example.test", "Private", 100.0),
        route_id="workspace-owner",
    )
    store.set_user_capability("read", False, user_id=owner)
    sent = []
    worker = WebhookDeliveryWorker(
        store, vault=Vault(owner), sender=lambda *_args: sent.append(True) or 202,
    )
    assert worker.run_once() == 0
    store.set_user_capability("read", True, user_id=owner)
    assert worker.run_once() == 0
    assert sent == []
    assert store.webhook_settings(user_id=owner)["cursor"] == 1
    store.close()


def test_webhook_http_settings_are_scoped_and_signing_secret_is_returned_once(tmp_path, monkeypatch):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    webhook_user, webhook_token = store.create_user("webhook-owner", "correct horse battery staple")
    _other, other_token = store.create_user("webhook-other", "another correct horse battery staple")
    with store._lock, store._connection:
        store._connection.execute(
            "INSERT INTO workspace_events(user_id,source,message_id,conversation_id,eligible) VALUES (?,?,?,?,?)",
            (webhook_user.user_id, "telegram", "before-enable", "chat", 1),
        )
    vault = ConnectorSecrets(tmp_path / "secrets")
    monkeypatch.setattr(http_api, "ConnectorSecrets", lambda: vault)
    monkeypatch.setattr(http_api, "validate_webhook_url", lambda value: value)
    test_calls = []
    monkeypatch.setattr(http_api, "send_webhook_test", lambda settings, secret_store: test_calls.append((settings, secret_store)) or 202)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="owner-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def request(method, path, bearer=webhook_token, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        raw = Request(base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {bearer}",
            **({"Content-Type": "application/json"} if data is not None else {}),
        })
        try:
            with urlopen(raw) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    try:
        status, saved = request("POST", "/v1/webhooks/incoming", payload={
            "endpoint": "https://receiver.example/events", "enabled": True,
            "include_message_text": False,
        })
        assert status == 200
        signing_secret = saved["one_time_signing_secret"]
        assert saved["secret_configured"] is True
        assert "secret_ref" not in saved
        assert store.webhook_settings(user_id=webhook_user.user_id)["cursor"] == 1

        status, readback = request("GET", "/v1/webhooks/incoming")
        assert status == 200
        assert readback["enabled"] is True
        assert readback["endpoint"] == "https://receiver.example/events"
        assert "one_time_signing_secret" not in readback
        assert "secret_ref" not in readback
        assert request("GET", "/v1/webhooks/incoming", other_token)[1]["configured"] is False

        status, tested = request("POST", "/v1/webhooks/incoming/test", payload={})
        assert status == 200 and tested["ok"] is True
        assert len(test_calls) == 1
        assert vault.load(
            store.webhook_settings(user_id=webhook_user.user_id)["secret_ref"],
            user_id=webhook_user.user_id, provider="webhook",
        )["signing_secret"] == signing_secret
        assert store.workspace_events(after=0, user_id=webhook_user.user_id)["events"] == []
    finally:
        server.shutdown(); server.server_close(); store.close()
