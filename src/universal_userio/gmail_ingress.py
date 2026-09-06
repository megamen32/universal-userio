"""Read-only Gmail ingress owned by Universal UserIO."""
from __future__ import annotations
import argparse, json, os, signal, threading, time, urllib.request
from pathlib import Path
from typing import Any, Mapping
from .channels.gmail import GmailMessage, HimalayaReader, _source

class UserIOIngressClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url, self.token = base_url.rstrip("/"), token

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
        return self._json(req).get("cursor")

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
        self.send_message(source=_source(account), account_id=account, route_id="gmail-read-only", message_id=message.message_id, sender=message.sender, body=message.body)


def accounts_from_file(path: str | Path) -> tuple[str, ...]:
    return tuple(line.strip() for line in Path(path).read_text().splitlines() if line.strip() and not line.lstrip().startswith("#"))


def poll_once(sink: UserIOIngressClient, readers: Mapping[str, HimalayaReader], *, limit: int) -> int:
    delivered = 0
    for account, reader in readers.items():
        source = _source(account)
        cursor = sink.cursor(source)
        messages, next_cursor = reader.poll(cursor, limit=limit)
        for message in messages:
            sink.send(account, message)
            delivered += 1
        if next_cursor is not None:
            sink.set_cursor(source, next_cursor)
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
    readers = {account: HimalayaReader(binary, account) for account in accounts_from_file(accounts_file)}
    sink = UserIOIngressClient(os.environ.get("USERIO_INGRESS_URL", "http://127.0.0.1:18093"), token)
    stop = threading.Event()
    if not args.once:
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: stop.set())
    try:
        while not stop.is_set():
            poll_once(sink, readers, limit=limit)
            if args.once:
                break
            stop.wait(interval)
    finally:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
