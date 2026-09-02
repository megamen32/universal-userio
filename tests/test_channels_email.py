"""Phase 3 tests: smtp-imap email channel (offline, fake transport)."""

from __future__ import annotations

import asyncio

import pytest
from email.message import EmailMessage

from universal_userio.channels.core import AdapterNotSupported
from universal_userio.channels.email import (
    EmailChannel,
    _parse_email_message,
    email_env_config,
)

FULL_ENV = {
    "USERIO_SMTP_HOST": "smtp.example.com",
    "USERIO_SMTP_USER": "bot@example.com",
    "USERIO_SMTP_PASSWORD": "secret",
    "USERIO_IMAP_HOST": "imap.example.com",
    "USERIO_IMAP_USER": "bot@example.com",
    "USERIO_IMAP_PASSWORD": "secret",
}

ROW = {
    "uid": 5,
    "sender": "lead@example.org",
    "subject": "hi",
    "date": None,
    "body": "hello",
    "attachments": [("doc.pdf", "application/pdf", b"pdf-bytes")],
}


class FakeTransport:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.sent: list[tuple[str, str, str, str | None]] = []

    def send(self, *, recipient: str, body: str, subject: str, in_reply_to: str | None = None) -> str:
        self.sent.append((recipient, body, subject, in_reply_to))
        return "<id-1@example.com>"

    def fetch(self, *, mailbox: str = "INBOX", limit: int = 20) -> list[dict]:
        return self.rows[:limit]

    def fetch_raw(self, *, uid: int, mailbox: str = "INBOX") -> dict | None:
        return next((row for row in self.rows if row["uid"] == uid), None)


def test_email_env_config_defaults_and_shared_password() -> None:
    config = email_env_config(
        {
            "USERIO_EMAIL_ADDRESS": "bot@example.com",
            "USERIO_EMAIL_PASSWORD": "secret",
            "USERIO_SMTP_HOST": "smtp.example.com",
            "USERIO_IMAP_HOST": "imap.example.com",
        }
    )
    assert config.smtp_port == 587 and not config.smtp_ssl
    assert config.imap_port == 993 and config.imap_ssl
    assert config.sender == "bot@example.com"

    with pytest.raises(ValueError, match="smtp_host"):
        email_env_config({"USERIO_SMTP_USER": "x"})


def test_parse_email_message_multipart_with_attachment() -> None:
    message = EmailMessage()
    message["From"] = "Lead <lead@example.org>"
    message["To"] = "bot@example.com"
    message["Subject"] = "Question"
    message["Date"] = "Thu, 03 Sep 2026 00:00:00 +0300"
    message.set_content("hello body")
    message.add_attachment(
        b"pdf-bytes", maintype="application", subtype="pdf", filename="doc.pdf"
    )

    row = _parse_email_message(77, message.as_bytes())

    assert row["sender"] == "lead@example.org"
    assert "hello body" in row["body"]
    assert row["attachments"][0][0] == "doc.pdf"
    assert row["attachments"][0][2] == b"pdf-bytes"
    assert row["date"] is not None


def test_channel_read_side() -> None:
    channel = EmailChannel(FakeTransport([ROW]))  # type: ignore[arg-type]

    chats = asyncio.run(channel.list_chats())
    assert [chat.id for chat in chats] == ["lead@example.org"]

    messages = asyncio.run(channel.read_chat("lead@example.org"))
    assert [message.id for message in messages] == [5]
    assert messages[0].text == "hello"
    assert messages[0].filename == "doc.pdf"

    single = asyncio.run(channel.read_message("lead@example.org", 5))
    assert single is not None and single.id == 5
    assert asyncio.run(channel.read_message("other@example.org", 5)) is None


def test_channel_send_and_media() -> None:
    transport = FakeTransport([ROW])
    channel = EmailChannel(transport)  # type: ignore[arg-type]

    sent = asyncio.run(channel.send_message("lead@example.org", "first line of reply\nmore"))
    assert sent.out is True
    assert sent.chat_id == "lead@example.org"
    assert transport.sent == [
        ("lead@example.org", "first line of reply\nmore", "first line of reply", None)
    ]

    media = asyncio.run(channel.download_media("lead@example.org", type("M", (), {"id": 5, "filename": "doc.pdf"})()))
    assert media.data == b"pdf-bytes"
    assert media.filename == "doc.pdf"


def test_unsupported_operations_raise() -> None:
    channel = EmailChannel(FakeTransport([]))  # type: ignore[arg-type]
    with pytest.raises(AdapterNotSupported):
        asyncio.run(channel.edit_message("a@example.org", 1, "x"))
    with pytest.raises(AdapterNotSupported):
        asyncio.run(channel.delete_message("a@example.org", 1))
    with pytest.raises(AdapterNotSupported):
        channel.typing("a@example.org")


def test_from_env_builds_channel() -> None:
    channel = EmailChannel.from_env(FULL_ENV)
    assert channel.platform == "email"
    assert channel.capabilities == frozenset({"read", "send", "media"})
