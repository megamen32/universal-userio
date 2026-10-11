"""Per-UserIO-user Matrix and IMAP connections.

Each connection belongs to an authenticated UserIO principal, not the host's
systemd environment. Provider secrets are never returned from HTTP endpoints.
"""
from __future__ import annotations

import email
import email.policy
import hashlib
import imaplib
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from .channels.matrix import MatrixReader
from .contracts import InboxMessage

if TYPE_CHECKING:
    from .service import UserIOService

log = logging.getLogger("userio.account_connections")
_PROVIDER_TYPES = frozenset({"matrix", "email"})
_ID_RE = re.compile(r"^[a-z0-9]{24,64}$")
_HOST_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,249}[a-z0-9]$")
_MX_ID = re.compile(r"^@[^\s:]{1,255}:[a-zA-Z0-9.-]+(?::\d+)?$")
_MATRIX_MAX_SIZE = 2 * 1024 * 1024


def public_hostname(host: str) -> str:
    """Never allow a web user to make the privileged backend talk to LAN/metadata IPs."""
    host = str(host).strip().lower()
    if not _HOST_RE.fullmatch(host) or ".." in host or host.endswith(".local"):
        raise ValueError("укажите публичный адрес сервера")
    try:
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("не удалось найти сервер") from exc
    if not addresses or not all(ipaddress.ip_address(info[4][0]).is_global for info in addresses):
        raise ValueError("подключение к локальным адресам запрещено")
    return host


def matrix_homeserver(value: str) -> str:
    parsed = urllib.parse.urlsplit(str(value).strip())
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ("", "/")
            or parsed.port not in (None, 443)):
        raise ValueError("Matrix-сервер должен быть публичным HTTPS-адресом")
    hostname = parsed.hostname.lower()
    # Trusted split-DNS Matrix deployments may resolve to their private LAN IP.
    # Only infrastructure administrators can set this exact hostname allowlist.
    trusted = {value.strip().lower() for value in os.environ.get(
        "USERIO_MATRIX_TRUSTED_HOSTS", "").split(",") if value.strip()}
    if hostname not in trusted:
        public_hostname(hostname)
    return "https://" + hostname


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Matrix-сервер не должен перенаправлять учётные данные")


def _matrix_json(url: str, *, token: str = "", payload: dict | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method="POST" if data is not None else "GET",
        headers={
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if data is not None else {}),
            **({"Authorization": "Bearer " + token} if token else {}),
        },
    )
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=12) as response:
            if response.status != 200:
                raise ValueError("Matrix не подтвердил подключение")
            content = response.read(_MATRIX_MAX_SIZE + 1)
        if len(content) > _MATRIX_MAX_SIZE:
            raise ValueError("слишком большой ответ Matrix")
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError("некорректный ответ Matrix")
        return result
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError,
            json.JSONDecodeError) as exc:
        raise ValueError("Matrix не принял вход или сервер недоступен") from exc


class ConnectorSecrets:
    """Opaque file references. Each JSON includes its UserIO owner and provider."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or os.environ.get(
            "USERIO_CONNECTION_SECRETS_DIR", "/var/lib/universal-userio/connected-secrets",
        ))

    def save(self, record: Mapping[str, str]) -> str:
        if record.get("provider") not in _PROVIDER_TYPES or not record.get("user_id"):
            raise ValueError("invalid account ownership")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink() or self.root.stat().st_mode & 0o077:
            raise ValueError("connection secret directory must be private")
        reference = secrets.token_hex(24)
        target = self.root / (reference + ".json")
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        handle = os.open(target, flags, 0o600)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as output:
                json.dump(dict(record), output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return "connector:" + reference

    def load(self, reference: str, *, user_id: str, provider: str) -> dict[str, str]:
        if not reference.startswith("connector:"):
            raise ValueError("not a managed connection")
        key = reference.removeprefix("connector:")
        if not _ID_RE.fullmatch(key):
            raise ValueError("invalid connector reference")
        path = self.root / (key + ".json")
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise ValueError("unsafe connection secret file")
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("provider") != provider or data.get("user_id") != user_id:
            raise PermissionError("connection belongs to another user")
        return data

    def delete(self, reference: str, *, user_id: str, provider: str) -> None:
        # Verify the owner before removing the user-owned secret.
        self.load(reference, user_id=user_id, provider=provider)
        (self.root / (reference.removeprefix("connector:") + ".json")).unlink(missing_ok=True)


def source_for(account_id: str) -> str:
    if account_id.startswith("matrix:") or account_id.startswith("email:"):
        return account_id
    raise ValueError("unsupported account")


def connect_matrix(service: UserIOService, user_id: str, payload: Mapping[str, Any],
                   *, vault: ConnectorSecrets | None = None) -> dict[str, str]:
    server = matrix_homeserver(str(payload.get("homeserver") or ""))
    expected = str(payload.get("user_id") or "").strip()
    if expected and not _MX_ID.fullmatch(expected):
        raise ValueError("укажите Matrix ID в формате @user:server")
    password = str(payload.get("password") or "")
    provided_token = str(payload.get("access_token") or "")
    login_token = str(payload.get("login_token") or "")
    if (sum(bool(x) for x in (password, provided_token, login_token)) != 1
            or max(map(len, (password, provided_token, login_token))) > 4096):
        raise ValueError("выберите один способ входа Matrix: пароль, SSO или токен")
    if password:
        if not expected:
            raise ValueError("для входа с паролем нужен Matrix ID")
        logged_in = _matrix_json(server + "/_matrix/client/v3/login", payload={
            "type": "m.login.password",
            "identifier": {"type": "m.id.user", "user": expected},
            "password": password,
            "initial_device_display_name": "UserIO",
        })
        token = str(logged_in.get("access_token") or "")
    elif login_token:
        logged_in = _matrix_json(server + "/_matrix/client/v3/login", payload={
            "type": "m.login.token", "token": login_token,
            "initial_device_display_name": "UserIO",
        })
        token = str(logged_in.get("access_token") or "")
    else:
        token = provided_token
    if not token:
        raise ValueError("Matrix не выдал токен доступа")
    identity = _matrix_json(server + "/_matrix/client/v3/account/whoami", token=token)
    actual = str(identity.get("user_id") or "")
    if not _MX_ID.fullmatch(actual) or expected and actual != expected:
        raise ValueError("токен принадлежит другому Matrix-аккаунту")
    # Stable per-principal/provider identity. Reconnecting never redirects other users.
    suffix = hashlib.sha256((user_id + "\0" + server + "\0" + actual).encode()).hexdigest()[:28]
    account_id = "matrix:" + suffix
    storage = vault or ConnectorSecrets()
    reference = storage.save({
        "provider": "matrix", "user_id": user_id, "account_id": account_id,
        "homeserver": server, "matrix_user_id": actual, "access_token": token,
    })
    service._store.register_account(
        account_id=account_id, provider="matrix", display_name=actual,
        can_read=True, can_reply=False, credential_ref=reference, user_id=user_id,
    )
    service._store.bind_channel_route(
        user_id=user_id, source=account_id, route_id="matrix-read-only",
    )
    return {"id": account_id, "source": account_id, "display_name": actual}


def connect_imap(service: UserIOService, user_id: str, payload: Mapping[str, Any],
                 *, vault: ConnectorSecrets | None = None,
                 verifier: Any = None) -> dict[str, str]:
    hostname = public_hostname(str(payload.get("host") or ""))
    email_address = str(payload.get("username") or "").strip().lower()
    password = str(payload.get("password") or "")
    port = payload.get("port", 993)
    if (type(port) is not int or not 1 <= port <= 65535
            or not email_address or len(email_address) > 254
            or not password or len(password) > 4096):
        raise ValueError("проверьте адрес, порт и пароль IMAP")
    if verifier is None:
        verifier = verify_imap_login
    verifier(hostname, port, email_address, password)
    suffix = hashlib.sha256(
        (user_id + "\0" + hostname + "\0" + email_address).encode(),
    ).hexdigest()[:28]
    account_id = "email:" + suffix
    storage = vault or ConnectorSecrets()
    reference = storage.save({
        "provider": "email", "user_id": user_id, "account_id": account_id,
        "host": hostname, "port": port, "username": email_address, "password": password,
    })
    service._store.register_account(
        account_id=account_id, provider="email", display_name=email_address,
        can_read=True, can_reply=False, credential_ref=reference, user_id=user_id,
    )
    service._store.bind_channel_route(
        user_id=user_id, source=account_id, route_id="email-read-only",
    )
    return {"id": account_id, "source": account_id, "display_name": email_address}


def verify_imap_login(host: str, port: int, username: str, password: str) -> None:
    try:
        with imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=12) as client:
            client.login(username, password)
            status, _ = client.select("INBOX", readonly=True)
            if status != "OK":
                raise ValueError("не удалось открыть INBOX")
    except (imaplib.IMAP4.error, OSError, TimeoutError) as exc:
        raise ValueError("неверные данные IMAP или сервер недоступен") from exc


def _email_text(message: email.message.Message) -> str:
    if message.is_multipart():
        part = message.get_body(preferencelist=("plain", "html"))
        if part is None:
            return "[письмо без текста]"
        message = part
    try:
        body = str(message.get_content())
    except (LookupError, ValueError, TypeError, AttributeError):
        body = "[письмо без текста]"
    if message.get_content_type() == "text/html":
        from .channels.gmail import html_to_accessible_text
        body = html_to_accessible_text(body)
    return body.strip()[:50_000] or "[письмо без текста]"


class UserAccountPoller:
    """Poll user-owned accounts; no shared Matrix token, cursor, or IMAP login."""

    def __init__(self, service: UserIOService, *, vault: ConnectorSecrets | None = None):
        self.service = service
        self.vault = vault or ConnectorSecrets()
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self.run, name="userio-connected-accounts", daemon=True)
        self.thread.start()

    def run(self) -> None:
        # Bounded concurrency: one slow provider must not block other users.
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="userio-connector") as pool:
            while not self.stop.is_set():
                try:
                    with self.service._store._lock:
                        accounts = [dict(r) for r in self.service._store._connection.execute(
                            """SELECT user_id,id,provider,credential_ref
                               FROM provider_accounts WHERE enabled=1
                                 AND provider IN ('matrix','email')
                                 AND credential_ref LIKE 'connector:%'""",
                        )]
                    futures = [pool.submit(self.poll_registered, account) for account in accounts]
                    for future in futures:
                        future.result()
                except Exception as exc:
                    log.error("connected account loop failed: %s", type(exc).__name__)
                self.stop.wait(15)

    def poll_registered(self, account: Mapping[str, str]) -> None:
        try:
            secret = self.vault.load(
                str(account["credential_ref"]),
                user_id=str(account["user_id"]), provider=str(account["provider"]),
            )
            if secret.get("account_id") != account["id"]:
                raise ValueError("account reference mismatch")
            if account["provider"] == "matrix":
                self.poll_matrix(secret)
            else:
                self.poll_imap(secret)
        except Exception as exc:
            # Never log exception bodies: providers can echo submitted credentials.
            log.warning("connected %s account sync failed: %s",
                        account["provider"], type(exc).__name__)

    def poll_matrix(self, account: Mapping[str, str]) -> None:
        uid, source = account["user_id"], account["account_id"]
        cursor_key = "source_cursor:" + source
        cursor = self.service._store.user_preference(cursor_key, user_id=uid)
        reader = MatrixReader(
            account["homeserver"], account["access_token"], ("*",), account["matrix_user_id"],
            include_own=True,
        )
        # Initial snapshot imports recent messages as history, without triggering
        # live workspace events. Subsequent polls only deliver new events.
        messages, nxt = reader.poll(cursor or "", limit=1000)
        for message in messages:
            self.service.receive(
                InboxMessage(
                    source, f"{message.room_id}:{message.event_id}",
                    message.sender, message.body, message.received_at or time.time(),
                    peer_id=message.room_id, conversation_kind="unknown",
                    direction=message.direction,
                    reconciliation=cursor is None,
                ),
                route_id="matrix-read-only", user_id=uid, account_ref=source,
            )
        if nxt:
            self.service._store.set_user_preference(cursor_key, nxt, user_id=uid)

    def poll_imap(self, account: Mapping[str, Any]) -> None:
        uid, source = account["user_id"], account["account_id"]
        cursor_key = "source_cursor:" + source
        cursor = self.service._store.user_preference(cursor_key, user_id=uid)
        try:
            with imaplib.IMAP4_SSL(
                str(account["host"]), int(account["port"]),
                ssl_context=ssl.create_default_context(), timeout=12,
            ) as client:
                client.login(str(account["username"]), str(account["password"]))
                status, _ = client.select("INBOX", readonly=True)
                if status != "OK":
                    raise RuntimeError("IMAP INBOX unavailable")
                status, result = client.uid("search", None, "ALL")
                if status != "OK":
                    raise RuntimeError("IMAP UID search failed")
                ids = [int(x) for x in (result[0] or b"").split()]
                # A mailbox reset can reuse UIDs. Keep UIDVALIDITY in both the
                # durable cursor and provider message ID when it is available.
                validity = ""
                try:
                    response_fn = getattr(client, "response", None)
                    info = response_fn("UIDVALIDITY") if callable(response_fn) else None
                    raw = (info[1] or [None])[0] if info else None
                    if raw is not None and str(raw.decode() if isinstance(raw, bytes) else raw).isdigit():
                        validity = str(int(raw))
                except (OSError, TypeError, ValueError, IndexError):
                    validity = ""
                stored_validity, sep, stored_uid = (cursor or "").partition(":")
                if not sep:
                    stored_uid, stored_validity = stored_validity, ""
                initial = cursor is None or bool(validity and stored_validity and validity != stored_validity)
                position = 0 if initial else int(stored_uid or "0")
                # First connection imports up to 25 recent messages; subsequent
                # passes fetch new UIDs without changing the server's Seen flags.
                pending = ids[-25:] if initial else [i for i in ids if i > position]
                pending = pending[:100]
                for number in pending:
                    status, rows = client.uid("fetch", str(number), "(BODY.PEEK[])")
                    if status != "OK":
                        raise RuntimeError("IMAP read failed")
                    payload = next(
                        (part[1] for part in rows or [] if isinstance(part, tuple)
                         and isinstance(part[1], bytes)), None,
                    )
                    if payload is None:
                        raise RuntimeError("IMAP returned empty message")
                    parsed = email.message_from_bytes(payload, policy=email.policy.default)
                    sender = parseaddr(str(parsed.get("From") or ""))[1]
                    subject = str(parsed.get("Subject") or "Без темы")
                    try:
                        date = parsedate_to_datetime(str(parsed.get("Date") or "")).timestamp()
                        if not 1 < date < time.time() + 3600:
                            date = time.time()
                    except (ValueError, TypeError, OverflowError):
                        date = time.time()
                    self.service.receive(
                        InboxMessage(
                            source, source + ":" + (validity + ":" if validity else "") +
                            str(number), sender or "unknown",
                            "Тема: " + subject + "\n\n" + _email_text(parsed), date,
                            reconciliation=initial,
                        ),
                        route_id="email-read-only", user_id=uid, account_ref=source,
                    )
                    self.service._store.set_user_preference(
                        cursor_key, (validity + ":" if validity else "") + str(number), user_id=uid,
                    )
                if not pending and initial:
                    self.service._store.set_user_preference(
                        cursor_key, (validity + ":" if validity else "") +
                        str(max(ids) if ids else 0), user_id=uid,
                    )
        except (imaplib.IMAP4.error, OSError, TimeoutError) as exc:
            raise RuntimeError("IMAP connection failed") from exc
