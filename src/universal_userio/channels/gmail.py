"""Gmail/Himalaya provider reader for UserIO-compatible runtimes.

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
