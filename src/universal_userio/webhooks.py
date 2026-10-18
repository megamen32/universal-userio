"""User-configured, signed HTTP delivery for allowed incoming UserIO events."""
from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import logging
import secrets
import socket
import ssl
import threading
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from .account_connections import ConnectorSecrets, public_hostname

log = logging.getLogger("userio.webhooks")
_MAX_BODY = 128 * 1024
_POLL_SECONDS = 3.0


def validate_webhook_url(value: str) -> str:
    """Accept public HTTPS endpoints only; no userinfo, query or redirects."""
    url = str(value or "").strip()
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.port not in (None, 443)
            or (parsed.path and not parsed.path.startswith("/")) or len(url) > 2048):
        raise ValueError("Введите публичный HTTPS-адрес без логина, пароля и параметров URL")
    host = parsed.hostname.lower().rstrip(".")
    public_hostname(host)
    return "https://" + host + (parsed.path or "/")


def sign_payload(body: bytes, secret: str, *, event_id: str, timestamp: int | None = None,
                 event_type: str = "message.received") -> dict[str, str]:
    stamp = int(time.time()) if timestamp is None else int(timestamp)
    digest = hmac.new(secret.encode(), str(stamp).encode() + b"." + body, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "UserIO-Webhook/1.0",
        "X-UserIO-Event": event_type,
        "X-UserIO-Timestamp": str(stamp),
        "X-UserIO-Signature": "sha256=" + digest,
        "Idempotency-Key": event_id,
    }


def _public_addresses(host: str) -> list[tuple[int, int, int, tuple[Any, ...]]]:
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses:
        raise OSError("Webhook host did not resolve")
    for family, _kind, _proto, _canon, address in addresses:
        ip = ipaddress.ip_address(address[0])
        if not ip.is_global:
            raise ValueError("Webhook address resolves to a non-public network")
        if family not in (socket.AF_INET, socket.AF_INET6):
            raise ValueError("Unsupported webhook address family")
    return addresses


def https_post(url: str, body: bytes, headers: Mapping[str, str], *, timeout: float = 8.0) -> int:
    """Post to one DNS-pinned public IP while preserving TLS hostname checks."""
    if len(body) > _MAX_BODY:
        raise ValueError("Webhook payload is too large")
    parsed = urlsplit(validate_webhook_url(url))
    host = str(parsed.hostname)
    target = parsed.path or "/"
    last_error: OSError | None = None
    for family, kind, proto, _canon, address in _public_addresses(host):
        raw = socket.socket(family, kind, proto)
        try:
            raw.settimeout(timeout)
            raw.connect(address)
            secure = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
            conn = http.client.HTTPSConnection(host, 443, timeout=timeout, context=ssl.create_default_context())
            conn.sock = secure
            conn.request("POST", target, body=body, headers=dict(headers))
            response = conn.getresponse()
            response.read(4096)
            status = int(response.status)
            conn.close()
            return status
        except (OSError, http.client.HTTPException) as error:
            last_error = OSError(type(error).__name__)
            raw.close()
    if last_error is not None:
        raise last_error
    raise OSError("Webhook endpoint is unavailable")


def _bounded_text(value: object, limit: int = 8000) -> tuple[str, bool]:
    text = str(value or "")
    if len(text) <= limit:
        return text, False
    return text[:limit], True


class WebhookDeliveryWorker:
    """Deliver each allowed incoming event once, retrying with its same key."""

    def __init__(self, store, *, vault: ConnectorSecrets | None = None,
                 sender: Callable[[str, bytes, Mapping[str, str]], int] | None = None,
                 clock: Callable[[], float] = time.time):
        self.store = store
        self.vault = vault or ConnectorSecrets()
        self.sender = sender or https_post
        self.clock = clock
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        self.stop.clear()
        self.thread = threading.Thread(target=self.run, name="userio-webhook-delivery", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=2)

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                delivered = self.run_once()
            except Exception:
                log.exception("Webhook delivery iteration failed")
                delivered = 0
            if not delivered:
                self.stop.wait(_POLL_SECONDS)

    def run_once(self) -> int:
        delivered = 0
        for user_id in self.store.webhook_users():
            settings = self.store.webhook_settings(user_id=user_id)
            if not settings or not settings.get("enabled") or float(settings.get("next_attempt_at") or 0) > self.clock():
                continue
            if not self.store.capability_enabled("read", user_id=user_id):
                head = self.store.webhook_event_head(user_id=user_id)
                self.store.advance_webhook_cursor(user_id=user_id, cursor=head, head=head)
                continue
            page = self.store.workspace_events(
                after=int(settings.get("cursor") or 0), limit=1, user_id=user_id,
            )
            events = page.get("events", [])
            if not events:
                self.store.advance_webhook_cursor(
                    user_id=user_id, cursor=int(page.get("head") or settings.get("cursor") or 0),
                    head=int(page.get("head") or 0),
                )
                continue
            event = events[0]
            seq = int(event["seq"])
            event_id = hashlib.sha256(f"{user_id}\0{seq}".encode()).hexdigest()
            fields = {
                key: event[key] for key in (
                    "seq", "source", "account_ref", "message_id", "conversation_id",
                    "sender", "received_at", "sender_is_bot",
                ) if key in event
            }
            if settings.get("include_message_text"):
                fields["text"], fields["text_truncated"] = _bounded_text(event.get("body"))
            payload = {"schema": "userio.inbound-message.v1", "event_id": event_id, "event": fields}
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
            try:
                secret = self.vault.load(
                    str(settings["secret_ref"]), user_id=user_id, provider="webhook",
                )["signing_secret"]
                status = int(self.sender(
                    str(settings["endpoint"]), body,
                    sign_payload(body, secret, event_id=event_id, timestamp=int(self.clock())),
                ))
                if not 200 <= status < 300:
                    raise RuntimeError("receiver returned HTTP " + str(status))
            except Exception as error:
                log.warning("Webhook delivery failed for one UserIO event (%s)", type(error).__name__)
                self.store.record_webhook_failure(
                    user_id=user_id, message="Webhook недоступен; событие будет повторено",
                    retry_at=self.clock(),
                )
                continue
            self.store.advance_webhook_cursor(user_id=user_id, cursor=seq, head=seq, delivered=True)
            delivered += 1
        return delivered


def test_payload(*, event_id: str | None = None) -> dict[str, object]:
    return {
        "schema": "userio.webhook-test.v1",
        "event_id": event_id or "test-" + secrets.token_urlsafe(18),
        "event": {"type": "test", "message": "Проверка связи UserIO. Новые сообщения не отправлялись."},
    }


def send_test(settings: Mapping[str, Any], vault: ConnectorSecrets, *, sender=None) -> int:
    if not settings.get("endpoint") or not settings.get("secret_ref"):
        raise ValueError("Сначала сохраните адрес и подпись webhook")
    secret = vault.load(str(settings["secret_ref"]), user_id=str(settings["user_id"]), provider="webhook")["signing_secret"]
    payload = test_payload()
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    send = sender or https_post
    status = int(send(str(settings["endpoint"]), body, sign_payload(
        body, secret, event_id=str(payload["event_id"]), event_type="webhook.test",
    )))
    if not 200 <= status < 300:
        raise RuntimeError("Webhook вернул HTTP " + str(status))
    return status
