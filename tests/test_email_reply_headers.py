"""Offline RFC822 reply evidence; no SMTP/IMAP operations."""
from datetime import datetime, timezone

from universal_userio.channels.email import EmailChannel, _parse_email_message


def test_rfc822_reply_preserves_opaque_provider_ids_and_date():
    raw = (b'From: qa@example.test\r\nTo: mailbox@example.test\r\n'
           b'Message-ID: <reply.opaque@example.test>\r\n'
           b'In-Reply-To: <sent.Z@example.test>\r\n'
           b'Date: Wed, 28 Oct 2026 18:00:00 +0300\r\n'
           b'Content-Type: text/plain; charset=utf-8\r\n\r\nNo, thank you.\r\n')
    row = _parse_email_message('41', raw)
    assert row['in_reply_to'] == '<sent.Z@example.test>'
    channel = EmailChannel(object())
    message = channel._to_message(row)
    assert message.id == 41 and message.reply_to_msg_id is None
    assert message.provider_message_id == '<reply.opaque@example.test>'
    assert message.provider_reply_to_message_id == '<sent.Z@example.test>'
    assert message.date == datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)


def test_absent_or_ambiguous_reply_header_remains_unknown():
    channel = EmailChannel(object())
    for header in [b'', b'In-Reply-To: <one@example.test> <two@example.test>\r\n']:
        row = _parse_email_message('42', b'From: qa@example.test\r\n' + header + b'\r\nBody')
        message = channel._to_message(row)
        assert message.reply_to_msg_id is None
        assert message.provider_reply_to_message_id is None
