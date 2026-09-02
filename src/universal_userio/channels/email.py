"""Email channel adapter: SMTP send plus IMAP read (spec Phase 3).

Two transports exist for email: the Himalaya CLI outbox (Gmail, service side)
and this stdlib ``smtp-imap`` channel usable in-process from any project.
Capabilities: ``read``, ``send``, ``media``; everything else raises
:class:`AdapterNotSupported`.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from email import message_from_bytes
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from universal_userio.channels.core import (
    AdapterNotSupported,
    ChatMessage,
    ChatRef,
    ChatSummary,
    DownloadedMedia,
    MessageRef,
)


@dataclass(frozen=True, slots=True)
class EmailEnvConfig:
    """SMTP/IMAP settings resolved from ``USERIO_SMTP_*``/``USERIO_IMAP_*``."""

    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    imap_host: str
    imap_port: int
    imap_user: str
    imap_password: str
    sender: str

    @property
    def smtp_ssl(self) -> bool:
        return self.smtp_port == 465

    @property
    def imap_ssl(self) -> bool:
        return self.imap_port == 993


def email_env_config(env: Mapping[str, str] | None = None) -> EmailEnvConfig:
    """Resolve email settings; the password may be shared via ``USERIO_EMAIL_PASSWORD``."""

    source: Mapping[str, str] = os.environ if env is None else env

    def pick(*names: str) -> str | None:
        for name in names:
            value = source.get(name)
            if value:
                return value
        return None

    smtp_host = pick("USERIO_SMTP_HOST")
    imap_host = pick("USERIO_IMAP_HOST", "USERIO_SMTP_HOST")
    smtp_user = pick("USERIO_SMTP_USER", "USERIO_EMAIL_ADDRESS")
    imap_user = pick("USERIO_IMAP_USER", "USERIO_EMAIL_ADDRESS", "USERIO_SMTP_USER")
    shared_password = pick("USERIO_EMAIL_PASSWORD")
    smtp_password = pick("USERIO_SMTP_PASSWORD") or shared_password
    imap_password = pick("USERIO_IMAP_PASSWORD") or shared_password
    missing = [
        label
        for label, value in (
            ("smtp_host", smtp_host),
            ("smtp_user", smtp_user),
            ("smtp_password", smtp_password),
            ("imap_host", imap_host),
            ("imap_user", imap_user),
            ("imap_password", imap_password),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            "Email env config incomplete, missing: "
            + ", ".join(missing)
            + "; set USERIO_SMTP_HOST/USER/PASSWORD, USERIO_IMAP_HOST/USER/PASSWORD"
            " (or the shared USERIO_EMAIL_ADDRESS + USERIO_EMAIL_PASSWORD)"
        )
    return EmailEnvConfig(
        smtp_host=str(smtp_host),
        smtp_port=int(pick("USERIO_SMTP_PORT") or "587"),
        smtp_user=str(smtp_user),
        smtp_password=str(smtp_password),
        imap_host=str(imap_host),
        imap_port=int(pick("USERIO_IMAP_PORT") or "993"),
        imap_user=str(imap_user),
        imap_password=str(imap_password),
        sender=str(pick("USERIO_EMAIL_ADDRESS") or smtp_user),
    )


def _parse_email_message(uid: int, raw: bytes) -> dict[str, Any]:
    """Parse one RFC822 message into a plain row for the channel."""

    mail = message_from_bytes(raw)
    sender = parseaddr(str(mail.get("From", "")))[1]
    subject = str(mail.get("Subject", ""))
    date: datetime | None = None
    try:
        date = parsedate_to_datetime(str(mail.get("Date", "")))
    except (TypeError, ValueError):
        date = None
    body = ""
    attachments: list[tuple[str, str, bytes]] = []
    if mail.is_multipart():
        for part in mail.walk():
            filename = part.get_filename()
            if filename:
                attachments.append(
                    (
                        filename,
                        part.get_content_type(),
                        part.get_payload(decode=True) or b"",
                    )
                )
            elif part.get_content_type() == "text/plain" and not body:
                body = (part.get_payload(decode=True) or b"").decode(errors="replace")
    elif mail.get_content_type() == "text/plain":
        body = (mail.get_payload(decode=True) or b"").decode(errors="replace")
    return {
        "uid": uid,
        "sender": sender,
        "subject": subject,
        "date": date,
        "body": body,
        "attachments": attachments,
    }


def _quote_mailbox(mailbox: str) -> str:
    """IMAP SELECT needs quoted mailbox names with spaces or slashes."""

    if mailbox.startswith('"') and mailbox.endswith('"'):
        return mailbox
    escaped = mailbox.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


class SmtpImapTransport:
    """Blocking stdlib SMTP/IMAP transport; :class:`EmailChannel` runs it in a thread."""

    def __init__(self, config: EmailEnvConfig, *, timeout: float = 30.0) -> None:
        self.config = config
        self.timeout = timeout

    def _smtp(self):
        import smtplib
        from ssl import create_default_context

        if self.config.smtp_ssl:
            server = smtplib.SMTP_SSL(
                self.config.smtp_host, self.config.smtp_port, timeout=self.timeout
            )
        else:
            server = smtplib.SMTP(
                self.config.smtp_host, self.config.smtp_port, timeout=self.timeout
            )
            server.starttls(context=create_default_context())
        if self.config.smtp_password:
            server.login(self.config.smtp_user, self.config.smtp_password)
        return server

    def _imap(self):
        import imaplib
        from ssl import create_default_context

        if self.config.imap_ssl:
            client = imaplib.IMAP4_SSL(
                self.config.imap_host, self.config.imap_port, timeout=self.timeout
            )
        else:
            client = imaplib.IMAP4(self.config.imap_host, self.config.imap_port, timeout=self.timeout)
            client.starttls(create_default_context())
        client.login(self.config.imap_user, self.config.imap_password)
        return client

    def send(self, *, recipient: str, body: str, subject: str, in_reply_to: str | None = None) -> str:
        """Send one plain-text email and return its Message-ID header."""

        message = EmailMessage()
        message["From"] = self.config.sender
        message["To"] = recipient
        message["Subject"] = subject
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
            message["References"] = in_reply_to
        message.set_content(body)
        server = self._smtp()
        try:
            server.send_message(message)
        finally:
            server.quit()
        return str(message["Message-ID"])

    def fetch(self, *, mailbox: str = "INBOX", limit: int = 20) -> list[dict[str, Any]]:
        """Return the newest ``limit`` messages as parsed rows (newest first)."""

        client = self._imap()
        try:
            client.select(_quote_mailbox(mailbox))
            status, data = client.uid("search", None, "ALL")
            if status != "OK":
                raise RuntimeError(f"IMAP search failed: {status}")
            uids = (data[0] or b"").split()[-limit:]
            rows = []
            for uid in reversed(uids):
                status, parts = client.uid("fetch", uid, "(RFC822)")
                if status != "OK" or not parts or parts[0] is None:
                    continue
                raw = next((item[1] for item in parts if isinstance(item, tuple)), None)
                if raw is None:
                    continue
                rows.append(_parse_email_message(int(uid), raw))
            return rows
        finally:
            client.logout()

    def fetch_raw(self, *, uid: int, mailbox: str = "INBOX") -> dict[str, Any] | None:
        """Fetch and parse one message by IMAP UID."""

        client = self._imap()
        try:
            client.select(_quote_mailbox(mailbox))
            status, parts = client.uid("fetch", str(uid).encode(), "(RFC822)")
            if status != "OK":
                return None
            raw = next((item[1] for item in parts or [] if isinstance(item, tuple)), None)
            return None if raw is None else _parse_email_message(uid, raw)
        finally:
            client.logout()


def _peer(chat: ChatRef) -> str:
    return str(getattr(chat, "id", chat))


def _subject_for(text: str) -> str:
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    return (first_line[:57] + "...") if len(first_line) > 60 else (first_line or "userio")


class EmailChannel:
    """Universal email channel over an injected SMTP/IMAP transport."""

    platform = "email"
    capabilities = frozenset({"read", "send", "media"})

    def __init__(self, transport: SmtpImapTransport) -> None:
        self._transport = transport

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "EmailChannel":
        return cls(SmtpImapTransport(email_env_config(env)))

    def _to_message(self, row: dict[str, Any]) -> ChatMessage:
        attachments = row.get("attachments") or []
        first = attachments[0] if attachments else None
        return ChatMessage(
            chat_id=str(row["sender"]),
            id=int(row["uid"]),
            text=str(row.get("body") or ""),
            sender_id=str(row["sender"]),
            date=row.get("date"),
            media_type=first[1] if first else None,
            filename=first[0] if first else None,
            out=False,
        )

    async def list_chats(self) -> list[ChatSummary]:
        rows = await asyncio.to_thread(self._transport.fetch, limit=100)
        chats: dict[str, ChatSummary] = {}
        for row in rows:
            sender = str(row.get("sender") or "")
            if sender and sender not in chats:
                chats[sender] = ChatSummary(
                    id=sender, title=sender, username=None, kind="dm", unread_count=0
                )
        return list(chats.values())

    async def read_chat(self, chat: ChatRef, limit: int | None = 100) -> list[ChatMessage]:
        peer = _peer(chat)
        rows = await asyncio.to_thread(self._transport.fetch, limit=max(limit or 100, 100))
        matching = [row for row in rows if str(row.get("sender") or "") == peer]
        return [self._to_message(row) for row in matching][: limit or 100]

    async def read_message(self, chat: ChatRef, message_id: int) -> ChatMessage | None:
        peer = _peer(chat)
        row = await asyncio.to_thread(self._transport.fetch_raw, uid=message_id)
        if row is None or str(row.get("sender") or "") != peer:
            return None
        return self._to_message(row)

    async def download_media(self, chat: ChatRef, message: MessageRef) -> DownloadedMedia:
        message_id = int(getattr(message, "id", message))
        row = await asyncio.to_thread(self._transport.fetch_raw, uid=message_id)
        if row is None:
            raise ValueError(f"email message {message_id} not found")
        attachments = row.get("attachments") or []
        if not attachments:
            raise ValueError(f"email message {message_id} has no attachments")
        wanted = getattr(message, "filename", None)
        chosen = next((a for a in attachments if a[0] == wanted), attachments[0])
        return DownloadedMedia(
            chat_id=_peer(chat),
            message_id=message_id,
            data=chosen[2],
            mime_type=chosen[1],
            filename=chosen[0],
        )

    async def send_message(
        self, chat: ChatRef, text: str, *, reply_to: int | None = None
    ) -> ChatMessage:
        recipient = _peer(chat)
        in_reply_to: str | None = None
        if reply_to:
            parent = await asyncio.to_thread(self._transport.fetch_raw, uid=reply_to)
            in_reply_to = f"<{parent['uid']}@userio>" if parent else None
        await asyncio.to_thread(
            self._transport.send,
            recipient=recipient,
            body=text,
            subject=_subject_for(text),
            in_reply_to=in_reply_to,
        )
        # Outgoing emails get no IMAP UID here; 0 marks a locally originated copy.
        return ChatMessage(chat_id=recipient, id=0, text=text, sender_id=None, out=True)

    async def acknowledge_chat(self, chat: ChatRef) -> None:
        raise AdapterNotSupported("email channel does not support acknowledge_chat")

    async def forward_message(
        self, source_chat: ChatRef, message: MessageRef, target_chat: ChatRef
    ) -> ChatMessage:
        raise AdapterNotSupported("email channel does not support forward_message")

    async def delete_message(self, chat: ChatRef, message: MessageRef) -> bool:
        raise AdapterNotSupported("email channel does not support delete_message")

    async def edit_message(self, chat: ChatRef, message: MessageRef, text: str) -> ChatMessage:
        raise AdapterNotSupported("email channel does not support edit_message")

    async def react(self, chat: ChatRef, message: MessageRef, emoji: str) -> None:
        raise AdapterNotSupported("email channel does not support react")

    def typing(self, chat: ChatRef):
        raise AdapterNotSupported("email channel does not support typing")
