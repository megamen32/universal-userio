"""SMTP receipts and read-only reconciliation; every transport is offline."""
import asyncio
from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from universal_userio.channels.email import EmailChannel, SmtpImapTransport, _parse_email_message, email_env_config

def test_smtp_wire_has_the_returned_message_id_and_date(monkeypatch):
    config=email_env_config({'USERIO_SMTP_HOST':'smtp.example.test','USERIO_IMAP_HOST':'imap.example.test','USERIO_EMAIL_ADDRESS':'qa@example.test','USERIO_EMAIL_PASSWORD':'test'})
    transport=SmtpImapTransport(config)
    server=Mock();server.send_message.return_value={}
    monkeypatch.setattr(transport,'_smtp',lambda:server)
    receipt=transport.send(recipient='qa@example.test',body='safe test',subject='test')
    message=server.send_message.call_args.args[0]
    assert receipt.startswith('<') and receipt.endswith('>') and receipt==message['Message-ID']
    assert message['Date']
    server.send_message.assert_called_once()

def test_operator_send_preserves_smtp_receipt_without_claiming_replay_support():
    transport=SimpleNamespace(send=Mock(return_value='<accepted@example.test>'))
    channel=EmailChannel(transport)
    result=asyncio.run(channel.send_operator_message('qa@example.test','safe test',idempotency_key='test-row'))
    assert result.id=='<accepted@example.test>' and result.chat_id=='qa@example.test'
    assert 'idempotent_send' not in channel.capabilities
    transport.send.assert_called_once()

@pytest.mark.parametrize('change',[None,'body','sender','recipient','message_id'])
def test_confirm_receipt_reads_only_exact_sender_recipient_body_and_message_id(change):
    message=EmailMessage();message['From']='qa@example.test';message['To']='qa@example.test';message['Message-ID']='<delivered@example.test>'
    message.set_content('safe test\nsecond line')
    row=_parse_email_message(108,message.as_bytes())
    if change=='body':row['body']='different text\n'
    if change=='sender':row['sender']='other@example.test'
    if change=='recipient':row['recipients']=['other@example.test']
    if change=='message_id':row['message_id']='<other@example.test>'
    transport=SimpleNamespace(config=SimpleNamespace(sender='qa@example.test'),
        search_uid_by_message_id=Mock(return_value=108),fetch_raw=Mock(return_value=row),send=Mock())
    channel=EmailChannel(transport)
    if change:
        with pytest.raises(ValueError):asyncio.run(channel.confirm_operator_receipt('qa@example.test','safe test\nsecond line','<delivered@example.test>'))
    else:
        result=asyncio.run(channel.confirm_operator_receipt('qa@example.test','safe test\nsecond line','<delivered@example.test>'))
        assert result.id=='<delivered@example.test>'
    transport.send.assert_not_called()
