"""Typed identity and native terminal coverage on the real contact/history path."""
import io
import json
from dataclasses import replace
from unittest.mock import patch

import pytest

from test_messenger import case, dispatch
from universal_userio.contact_check import has_contacted
from universal_userio.contracts import InboxMessage
from universal_userio.messenger import Messenger
from universal_userio.messenger_telegram_mirror import TelegramMirrorProvider


def test_username_native_receipt_keeps_account_provenance(case, monkeypatch):
    monkeypatch.setenv('USERIO_API_TOKEN', 'fixture')
    ref = case[2].account_ref
    def read(request, timeout):
        assert json.loads(request.data) == {'account_id': ref, 'identifier': 'fixture_user'}
        return io.BytesIO(json.dumps({'ok': True, 'account_id': 'telegram:foreign',
            'peer_id': '987654', 'username': 'fixture_user'}).encode())
    provider = TelegramMirrorProvider(case[1], ref, case[0].default_user_id, opener=read)
    with pytest.raises(ValueError, match='identity'):
        provider.resolve_username('fixture_user')


def setup(case, monkeypatch, pages):
    store, service = case[:2]
    ref = case[2].account_ref
    monkeypatch.setenv('USERIO_API_TOKEN', 'fixture')
    conv = store.ensure_contact_conversation('telegram', '123456', account_ref=ref,
                                             peer_id='123456')
    calls = []

    def read(request, timeout):
        args = json.loads(request.data)
        calls.append(args)
        raw = pages.pop(0)
        for mid in raw['message_ids']:
            service.receive(InboxMessage('telegram', f'123456:{mid}', '123456', 'fixture', mid,
                peer_id='123456', conversation_kind='direct', direction='incoming',
                reconciliation=True), route_id='telegram', account_ref=ref)
        receipt = dict(account_id=ref, peer_id='123456', provider_read_acknowledged=False, scan=True)
        receipt.update(raw)
        return io.BytesIO(json.dumps(receipt).encode())

    service.messenger_providers[ref] = TelegramMirrorProvider(service, ref, store.default_user_id, opener=read)
    return Messenger(store, service, replace(store.owner(), read_only=True)), ref, conv['id'], calls


def native(ids=(), **kw):
    return dict(message_ids=list(ids), fetched=len(ids), oldest_id=min(ids, default=0),
                has_more=False, **kw)


def test_terminal_requires_native_empty_page_after_lossless_pages(case, monkeypatch):
    first = native([6, 5, 4]); first['has_more'] = True
    api, ref, cid, calls = setup(case, monkeypatch, [first, native([4]), native()])
    r = has_contacted(api, ref, cid, limit=2, max_pages=3, refresh=True)
    assert r['status'] == 'NOT_CONTACTED' and r['coverage']['exhausted']
    assert r['coverage']['pages_checked'] == 3
    assert [x['before_id'] for x in calls] == [0, 5, 4]
    assert not r['scope']['other_accounts_channels_checked']


@pytest.mark.parametrize('change', [{'fetched': 2}, {'oldest_id': 3}, {'scan': False}])
def test_lossy_or_unbound_page_cannot_be_repaired_by_later_empty(case, monkeypatch, change):
    first = native([4]); first.update(change)
    api, ref, cid, _ = setup(case, monkeypatch, [first, native()])
    r = has_contacted(api, ref, cid, max_pages=2, refresh=True)
    assert r['status'] == 'UNKNOWN' and not r['coverage']['exhausted']


def test_short_nonempty_page_is_not_terminal_and_budget_is_unknown(case, monkeypatch):
    api, ref, cid, _ = setup(case, monkeypatch, [native([4])])
    r = has_contacted(api, ref, cid, max_pages=1, refresh=True)
    assert r['status'] == 'UNKNOWN' and r['coverage']['next_cursor']


def test_empty_native_head_is_certified_for_only_this_account(case, monkeypatch):
    api, ref, cid, _ = setup(case, monkeypatch, [native()])
    r = has_contacted(api, ref, cid, max_pages=1, refresh=True)
    assert r['status'] == 'NOT_CONTACTED' and r['fresh']


def test_username_without_prior_chat_uses_read_resolver_and_local_projection(case):
    store, service = case[:2]; ref = case[2].account_ref
    peer = {'account_ref': ref, 'peer_id': '987654'}
    before = store._connection.execute('SELECT COUNT(*) FROM drafts').fetchone()[0]
    with patch.object(case[2], 'resolve_username', return_value=peer, create=True) as resolve:
        r = dispatch(case, 'has_contacted', dict(account_ref=ref,
            telegram_username='fixture_user', refresh=True, max_pages=1), readonly=True)
    assert r['ok'] and r['status'] == 'UNKNOWN'
    resolve.assert_called_once_with('fixture_user')
    conv = store.conversation(r['scope']['conversation_id'])
    assert conv['peer_id'] == '987654' and conv['account_ref'] == ref
    assert r['scope']['identity'] == 'resolved_telegram_username'
    assert store._connection.execute('SELECT COUNT(*) FROM drafts').fetchone()[0] == before
    assert case[2].calls == []


@pytest.mark.parametrize('username', ['123456789', '+123456789', 't.me/fixture_user', '12345abc', ''])
def test_username_never_enters_phone_or_untyped_branch(case, username):
    api = Messenger(case[0], case[1], case[0].owner())
    with patch.object(case[2], 'resolve_username', create=True) as resolve:
        with pytest.raises(ValueError):
            has_contacted(api, case[2].account_ref, telegram_username=username, refresh=True)
        resolve.assert_not_called()


def test_resolver_foreign_account_is_not_materialized(case):
    api = Messenger(case[0], case[1], case[0].owner())
    peer = {'account_ref': 'telegram:foreign', 'peer_id': '987654'}
    with patch.object(case[2], 'resolve_username', return_value=peer, create=True):
        r = has_contacted(api, case[2].account_ref, telegram_username='fixture_user', refresh=True)
    assert r['status'] == 'UNKNOWN' and not r['scope']['conversation_id']


def test_chat_grant_cannot_resolve_new_username(case):
    store, service = case[:2]; ref = case[2].account_ref
    principal = replace(store.owner(), messenger_grants=({'account_ref': ref, 'access': 'read',
        'chats': [{'conversation_id': case[3], 'access': 'read'}]},))
    with patch.object(case[2], 'resolve_username', create=True) as resolve:
        with pytest.raises(PermissionError):
            has_contacted(Messenger(store, service, principal), ref,
                          telegram_username='fixture_user', refresh=True)
        resolve.assert_not_called()


@pytest.mark.parametrize('field,value', [('account_id', 'telegram:foreign'),
    ('peer_id', '999999'), ('provider_read_acknowledged', True)])
def test_foreign_or_ack_native_empty_is_never_certified(case, monkeypatch, field, value):
    # The real provider parser rejects a forged terminal receipt before certification.
    api, ref, cid, _ = setup(case, monkeypatch, [native(**{field: value})])
    assert has_contacted(api, ref, cid, refresh=True)['status'] == 'UNKNOWN'


def test_missing_prior_projection_certificate_keeps_unknown(case):
    api = Messenger(case[0], case[1], case[0].owner())
    with case[0]._connection:
        case[0]._connection.execute("UPDATE conversations SET conversation_kind='direct' WHERE id=?", (case[3],))
    base = dict(account_ref=case[2].account_ref, conversation_id=case[3], messages=[],
        fresh=True, mode='provider', provider_read_acknowledged=False, observed_at=42)
    first = dict(base, next_cursor='older', partial=True)
    final = dict(base, next_cursor='', projection_complete=True, partial=False,
                 exhausted=True, coverage='accessible_provider_history')
    with patch.object(api, 'history', side_effect=[first, final]):
        assert has_contacted(api, case[2].account_ref, case[3], refresh=True)['status'] == 'UNKNOWN'


def test_repeated_native_boundary_never_certifies(case, monkeypatch):
    api, ref, cid, _ = setup(case, monkeypatch, [native([4]), native([4])])
    r = has_contacted(api, ref, cid, refresh=True, max_pages=2)
    assert r['status'] == 'UNKNOWN' and r['reason'] == 'history_unavailable'
