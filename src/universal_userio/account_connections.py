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
_PROVIDER_TYPES = frozenset({"matrix", "email", "vk", "max", "yandex", "webhook"})
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
        "device_id": str(identity.get("device_id") or ""),
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
        self._account_cursor = 0

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
                                 AND provider IN ('matrix','email','vk','max','yandex')
                                 AND credential_ref LIKE 'connector:%'
                               ORDER BY user_id,id""",
                        )]
                    # Bound runnable account jobs. A multi-user deployment
                    # must not enqueue every tenant into an unbounded queue.
                    if accounts:
                        limit = min(16, len(accounts))
                        start = self._account_cursor % len(accounts)
                        selected = [accounts[(start + i) % len(accounts)]
                                    for i in range(limit)]
                        self._account_cursor = (start + limit) % len(accounts)
                    else:
                        selected = []
                        self._account_cursor = 0
                    futures = [pool.submit(self.poll_registered, account) for account in selected]
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
            if account["provider"] == "max":
                from .max_accounts import poll_personal_max
                poll_personal_max(self.service,secret)
            elif account["provider"] == "matrix":
                self.poll_matrix(secret)
            elif account["provider"] == "email":
                self.poll_imap(secret)
            elif account["provider"] == "yandex":
                from .yandex.sync import poll
                poll(self.service, secret)
            elif account["provider"] == "vk" and secret.get("kind") == "community":
                self.poll_vk_community(secret)
            elif account["provider"] == "vk" and secret.get("kind") == "personal":
                self.poll_vk_personal(secret)
        except Exception as exc:
            # Never log exception bodies: providers can echo submitted credentials.
            log.warning("connected %s account sync failed: %s",
                        account["provider"], type(exc).__name__)


    def poll_vk_personal(self, account: Mapping[str, str]) -> None:
        from .channels.vk_personal import VKPersonalReader
        user = str(account["user_id"])
        account_id = str(account["account_id"])
        token = str(account["access_token"])
        reader = VKPersonalReader(token)
        page_key = "vk-personal:dialog-page:" + account_id
        offset = int(self.service._store.user_preference(page_key, user_id=user) or "0")
        page = reader.conversations(offset=offset, count=40)
        results = page["items"]
        user_names: dict[int, str] = {}
        for profile in page.get("profiles") or []:
            if isinstance(profile, dict) and type(profile.get("id")) is int:
                user_names[profile["id"]] = (
                    str(profile.get("first_name") or "") + " " +
                    str(profile.get("last_name") or "")
                ).strip()[:120]
        processed = 0
        next_offset = offset + len(results)
        for index, item in enumerate(results):
            if not isinstance(item, dict):
                continue
            peer = (item.get("conversation") or {}).get("peer") or {}
            peer_id = peer.get("id")
            latest = item.get("last_message") or {}
            if type(peer_id) is not int or peer_id == 0:
                continue
            peer_type = str(peer.get("type") or "")
            if peer_type in {"user", "group", "chat"}:
                self.service._store.reconcile_vk_peer_kind(
                    account_ref=account_id, peer_id=str(peer_id),
                    kind="group" if peer_type == "chat" else "direct", user_id=user,
                )
            key = f"vk-personal:message-cursor:{account_id}:{peer_id}"
            previous = int(self.service._store.user_preference(key, user_id=user) or "0")
            latest_id = latest.get("id") or (item.get("conversation") or {}).get("last_message_id")
            if type(latest_id) is not int or latest_id <= previous:
                continue
            if processed >= 5:
                # Resume exactly at the first unprocessed dialog, rather than
                # skipping an account's less-active conversations.
                next_offset = offset + index
                break
            messages = reader.history(peer_id, count=40)
            # VK returns newest first. Older pages are needed if a busy chat
            # accumulated many messages while UserIO was offline.
            page_number = 1
            while previous and len(messages) >= page_number * 40:
                earliest = min(
                    (m["id"] for m in messages if type(m.get("id")) is int),
                    default=0,
                )
                if earliest <= previous:
                    break
                if page_number >= 5:
                    # Never advance a cursor across an unverified history gap.
                    raise RuntimeError("VK history catch-up requires more pages")
                older = reader.history(peer_id, count=40, offset=page_number * 40)
                messages.extend(older)
                page_number += 1
                if len(older) < 40:
                    break
            settings = (item.get("conversation") or {}).get("chat_settings") or {}
            title = str(settings.get("title") or "") if isinstance(settings, dict) else ""
            if not title:
                title = user_names.get(peer_id, f"VK {peer_id}")
            for row in sorted(messages, key=lambda m: m.get("id") or 0):
                if type(row.get("id")) is not int or row["id"] <= previous:
                    continue
                envelope = vk_personal_message(
                    account_id=account_id, peer_id=peer_id, row=row,
                    peer_type=peer_type,
                    title=title, reconciled=not previous,
                    in_read=(item.get("conversation") or {}).get("in_read"),
                )
                if envelope is not None:
                    self.service.receive(
                        envelope, route_id="vk-personal-read-only", user_id=user,
                        account_ref=account_id,
                    )
            self.service._store.set_user_preference(
                key, str(max((m["id"] for m in messages if type(m.get("id")) is int),
                             default=previous)), user_id=user,
            )
            processed += 1
        # Rotate pages fairly; newly active dialogs normally appear first.
        total = int(page.get("count") or len(results))
        self.service._store.set_user_preference(
            page_key, str(next_offset if results and next_offset < total else 0),
            user_id=user,
        )

    def poll_vk_community(self, account: Mapping[str, str]) -> None:
        from .channels.vk_community import VKCommunityReader

        uid = account["user_id"]
        ref = account["account_id"]
        group = int(account["group_id"])
        reader = VKCommunityReader(account["access_token"], group)
        page_key = "vk-community:dialog-page:" + ref
        offset = int(self.service._store.user_preference(page_key, user_id=uid) or "0")
        page = reader.conversations(offset=offset, count=25)
        name_map = {}
        for row in page.get("profiles") or []:
            if isinstance(row, dict) and type(row.get("id")) is int:
                name_map[row["id"]] = (
                    str(row.get("first_name") or "") + " " +
                    str(row.get("last_name") or "")
                ).strip()[:120]
        results = page["items"]
        next_offset = offset + len(results)
        processed = 0
        for index, item in enumerate(results):
            if not isinstance(item, dict):
                continue
            conversation = item.get("conversation") or {}
            peer = conversation.get("peer") or {}
            peer_id = peer.get("id")
            if type(peer_id) is not int or peer_id == 0:
                continue
            newest = ((item.get("last_message") or {}).get("id") or
                      conversation.get("last_message_id"))
            cursor_key = f"vk-community:message-cursor:{ref}:{peer_id}"
            previous = int(self.service._store.user_preference(cursor_key, user_id=uid) or "0")
            if type(newest) is not int or newest <= previous:
                continue
            if processed >= 5:
                next_offset = offset + index
                break
            items = reader.history(peer_id, count=35)
            pages = 1
            # A busy dialog can have >35 new items since last sync. Never
            # advance a cursor past messages not fetched from the provider.
            while previous and pages <= 4:
                earliest = min(
                    (v["id"] for v in items if type(v.get("id")) is int),
                    default=0,
                )
                if earliest <= previous or len(items) < pages * 35:
                    break
                if pages == 4:
                    raise RuntimeError("VK community history gap exceeds catch-up budget")
                older = reader.history(peer_id, offset=pages * 35, count=35)
                items.extend(older)
                pages += 1
                if len(older) < 35:
                    break
            settings = conversation.get("chat_settings") or {}
            title = (
                str(settings.get("title") or "")[:120]
                if isinstance(settings, dict) else ""
            ) or name_map.get(peer_id, f"VK {peer_id}")
            peer_type = str(peer.get("type") or "")
            in_read = conversation.get("in_read")
            for message in sorted(items, key=lambda r: r.get("id") or 0):
                if type(message.get("id")) is not int or message["id"] <= previous:
                    continue
                inbox = vk_community_message(
                    account_id=ref, group_id=group, peer_id=peer_id,
                    row=message, title=title, peer_type=peer_type,
                    in_read=in_read, reconciled=not previous,
                )
                if inbox is not None:
                    self.service.receive(
                        inbox, route_id="vk-community-read-only",
                        user_id=uid, account_ref=ref,
                    )
            highest = max(
                (row["id"] for row in items if type(row.get("id")) is int),
                default=previous,
            )
            if highest > previous:
                self.service._store.set_user_preference(
                    cursor_key, str(highest), user_id=uid,
                )
            processed += 1
        total = int(page.get("count") or len(results))
        self.service._store.set_user_preference(
            page_key, str(next_offset if results and next_offset < total else 0),
            user_id=uid,
        )

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
        from .matrix_crypto import for_account, enabled_for
        if enabled_for(account):
            messages, nxt = reader.poll(cursor or "", limit=1000, data=for_account(account).poll(cursor))
        else:
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


def connect_vk_community(
    service: UserIOService, user_id: str, payload: Mapping[str, Any],
    *, vault: ConnectorSecrets | None = None, reader_factory: Any = None,
) -> dict[str, str]:
    """Connect a consented VK community inbox, not the private VK profile."""
    from .channels.vk_community import VKCommunityReader

    raw_group = str(payload.get("group_id") or "").strip()
    if not raw_group.isdecimal() or len(raw_group) > 16:
        raise ValueError("укажите числовой ID сообщества VK")
    group_id = int(raw_group)
    if group_id <= 0:
        raise ValueError("ID сообщества должен быть положительным")
    token = str(payload.get("access_token") or "")
    if not 10 <= len(token) <= 4096:
        raise ValueError("укажите токен сообщества VK")
    reader = (reader_factory or VKCommunityReader)(token, group_id)
    snapshot = reader.conversations(count=1)
    dialogs = snapshot.get("items") or []
    if dialogs:
        peer = (dialogs[0].get("conversation") or {}).get("peer") or {}
        peer_id = peer.get("id")
        if type(peer_id) is int and peer_id:
            reader.history(peer_id, count=1)
    account_id = f"vk:community:{group_id}"
    name = str(payload.get("display_name") or f"VK сообщество #{group_id}")[:120]
    storage = vault or ConnectorSecrets()
    reference = storage.save({
        "provider": "vk", "kind": "community", "user_id": user_id,
        "account_id": account_id, "group_id": str(group_id),
        "access_token": token,
    })
    old_account = next(
        (item for item in service._store.accounts(user_id=user_id)
         if item["id"] == account_id), None,
    )
    service._store.register_account(
        account_id=account_id, provider="vk", display_name=name,
        can_read=True, can_reply=False, enabled=True,
        credential_ref=reference, user_id=user_id,
    )
    service._store.bind_channel_route(
        user_id=user_id, source="vk", route_id="vk-community-read-only",
    )
    if old_account:
        old_ref = str(old_account.get("credential_ref") or "")
        if old_ref.startswith("connector:") and old_ref != reference:
            try:
                storage.delete(old_ref, user_id=user_id, provider="vk")
            except (OSError, ValueError, PermissionError):
                pass
    return {"id": account_id, "display_name": name, "source": "vk",
            "mode": "vk_community_messages_readonly"}


def vk_community_message(
    *, account_id: str, group_id: int, peer_id: int,
    row: Mapping[str, Any], title: str = "", peer_type: str = "",
    in_read: Any = None, reconciled: bool = False,
) -> InboxMessage | None:
    """Stable user/peer/message identity with VK-native read watermark."""
    ident = row.get("id")
    when = row.get("date")
    if type(ident) is not int or ident <= 0 or type(when) is not int or when <= 0:
        return None
    body = str(row.get("text") or "")
    attachments = row.get("attachments") or []
    if not body and isinstance(attachments, list) and attachments:
        body = "[Вложение VK]"
    if not body:
        return None
    author = row.get("from_id")
    outgoing = (row.get("out") == 1 or row.get("out") is True or
                author == -group_id)
    read = (ident <= in_read) if (
        not outgoing and type(in_read) is int
    ) else None
    kind = "group" if peer_type == "chat" or peer_id >= 2_000_000_000 else (
        "direct" if peer_type == "user" else "unknown"
    )
    edited = row.get("update_time")
    return InboxMessage(
        source="vk", message_id=f"{account_id}|{peer_id}:{ident}",
        sender=str(author) if type(author) is int else str(peer_id),
        body=body[:50_000], received_at=float(when), peer_id=str(peer_id),
        direction="outgoing" if outgoing else "incoming",
        conversation_kind=kind, conversation_title=title,
        reconciliation=reconciled, provider_read=read,
        edited_at=float(edited) if type(edited) in (int, float) else 0.0,
    )

def connect_vk_personal(
    service: UserIOService, user_id: str, payload: Mapping[str, Any],
    *, vault: ConnectorSecrets | None = None, reader_factory: Any = None,
) -> dict[str, str]:
    """Enable VK personal messages only after the actual message permission is proven."""
    from .channels.vk_personal import VKPersonalReader
    from .channels.vk_authorization import resolve_authorization

    expected = str(payload.get("user_id") or "").strip()
    if expected and (not expected.isdecimal() or len(expected) > 20):
        raise ValueError("неверный VK ID")
    supplied = str(payload.get("authorization") or payload.get("access_token") or "")
    token, asserted_id, expires_in = resolve_authorization(supplied)
    reader = (reader_factory or VKPersonalReader)(token)
    actual = reader.identity()
    if (expected and int(expected) != actual) or (asserted_id and int(asserted_id) != actual):
        raise ValueError("токен принадлежит другому аккаунту VK")
    dialogs = reader.conversations(count=1)
    if dialogs["items"]:
        first_peer = ((dialogs["items"][0].get("conversation") or {}).get("peer") or {}).get("id")
        if type(first_peer) is int and first_peer:
            reader.history(first_peer, count=1)
    # The legacy VK ID account is upgraded only once both methods succeed.
    account_id = f"vk:{actual}"
    storage = vault or ConnectorSecrets()
    reference = storage.save({
        "provider": "vk", "user_id": user_id, "account_id": account_id,
        "kind": "personal", "vk_user_id": str(actual), "access_token": token,
        **({"expires_at": str(time.time() + expires_in)} if expires_in else {}),
    })
    old_account = next(
        (row for row in service._store.accounts(user_id=user_id) if row["id"] == account_id),
        None,
    )
    service._store.register_account(
        account_id=account_id, provider="vk",
        display_name=str(payload.get("display_name") or f"VK {actual}")[:120],
        can_read=True, can_reply=True, credential_ref=reference, user_id=user_id,
    )
    service._store.bind_channel_route(user_id=user_id, source="vk", route_id="vk-personal-read-only")
    if old_account:
        old_reference = str(old_account.get("credential_ref") or "")
        if old_reference.startswith("connector:") and old_reference != reference:
            try:
                storage.delete(old_reference, user_id=user_id, provider="vk")
            except (OSError, ValueError, PermissionError):
                pass
    return {"id": account_id, "source": "vk", "display_name": str(payload.get("display_name") or f"VK {actual}")[:120],
            "mode": "vk_personal_messages"}


def _vk_personal_client(service, conversation, user_id, *, for_send=True):
    from .channels.vk_personal import VKPersonalClient
    account_ref=str(conversation.get('account_ref') or '')
    account=next((a for a in service._store.accounts(user_id=user_id)
                  if a['id']==account_ref and a['provider']=='vk' and a['enabled']
                  and ('reply' if for_send else 'read') in a['capabilities']),None)
    if account is None or not account_ref.startswith('vk:') or not account_ref[3:].isdecimal():
        raise ValueError('Личный аккаунт VK не разрешает отправку')
    secret=ConnectorSecrets().load(account['credential_ref'],user_id=user_id,provider='vk')
    if (secret.get('kind'),secret.get('account_id'),secret.get('user_id'),secret.get('vk_user_id'))!=('personal',account_ref,user_id,account_ref[3:]):
        raise ValueError('VK credential account binding mismatch')
    return VKPersonalClient(secret['access_token']),int(account_ref[3:])


def prepare_vk_reply(service, conversation, draft, *, user_id):
    client,expected=_vk_personal_client(service,conversation,user_id)
    peer=str(conversation.get('peer_id') or '')
    if not re.fullmatch(r'-?[1-9][0-9]*',peer): raise ValueError('VK recipient is missing')
    if client.identity()!=expected: raise ValueError('Токен принадлежит другому аккаунту VK')
    from .vk_web import VKWeb
    web=VKWeb(service,user_id)
    upload=web.for_draft(draft.id,conversation)
    if (not draft.body.strip() and not upload) or len(draft.body)>4096:
        raise ValueError('Сообщение VK должно содержать от 1 до 4096 символов')
    import hashlib
    key='\0'.join((user_id,conversation['account_ref'],peer,draft.id))
    random_id=(int.from_bytes(hashlib.sha256(key.encode()).digest()[:4],'big')&0x7fffffff) or 1
    if upload:
        return lambda:web.send_file(client,conversation,draft,upload,random_id)
    return lambda:client.send(int(peer),draft.body,random_id)


def reconcile_vk_receipt(service, conversation, receipt, *, user_id):
    client,expected=_vk_personal_client(service,conversation,user_id)
    row=client.message(int(receipt))
    peer=int(conversation['peer_id'])
    if row.get('peer_id')!=peer or row.get('from_id')!=expected or row.get('out')!=1:
        raise ValueError('VK sent receipt account/peer conflict')
    message=vk_personal_message(row=row,account_id=conversation['account_ref'],peer_id=peer,
        peer_type='chat' if conversation.get('conversation_kind')=='group' else 'user',
        title=conversation.get('display_name') or '',reconciled=True)
    if message is None: raise ValueError('VK sent receipt has no actual message')
    service.receive(message,route_id=conversation['route_id'],user_id=user_id,account_ref=conversation['account_ref'])

def vk_personal_message(
    *, account_id: str, peer_id: int, row: Mapping[str, Any],
    title: str = "", reconciled: bool, in_read: Any = None, peer_type: str = "",
) -> InboxMessage | None:
    """VK message envelope, retaining actual peer and local account identity."""
    message_id = row.get("id")
    received = row.get("date")
    if type(message_id) is not int or message_id <= 0:
        return None
    if type(received) is not int or received <= 0:
        return None
    body = str(row.get("text") or "")
    if not body and row.get("attachments"):
        body = "[Вложение VK]"
    if not body:
        return None
    outgoing = row.get("out") is True or row.get("out") == 1
    peer = str(peer_id)
    from .channels.vk_personal import vk_native_attachments
    return InboxMessage(
        source="vk", message_id=f"{account_id}|{peer}:{message_id}",
        sender=str(row.get("from_id")) if type(row.get("from_id")) is int else peer,
        sender_name=title if str(row.get('from_id'))==peer else '', body=body[:50_000],
        attachments=tuple(vk_native_attachments(row)),
        received_at=float(received), direction="outgoing" if outgoing else "incoming",
        peer_id=peer, conversation_kind=("group" if peer_type == "chat" else "direct"
                                       if peer_type in {"user", "group"} else
                                       "group" if peer_id >= 2_000_000_000 else "direct"),
        conversation_title=title, reconciliation=reconciled,
        provider_read=(message_id <= in_read) if (
            not outgoing and type(in_read) is int
        ) else None,
        edited_at=float(row.get("update_time")) if (
            type(row.get("update_time")) in (int,float)
        ) else 0.0,
    )
