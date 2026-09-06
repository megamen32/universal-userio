"""Read-only Gmail ingress owned by Universal UserIO.

Polls allowlisted local Himalaya accounts, keeps per-user/source cursors in the
canonical UserIO database, and posts new messages through UserIO's HTTP ingress
so normal subscriptions and policy routing are preserved.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping



@dataclass(frozen=True, slots=True)
class GmailMessage:
    message_id: str
    fetch_id: str
    sender: str
    body: str


def _source(account: str) -> str:
    return "gmail" if account == "gmail" else f"gmail:{account}"


class HimalayaReader:
    def __init__(self, binary: str, account: str, *, mailbox: str = "Inbox", snapshot_size: int = 100) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", account):
            raise ValueError("invalid Himalaya account")
        self.binary, self.account, self.mailbox, self.snapshot_size = binary, account, mailbox, snapshot_size

    def _run(self, *args: str) -> Any:
        completed = subprocess.run(
            (self.binary, "-a", self.account, *args), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"himalaya {self.account} failed")
        return json.loads(completed.stdout)

    def poll(self, cursor: str | None, *, limit: int = 100) -> tuple[list[GmailMessage], str | None]:
        payload = self._run("--json", "envelope", "list", "-m", self.mailbox, "-p", "1", "-s", str(max(limit, self.snapshot_size)))
        envelopes = payload.get("envelopes") if isinstance(payload, dict) else None
        if not isinstance(envelopes, list):
            raise RuntimeError("invalid Himalaya envelope response")
        pending: list[dict[str, Any]] = []
        found = cursor is None
        for env in envelopes:
            if not isinstance(env, dict):
                continue
            message_id = str(env.get("message-id") or env.get("id") or "").strip()
            if cursor and message_id == cursor:
                found = True
                break
            if message_id:
                pending.append(env)
        if cursor and not found:
            raise RuntimeError(f"gmail cursor for {self.account} is outside bounded snapshot")
        selected = list(reversed(pending))[:limit]
        messages = [self._read(env) for env in selected]
        return messages, (messages[-1].message_id if messages else cursor)

    def _read(self, env: Mapping[str, Any]) -> GmailMessage:
        fetch_id = str(env.get("id") or "").strip()
        message_id = str(env.get("message-id") or fetch_id).strip()
        if not fetch_id or not message_id:
            raise RuntimeError("gmail envelope missing identity")
        payload = self._run("--backend", "imap", "--json", "message", "read", "-m", self.mailbox, fetch_id)
        parts = payload.get("parts") if isinstance(payload, dict) else None
        if not isinstance(parts, list):
            raise RuntimeError("invalid Himalaya message response")
        body = ""
        for key in ("html_body", "text_body"):
            indexes = payload.get(key) if isinstance(payload, dict) else None
            if not isinstance(indexes, list):
                continue
            chunks = []
            for index in indexes:
                if not isinstance(index, int) or index < 0 or index >= len(parts) or not isinstance(parts[index], dict):
                    continue
                value = parts[index].get("body")
                if isinstance(value, dict):
                    value = value.get("Text") or value.get("Html")
                if isinstance(value, str) and value.strip():
                    chunks.append(value.strip())
            if chunks:
                body = "\n\n".join(chunks)
                break
        senders = env.get("from") or []
        sender = ""
        if isinstance(senders, list) and senders and isinstance(senders[0], dict):
            sender = str(senders[0].get("email") or senders[0].get("name") or "")
        return GmailMessage(message_id, fetch_id, sender or message_id, body or str(env.get("subject") or ""))


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
