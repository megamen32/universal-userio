"""Read-only Gmail ingress owned by Universal UserIO."""
from __future__ import annotations
import argparse, hashlib, json, logging, os, signal, threading, time, urllib.request
from pathlib import Path
from typing import Any, Mapping
from .channels.gmail import GmailMessage, HimalayaReader, _source, himalaya_mailbox_addresses, mailbox_identity, readable_message_body

log = logging.getLogger("userio.gmail_ingress")


def _preview(value: str, limit: int) -> str:
    compact = " ".join(str(value or "").split())
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


class NoticePlaceEmailNotifier:
    """Create one idempotent owner notification for each provider message."""

    def __init__(
        self, event_url: str, token: str, *, project: str = "userio", recipient: str = "me",
        opener: object = urllib.request.urlopen,
    ) -> None:
        self.event_url = event_url.strip()
        self.token = token.strip()
        self.project = project.strip()
        self.recipient = recipient.strip()
        self._opener = opener
        if not all((self.event_url, self.token, self.project, self.recipient)):
            raise ValueError("NoticePlace email notifier requires URL, token, project and recipient")

    def notify(self, *, account: str, source: str, message: GmailMessage) -> None:
        mailbox = mailbox_identity(message, account)
        digest = hashlib.sha256(f"{source}\0{message.message_id}".encode()).hexdigest()
        event_key = f"userio-gmail:{digest}"
        subject = _preview(message.subject or "Без темы", 240)
        sender = _preview(message.sender or "Неизвестный отправитель", 160)
        payload = {
            "schema": "notify.event.v1",
            "project": self.project,
            "recipient": self.recipient,
            "kind": "notification",
            "severity": "notice",
            "title": f"Новое письмо: {mailbox}",
            "body": (
                f"Почтовый аккаунт: {mailbox}. От: {sender}. Тема: {subject}. "
                "Письмо сохранено в UserIO и доступно для разбора."
            ),
            "dedup_key": event_key,
        }
        request = urllib.request.Request(
            self.event_url, data=json.dumps(payload, ensure_ascii=False).encode(), method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Idempotency-Key": event_key,
            },
        )
        opener = self._opener
        if not callable(opener):
            raise TypeError("NoticePlace opener must be callable")
        with opener(request, timeout=10) as response:  # type: ignore[misc]
            if int(response.status) not in {200, 202}:
                raise RuntimeError(f"NoticePlace returned HTTP {response.status}")


class UserIOIngressClient:
    def __init__(self, base_url: str, token: str, *, notifier: object | None = None) -> None:
        self.base_url, self.token = base_url.rstrip("/"), token
        self.notifier = notifier
        self._recent_message_ids: dict[str, frozenset[str]] = {}

    def _json(self, request: urllib.request.Request) -> dict[str, Any]:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            if int(response.status) not in {200, 202}:
                raise RuntimeError(f"UserIO returned HTTP {response.status}")
            return json.loads(response.read())

    def cursor(self, source: str) -> str | None:
        from urllib.parse import quote
        req = urllib.request.Request(
            self.base_url + "/v1/source-cursors/" + quote(source, safe=""),
            headers={"Authorization": f"Bearer {self.token}"},
        )
        payload = self._json(req)
        recent = payload.get("recent_message_ids")
        self._recent_message_ids[source] = frozenset(
            str(item) for item in recent if str(item).strip()
        ) if isinstance(recent, list) else frozenset()
        return payload.get("cursor")

    def recent_message_ids(self, source: str) -> frozenset[str]:
        return self._recent_message_ids.get(source, frozenset())

    def set_cursor(self, source: str, cursor: str) -> None:
        from urllib.parse import quote
        req = urllib.request.Request(
            self.base_url + "/v1/source-cursors/" + quote(source, safe=""),
            data=json.dumps({"cursor": cursor}).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        self._json(req)

    def register_account(self, *, account_id: str, provider: str, display_name: str, can_read: bool = True, can_reply: bool = False, credential_ref: str = "") -> None:
        payload = {
            "id": account_id, "provider": provider, "display_name": display_name,
            "can_read": bool(can_read), "can_reply": bool(can_reply),
            "credential_ref": credential_ref, "enabled": True,
        }
        req = urllib.request.Request(
            self.base_url + "/v1/accounts", data=json.dumps(payload, ensure_ascii=False).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        self._json(req)

    def send_message(self, *, source: str, account_id: str, route_id: str, message_id: str, sender: str, body: str) -> None:
        payload = {
            "route_id": route_id,
            "account_id": account_id,
            "message": {"schema": "universal.inbox.message.v1", "source": source, "message_id": message_id, "sender": sender, "body": body},
        }
        req = urllib.request.Request(
            self.base_url + "/v1/messages", data=json.dumps(payload, ensure_ascii=False).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        self._json(req)

    def send(self, account: str, message: GmailMessage) -> None:
        source = _source(account)
        self.send_message(
            source=source, account_id=account, route_id="gmail-read-only",
            message_id=message.message_id, sender=message.sender,
            body=readable_message_body(message, account),
        )
        notify = getattr(self.notifier, "notify", None)
        if callable(notify):
            notify(account=account, source=source, message=message)


def accounts_from_file(path: str | Path) -> tuple[str, ...]:
    return tuple(line.strip() for line in Path(path).read_text().splitlines() if line.strip() and not line.lstrip().startswith("#"))


def poll_once(sink: UserIOIngressClient, readers: Mapping[str, HimalayaReader], *, limit: int) -> int:
    delivered, errors = 0, []
    for account, reader in readers.items():
        try:
            source = _source(account)
            cursor = sink.cursor(source)
            recent = getattr(sink, "recent_message_ids", lambda _source: frozenset())(source)
            messages, next_cursor = reader.poll(
                cursor, limit=limit, known_message_ids=frozenset(recent),
            )
            for message in messages:
                sink.send(account, message)
                delivered += 1
            if next_cursor is not None:
                sink.set_cursor(source, next_cursor)
        except Exception as error:
            errors.append(f"{account}: {error}")
            log.warning("gmail account %s poll failed: %s", account, error)
    if errors:
        raise RuntimeError("; ".join(errors))
    return delivered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    token = os.environ["USERIO_API_TOKEN"]
    binary = os.environ.get("USERIO_HIMALAYA_BIN", "/home/roomhacker/.cargo/bin/himalaya")
    accounts_file = os.environ.get("USERIO_GMAIL_ACCOUNTS_FILE", "/var/lib/universal-userio/gmail-accounts.txt")
    interval = float(os.environ.get("USERIO_GMAIL_POLL_INTERVAL_SECONDS", "60"))
    limit = int(os.environ.get("USERIO_GMAIL_POLL_LIMIT", "100"))
    snapshot_size = int(os.environ.get("USERIO_GMAIL_SNAPSHOT_SIZE", "500"))
    accounts = accounts_from_file(accounts_file)
    mailbox_addresses = himalaya_mailbox_addresses(
        os.environ.get("USERIO_HIMALAYA_CONFIG", "/home/roomhacker/.config/himalaya/config.toml"),
        accounts,
    )
    readers = {
        account: HimalayaReader(
            binary, account, snapshot_size=snapshot_size,
            mailbox_address=mailbox_addresses[account],
        )
        for account in accounts
    }
    notice_token = os.environ.get("USERIO_NOTICEPLACE_TOKEN", "").strip()
    notifier = NoticePlaceEmailNotifier(
        os.environ.get("USERIO_NOTICEPLACE_EVENT_URL", "http://127.0.0.1:8091/v1/events"),
        notice_token,
        project=os.environ.get("USERIO_NOTICEPLACE_PROJECT", "userio"),
        recipient=os.environ.get("USERIO_NOTICEPLACE_RECIPIENT", "me"),
    ) if notice_token else None
    sink = UserIOIngressClient(
        os.environ.get("USERIO_INGRESS_URL", "http://127.0.0.1:18093"), token,
        notifier=notifier,
    )
    logging.basicConfig(level=os.environ.get("USERIO_LOG_LEVEL", "INFO"))
    stop = threading.Event()
    if not args.once:
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: stop.set())
    failures = 0
    max_backoff = float(os.environ.get("USERIO_GMAIL_MAX_BACKOFF_SECONDS", "60"))
    while not stop.is_set():
        try:
            delivered = poll_once(sink, readers, limit=limit)
            if delivered:
                log.info("gmail ingress delivered %d message(s)", delivered)
            failures = 0
            if args.once:
                break
            stop.wait(interval)
        except Exception as error:
            if args.once:
                raise
            failures += 1
            delay = min(max_backoff, max(2.0, 2 ** min(failures, 6)))
            log.warning("gmail poll failed; retrying in %.1fs: %s", delay, error)
            stop.wait(delay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
