"""Bounded read-only Sent-copy verification for already claimed Gmail SMTP sends.

This never issues a send, append or retry. A unique RFC Message-ID in the
account's Sent mailbox is positive evidence of the exact outbound MIME copy.
Its absence is not proof of non-submission and must retain the UNKNOWN claim.
"""
from __future__ import annotations

import json
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr
from typing import Any


def _cmd(outbox: Any, argv: list[str], *, timeout: int = 15) -> bytes:
    result = outbox._runner(argv, capture_output=True, timeout=timeout,
                            check=False)
    if result.returncode:
        raise RuntimeError('Gmail Sent folder could not be read')
    data = result.stdout
    if isinstance(data, str):
        data = data.encode('utf-8')
    if not isinstance(data, bytes) or len(data) > 512 * 1024:
        raise RuntimeError('Gmail Sent readback is invalid or unbounded')
    return data



def resolve_native_sent_mailbox(outbox: Any, account: str) -> str | None:
    """Discover an exact native Sent mailbox; never trust a configured alias alone.

    A Gmail account can expose [Gmail]/Отправленные rather than the English
    [Gmail]/Sent Mail. Do not select arbitrary user folders whose names happen
    to contain "sent"/"отправл".
    """
    if not account or ':' in account:
        return None
    raw = _cmd(outbox, [outbox._binary, '--config', outbox._config,
                        '--account', account, 'mailbox', 'list', '--json'])
    try:
        data = json.loads(raw)
    except (UnicodeError, ValueError) as error:
        raise RuntimeError('Gmail mailbox inventory returned invalid JSON') from error
    rows = data.get('mailboxes') if isinstance(data, dict) else data
    if not isinstance(rows, list) or len(rows) > 500:
        raise RuntimeError('Gmail mailbox inventory is invalid')
    known = {'sent', 'sent mail', 'отправленные', 'gesendet', 'enviados',
             'envoyés', 'inviati'}
    candidates = []
    system = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get('name')
        if not isinstance(name, str) or not name or len(name) > 256:
            continue
        path = name.replace('\\', '/')
        components = path.casefold().split('/')
        if components[-1] not in known:
            continue
        candidates.append(name)
        if len(components) == 2 and components[0] in ('[gmail]', '[google mail]'):
            system.append(name)
    if len(system) == 1:
        return system[0]
    if len(system) > 1:
        return None
    if len(candidates) == 1:
        return candidates[0]
    return None



def _matches(raw: bytes, *, message_id: str, sender: str,
             recipient: str, body: str) -> bool:
    try:
        mail = BytesParser(policy=policy.default).parsebytes(raw)
        from_headers = mail.get_all('From', [])
        to_headers = mail.get_all('To', [])
        ids = mail.get_all('Message-ID', [])
        if not (len(ids) == len(from_headers) == len(to_headers) == 1):
            return False
        if str(ids[0]).strip() != '<' + message_id + '>':
            return False
        if len(getaddresses(from_headers)) != 1 or len(getaddresses(to_headers)) != 1:
            return False
        if (parseaddr(str(from_headers[0]))[1].casefold() != sender.casefold()
                or parseaddr(str(to_headers[0]))[1].casefold() != recipient.casefold()):
            return False
        if (mail.is_multipart() or mail.get_content_type() != 'text/plain'
                or mail.get_all('Cc') or mail.get_all('Bcc')
                or mail.get_all('Resent-To')):
            return False
        return mail.get_content().replace('\r\n','\n') == body + '\n'
    except (UnicodeError, ValueError, TypeError, KeyError, LookupError):
        return False


def reconcile_original_gmail_sent(outbox: Any, *, account: str, sender: str,
                                   recipient: str, draft_id: str,
                                   body: str) -> str | None:
    """Return canonical SMTP accepted/verified-copy receipt or keep UNKNOWN."""
    if not all(isinstance(x, str) and x for x in
               (account, sender, recipient, draft_id, body)):
        raise ValueError('Exact Gmail original identity is required')
    from_email = parseaddr(sender)[1]
    to_email = parseaddr(recipient)[1]
    if not from_email or not to_email:
        return None
    message_id = outbox.outgoing_message_id(account, draft_id)
    if not message_id or '/' in message_id or ':' in account:
        return None
    binary, config = outbox._binary, outbox._config
    if not binary or not config:
        return None
    mailbox = resolve_native_sent_mailbox(outbox, account)
    if mailbox is None:
        return None
    # Search by recipient at the provider, then inspect at most 12 bounded
    # candidate RFC messages. Failure or exhaustion is UNKNOWN, not rejection.
    cmd = [binary, '--config', config, '--account', account]
    raw = _cmd(outbox, cmd + ['envelope', 'search', '--mailbox', mailbox,
                             '--page', '1', '--page-size', '50',
                             '--json', 'to', to_email])
    try:
        envelopes = json.loads(raw)
    except (UnicodeError, ValueError) as error:
        raise RuntimeError('Gmail Sent search returned invalid JSON') from error
    if isinstance(envelopes, dict):
        envelopes = envelopes.get('envelopes')
    if not isinstance(envelopes, list) or len(envelopes) > 50:
        raise RuntimeError('Gmail Sent search returned invalid envelope list')
    matches = 0
    checked = 0
    for envelope in envelopes:
        if not isinstance(envelope, dict):
            continue
        uid = envelope.get('id')
        uid = str(uid) if type(uid) in (int, str) else ''
        # Never let an untrusted mailbox envelope supply CLI flags or paths.
        if not uid.isdecimal() or uid.startswith('0') or len(uid) > 15:
            continue
        if checked >= 12:
            break
        checked += 1
        candidate = _cmd(outbox, cmd + ['message', 'read', '--mailbox',
                                       mailbox, '--raw', uid])
        if _matches(candidate, message_id=message_id, sender=from_email,
                    recipient=to_email, body=body):
            matches += 1
            if matches > 1:
                return None
    if matches != 1:
        return None
    # This is SMTP acceptance with a verified Sent copy, not recipient delivery.
    return f'himalaya:{account}:{draft_id}:{message_id}:saved'
