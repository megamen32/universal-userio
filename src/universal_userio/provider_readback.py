"""Read-only verification of original provider sends. No retry/send function is used.

Every returned identity is positive, source-scoped provider evidence; missing or
truncated history never proves non-submission.  This module intentionally cannot
release an UNKNOWN claim or authorize a new outgoing operation.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
import uuid
from typing import Any, Callable


def stable_vk_random_id(user_id: str, account_ref: str, peer_id: str, draft_id: str) -> int:
    key = chr(0).join((user_id, account_ref, peer_id, draft_id))
    return (int.from_bytes(hashlib.sha256(key.encode()).digest()[:4], 'big') & 0x7fffffff) or 1


def stable_yandex_payload_id(user_id: str, draft_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, user_id + chr(0) + draft_id))


def _unique_provider_id(candidates: list[str]) -> str | None:
    """Reject duplicates rather than arbitrarily choosing the first."""
    return candidates[0] if len(candidates) == 1 else None


def vk_random_id_readback(client: Any, *, own_id: int, peer_id: int,
                          random_id: int, body: str, pages: int = 3) -> str | None:
    """Match the native VK random_id, original account, peer and exact payload."""
    if type(own_id) is not int or own_id <= 0 or type(peer_id) is not int or not peer_id:
        raise ValueError('Exact VK account and recipient are required')
    if type(random_id) is not int or random_id <= 0:
        raise ValueError('Stable VK random_id is required')
    matches: list[str] = []
    for offset in range(max(1, min(pages, 3))):
        rows = client.history(peer_id, count=100, offset=offset * 100)
        if not isinstance(rows, list):
            raise RuntimeError('Invalid VK read-only history')
        for row in rows:
            if not isinstance(row, dict) or row.get('peer_id') != peer_id:
                continue
            if row.get('from_id') != own_id or row.get('out') not in (1, True):
                continue
            if str(row.get('random_id') or '') != str(random_id) or row.get('text') != body:
                continue
            if row.get('attachments') or row.get('fwd_messages') or row.get('reply_message'):
                continue
            mid = row.get('id')
            if type(mid) is int and mid > 0:
                matches.append(str(mid))
        if len(rows) < 100:
            break
    return _unique_provider_id(matches)


def yandex_payload_readback(read: Callable[..., dict], account: dict, *,
                            peer_id: str, payload_id: str, body: str,
                            pages: int = 3) -> str | None:
    """Read Yandex native history and require the original ClientMessage.PayloadId."""
    if not peer_id or not payload_id or not account.get('guid'):
        raise ValueError('Pinned Yandex identity is required')
    matches: list[str] = []
    before = 0
    for _ in range(max(1, min(pages, 3))):
        page = read(account, chat_id=peer_id, before=before, limit=50)
        if not isinstance(page, dict) or not isinstance(page.get('chats'), list):
            raise RuntimeError('Invalid Yandex history response')
        rows = []
        for chat in page['chats']:
            if isinstance(chat, dict) and chat.get('ChatId') == peer_id:
                rows.extend(chat.get('Messages') or [])
        timestamps: list[int] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            server = item.get('ServerMessage') or {}
            info = server.get('ServerMessageInfo') or {}
            plain = (server.get('ClientMessage') or {}).get('Plain') or {}
            stamp = info.get('Timestamp')
            if type(stamp) is int and stamp > 0:
                timestamps.append(stamp)
            if (info.get('From', {}).get('Guid') != account['guid']
                    or type(stamp) is not int or stamp <= 0
                    or str(plain.get('PayloadId') or '') != payload_id):
                continue
            text = plain.get('Text')
            actual = text.get('MessageText') if isinstance(text, dict) else text
            if actual != body or info.get('LastEditTimestamp') or any(
                    key in plain for key in ('Image', 'MiscFile', 'Voice')):
                continue
            matches.append(str(stamp))
        if len(rows) < 50 or not timestamps:
            break
        oldest = min(timestamps)
        if before and oldest >= before:
            break
        before = oldest
    return _unique_provider_id(matches)


def matrix_transaction_readback(homeserver: str, token: str, *, room_id: str,
                                transaction_id: str, body: str,
                                runner: Callable[..., Any] | None = None,
                                pages: int = 3) -> str | None:
    """GET only, using the connected account's token and Matrix transaction ID."""
    from .channels.matrix import _matrix_open
    if not (homeserver.startswith('https://') or homeserver.startswith(('http://127.0.0.1:', 'http://localhost:'))) or not token or not room_id or not transaction_id:
        raise ValueError('Exact Matrix account, room and transaction are required')
    get = _matrix_open if runner is None else runner
    base = homeserver.rstrip('/')
    headers = {'Authorization': 'Bearer ' + token}

    def fetch(path: str) -> dict:
        req = urllib.request.Request(base + '/_matrix/client/v3/' + path,
                                     headers=headers, method='GET')
        try:
            with get(req, timeout=12) as response:
                raw = response.read(1024 * 1024 + 1)
        except Exception as exc:
            raise RuntimeError('Matrix read-only provider history unavailable') from exc
        if len(raw) > 1024 * 1024:
            raise RuntimeError('Matrix readback exceeded the response limit')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise RuntimeError('Invalid Matrix provider response')
        return value

    who = fetch('account/whoami')
    sender = who.get('user_id')
    if not isinstance(sender, str) or not sender.startswith('@'):
        raise RuntimeError('Matrix account identity unavailable')
    filt = urllib.parse.quote(json.dumps({'room': {'timeline': {'limit': 1}}}), safe='')
    sync = fetch('sync?timeout=0&filter=' + filt)
    cursor = sync.get('next_batch')
    if not isinstance(cursor, str) or not cursor:
        return None
    room = urllib.parse.quote(room_id, safe='')
    matches: list[str] = []
    for _ in range(max(1, min(pages, 3))):
        page = fetch('rooms/' + room + '/messages?from=' +
                     urllib.parse.quote(cursor, safe='') + '&dir=b&limit=100')
        chunk = page.get('chunk')
        if not isinstance(chunk, list):
            raise RuntimeError('Matrix history chunk unavailable')
        for event in chunk:
            if not isinstance(event, dict):
                continue
            content = event.get('content') or {}
            unsigned = event.get('unsigned') or {}
            event_id = event.get('event_id')
            if (event.get('type') == 'm.room.message' and event.get('sender') == sender
                    and event.get('room_id', room_id) == room_id
                    and content.get('msgtype') == 'm.text' and content.get('body') == body
                    and unsigned.get('transaction_id') == transaction_id
                    and isinstance(event_id, str) and event_id.startswith('$')
                    and 'm.relates_to' not in content):
                matches.append('matrix:' + event_id)
        cursor = page.get('end')
        if len(chunk) < 100 or not isinstance(cursor, str) or not cursor:
            break
    return _unique_provider_id(matches)



def max_client_id_readback(client: Any, *, peer_id: str,
                           own_id: str, client_id: int,
                           body: str, pages: int = 2) -> str | None:
    """Read MAX history then GET_MESSAGE for an exact original stable CID.

    GET_HISTORY can omit CID; the single-message response includes it.
    No text-only match is proof, and no request here is a provider mutation.
    """
    if (not peer_id or not own_id or not isinstance(body, str) or not body
            or type(client_id) is not int or not 0 < client_id < 2**53):
        raise ValueError('Pinned MAX original identity is required')
    matches = []
    cursor = None
    examined = set()
    for _ in range(max(1, min(pages, 2))):
        rows = client.history(peer_id, backward=100, before=cursor)
        if not isinstance(rows, list) or len(rows) > 100:
            raise RuntimeError('Invalid MAX provider history')
        timestamps = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            stamp = row.get('time')
            if type(stamp) is int and stamp > 0:
                timestamps.append(stamp)
            if str(row.get('sender') or '') != own_id or row.get('text') != body:
                continue
            if row.get('attaches') or row.get('link') or row.get('updateTime'):
                continue
            mid = str(row.get('id') or '')
            if not mid.isdecimal() or mid == '0' or mid in examined:
                continue
            examined.add(mid)
            if len(examined) > 8:
                return None
            native = client.get_message(chat_id=peer_id, message_id=mid)
            if not isinstance(native, dict):
                continue
            if (str(native.get('id') or '') != mid
                    or str(native.get('sender') or '') != own_id
                    or native.get('text') != body
                    or str(native.get('cid') or '') != str(client_id)
                    or native.get('attaches') or native.get('link')
                    or native.get('updateTime') or native.get('type') not in (None, 'USER')):
                continue
            matches.append(mid)
            if len(matches) > 1:
                return None
        if len(rows) < 100 or not timestamps:
            break
        oldest = min(timestamps)
        if cursor is not None and oldest >= cursor:
            break
        cursor = oldest
    return _unique_provider_id(matches)



def original_provider_receipt(service: Any, draft: Any, conversation: dict, *,
                              user_id: str) -> str | None:
    """Use only existing user-scoped credentials and read-only native APIs."""
    source = str(conversation.get('source') or '')
    account_ref = str(conversation.get('account_ref') or '')
    peer = str(conversation.get('peer_id') or '')
    if source == 'whatsapp':
        # Only personal source-bound QR accounts have a durable journal.
        # Old legacy bridge sends have no native ID and stay UNKNOWN.
        if not account_ref or ':account-' not in account_ref or not peer:
            return None
        matches = [a for a in service._store.accounts(user_id=user_id)
                   if a['id'] == account_ref and a['provider'] == 'whatsapp'
                   and a['enabled'] and 'read' in a['capabilities']
                   and str(a.get('credential_ref') or '').startswith('whatsapp-qr:')]
        if len(matches) != 1 or service.whatsapp_outbox is None:
            return None
        checker = getattr(service.whatsapp_outbox, 'original_send_status', None)
        if not callable(checker):
            return None
        state = checker(chat_id=peer, user_id=user_id,
                        account_ref=account_ref, attempt_id=draft.id)
        if state is None:
            return None
        if not isinstance(state, dict) or any(state.get(key) != value for key, value in (
                ('attempt_id', draft.id), ('account_ref', account_ref),
                ('chat_id', peer))):
            raise RuntimeError('WhatsApp original journal identity conflict')
        if state.get('state') != 'sent':
            return None
        message_id = state.get('message_id')
        if not isinstance(message_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', message_id):
            return None
        return message_id
    if source == 'max':
        claim = service._store.draft_delivery_state(draft.id, user_id=user_id)
        native = (claim.get('evidence') or {}).get('native_claim')
        if not isinstance(native, dict) or (
                native.get('version') != 1 or native.get('source') != 'max'
                or native.get('account_ref') != (account_ref or 'max')
                or native.get('peer_id') != peer):
            return None
        cid = int(hashlib.sha256(draft.id.encode()).hexdigest()[:13], 16)
        if native.get('native_id') != str(cid):
            return None
        client = service.max_client_for(conversation, user_id)
        from .max_ingress import _own_id
        own = _own_id(client)
        if not own:
            # The first read also initializes the provider's connected profile.
            client.history(peer, backward=1)
            own = _own_id(client)
        if not own or (getattr(client,'expected_user_id','') and
                       str(client.expected_user_id) != own):
            return None
        return max_client_id_readback(client,peer_id=peer,own_id=own,
                                      client_id=cid,body=draft.body)
    if source == 'gmail' or source.startswith('gmail:'):
        from .gmail_sent_reconciliation import reconcile_original_gmail_sent
        alias = 'gmail' if source == 'gmail' else source.partition(':')[2]
        candidates = [a for a in service._store.accounts(user_id=user_id)
            if a['provider']=='gmail' and a['credential_ref']=='himalaya:'+alias
            and a['enabled'] and 'read' in a['capabilities']]
        if len(candidates) != 1:
            return None
        account = candidates[0]
        if account_ref and account_ref not in (alias, str(account['id'])):
            return None
        if service.gmail_outbox is None:
            return None
        return reconcile_original_gmail_sent(service.gmail_outbox,account=alias,
            sender=str(account.get('display_name') or ''),
            recipient=str(conversation.get('sender') or ''),draft_id=draft.id,
            body=draft.body)
    if not account_ref or not peer:
        return None
    if source == 'vk' and account_ref.startswith('vk:'):
        from .account_connections import _vk_personal_client
        client, own_id = _vk_personal_client(service, conversation, user_id, for_send=False)
        if client.identity() != own_id:
            raise ValueError('VK credential/account identity mismatch')
        if not re.fullmatch(r'-?[1-9][0-9]*', peer):
            return None
        return vk_random_id_readback(client, own_id=own_id, peer_id=int(peer),
            random_id=stable_vk_random_id(user_id, account_ref, peer, draft.id),
            body=draft.body)
    if source == 'yandex':
        from .yandex.web import YandexWeb
        from .yandex.sync import read
        account = YandexWeb(service, user_id).account(account_ref)
        return yandex_payload_readback(read, account, peer_id=peer,
            payload_id=stable_yandex_payload_id(user_id, draft.id), body=draft.body)
    if source == 'matrix' or source.startswith('matrix:'):
        from .account_connections import ConnectorSecrets
        if source != 'matrix' and source != account_ref:
            return None
        account = service.contact_starter.account(user_id=user_id, account_ref=account_ref)
        reference = str(account.get('credential_ref') or '')
        if account.get('provider') != 'matrix' or not reference.startswith('connector:'):
            return None
        secret = ConnectorSecrets().load(reference, user_id=user_id, provider='matrix')
        if secret.get('account_id') != account_ref or secret.get('user_id') != user_id:
            return None
        txn = 'userio-' + hashlib.sha256(draft.id.encode()).hexdigest()[:32]
        return matrix_transaction_readback(str(secret.get('homeserver') or ''),
            str(secret.get('access_token') or ''), room_id=peer,
            transaction_id=txn, body=draft.body)
    return None
