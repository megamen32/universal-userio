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
from email.utils import parseaddr
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping



@dataclass(frozen=True, slots=True)
class GmailMessage:
    message_id: str
    fetch_id: str
    sender: str
    body: str
    subject: str = ""
    mailbox_address: str = ""


_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "div", "footer", "h1", "h2",
    "h3", "h4", "h5", "h6", "header", "li", "main", "nav", "p", "pre",
    "section", "table", "td", "th", "tr", "ul", "ol",
})
_IGNORED_TAGS = frozenset({"head", "script", "style", "svg", "template"})
_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})


class _AccessibleHTMLText(HTMLParser):
    """Extract readable email text without exposing markup or tracking URLs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): (value or "") for key, value in attrs}
        style = re.sub(r"\s+", "", attributes.get("style", "")).lower()
        hidden = (
            tag in _IGNORED_TAGS
            or "hidden" in attributes
            or attributes.get("aria-hidden", "").strip().lower() == "true"
            or "display:none" in style
            or "visibility:hidden" in style
            or "mso-hide:all" in style
        )
        if self._ignored_depth:
            if tag not in _VOID_TAGS:
                self._ignored_depth += 1
            return
        if hidden:
            if tag not in _VOID_TAGS:
                self._ignored_depth = 1
            return
        if tag == "br" or tag in _BLOCK_TAGS:
            self._chunks.append("\n")
        if tag == "img":
            alt = attributes.get("alt", "").strip()
            if alt:
                self._chunks.extend((alt, "\n"))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        prior_depth = self._ignored_depth
        self.handle_starttag(tag, attrs)
        if self._ignored_depth > prior_depth:
            self._ignored_depth -= 1

    def handle_endtag(self, tag: str) -> None:
        if self._ignored_depth:
            self._ignored_depth -= 1
            return
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and data:
            self._chunks.append(data)

    def text(self) -> str:
        lines: list[str] = []
        for raw_line in "".join(self._chunks).replace("\xa0", " ").splitlines():
            line = re.sub(r"[\t \f\v]+", " ", raw_line).strip()
            if line:
                lines.append(line)
        return "\n".join(lines).strip()


def html_to_accessible_text(value: str) -> str:
    """Return human-readable text from an HTML email body."""
    parser = _AccessibleHTMLText()
    try:
        parser.feed(value)
        parser.close()
    except (AssertionError, ValueError):
        # HTMLParser is deliberately tolerant, but malformed vendor markup must
        # not prevent the rest of the mailbox from being ingested.
        pass
    return parser.text()


def mailbox_identity(message: GmailMessage, account_ref: str) -> str:
    mailbox = message.mailbox_address.strip()
    if mailbox:
        return mailbox
    alias = account_ref.strip()
    if not alias:
        raise ValueError("gmail message has neither mailbox address nor account ref")
    return f"account_ref:{alias}"


def readable_message_body(message: GmailMessage, account_ref: str = "") -> str:
    identity = mailbox_identity(message, account_ref)
    body = message.body.strip()
    subject = " ".join(message.subject.split()) or "Без темы"
    return (
        f"Почтовый аккаунт: {identity}\nТема: {subject}\n\n"
        f"{body or 'Письмо без текстового содержимого.'}"
    )


def himalaya_mailbox_addresses(path: str | Path, aliases: tuple[str, ...]) -> dict[str, str]:
    """Resolve the exact receiving address for each configured Himalaya alias."""
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError:
            return {alias: "" for alias in aliases}
    try:
        config = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {alias: "" for alias in aliases}
    accounts = config.get("accounts") or {}
    result: dict[str, str] = {}
    for alias in aliases:
        entry = accounts.get(alias) if isinstance(accounts, dict) else None
        if not isinstance(entry, dict):
            result[alias] = ""
            continue
        imap = entry.get("imap") or {}
        imap_user = (((imap.get("sasl") or {}).get("plain") or {}).get("username")
                     if isinstance(imap, dict) else None)
        address = str(entry.get("email") or imap_user or "").strip()
        if parseaddr(address)[1] != address or not re.fullmatch(r"[^\s@]+@[^\s@]+", address):
            address = ""
        result[alias] = address
    return result


def _source(account: str) -> str:
    return "gmail" if account == "gmail" else f"gmail:{account}"


class HimalayaReader:
    def __init__(
        self, binary: str, account: str, *, mailbox: str = "Inbox",
        snapshot_size: int = 500, mailbox_address: str = "",
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", account):
            raise ValueError("invalid Himalaya account")
        self.binary, self.account, self.mailbox, self.snapshot_size = binary, account, mailbox, snapshot_size
        self.mailbox_address = mailbox_address

    def _run(self, *args: str) -> Any:
        completed = subprocess.run(
            (self.binary, "-a", self.account, *args), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"himalaya {self.account} failed")
        return json.loads(completed.stdout)

    def poll(
        self, cursor: str | None, *, limit: int = 100,
        known_message_ids: set[str] | frozenset[str] = frozenset(),
    ) -> tuple[list[GmailMessage], str | None]:
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
            if cursor and (message_id == cursor or message_id in known_message_ids):
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
        for key in ("text_body", "html_body"):
            indexes = payload.get(key) if isinstance(payload, dict) else None
            if not isinstance(indexes, list):
                continue
            chunks = []
            for index in indexes:
                if not isinstance(index, int) or index < 0 or index >= len(parts) or not isinstance(parts[index], dict):
                    continue
                value = parts[index].get("body")
                is_html = key == "html_body"
                if isinstance(value, dict):
                    if key == "text_body" and value.get("Text"):
                        value = value["Text"]
                    elif value.get("Html"):
                        value = value["Html"]
                        is_html = True
                    else:
                        value = value.get("Text")
                if isinstance(value, str) and value.strip():
                    chunk = value.strip()
                    if is_html or re.search(r"<!doctype\s+html|<html(?:\s|>)", chunk, re.I):
                        chunk = html_to_accessible_text(chunk)
                    if chunk:
                        chunks.append(chunk)
            if chunks:
                body = "\n\n".join(chunks)
                break
        senders = env.get("from") or []
        sender = ""
        if isinstance(senders, list) and senders and isinstance(senders[0], dict):
            sender = str(senders[0].get("email") or senders[0].get("name") or "")
        subject = str(env.get("subject") or "").strip()
        return GmailMessage(message_id, fetch_id, sender or message_id, body, subject, self.mailbox_address)
