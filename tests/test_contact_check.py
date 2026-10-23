"""Bounded contact truth: no external providers, sends, or read acknowledgments."""
import json
from dataclasses import replace
from unittest.mock import patch

import pytest

from test_messenger import case, dispatch
from universal_userio.contact_check import has_contacted
from universal_userio.messenger import Messenger
from universal_userio.mcp_surface import UserIOMcpSurface


def direct(case):
    store, service, provider, cid, source = case
    with store._connection:
        store._connection.execute("UPDATE conversations SET conversation_kind='direct' WHERE id=?", (cid,))
    return Messenger(store, service, replace(store.owner(), read_only=True)), provider.account_ref, cid


def page(ref, cid, messages=(), **kw):
    return dict(account_ref=ref, conversation_id=cid, messages=list(messages),
                next_cursor='', fresh=True, partial=True, mode='provider',
                provider_read_acknowledged=False, observed_at=42, **kw)


def outgoing(api, ref, cid):
    p = api.history(ref, cid)['messages'][0]
    p['source']['direction'] = 'outgoing'
    return p


@pytest.mark.parametrize('legacy', [True, False])
def test_real_stored_history_readonly_dispatch_and_redaction(case, legacy):
    api, ref, cid = direct(case)
    # A real normalized historical outgoing, not a draft or messenger action.
    case[1].receive(replace(case[4], message_id='-10042:78', direction='outgoing', received_at=22),
                    route_id='telegram', account_ref=ref)
    result = dispatch(case, 'has_contacted', dict(account_ref=ref, conversation_id=cid),
                      readonly=True, legacy=legacy)
    assert result['ok'] and result['status'] == 'CONTACTED'
    assert result['evidence'][0]['provider_message_id'] == '78'
    assert result['evidence'][0]['at'] == 22
    assert result['coverage']['partial'] and not result['fresh']
    assert result['scope']['principal_user_id'] == api.user
    assert not result['scope']['team_history_checked']
    assert not result['scope']['other_accounts_channels_checked']
    assert 'real source' not in json.dumps(result)
    assert 'text' not in json.dumps(result) and 'credential' not in json.dumps(result)
    assert case[2].calls == []
    assert api.capabilities(ref)['operations']['has_contacted']['allowed']
    tools = UserIOMcpSurface(case[0], case[1]).tool_manifest(principal=api.principal)['tools']
    assert any(t['name'] == 'userio.messenger.has_contacted' for t in tools)


def test_empty_partial_and_empty_stored_never_negative(case):
    api, ref, cid = direct(case)
    for refresh in (False, True):
        with patch.object(api, 'history', return_value=page(ref, cid)):
            r = has_contacted(api, ref, cid, refresh=refresh)
        assert r['status'] == 'UNKNOWN' and r['coverage']['partial']
        assert not r['coverage']['exhausted']


@pytest.mark.parametrize('status', ['draft', 'failed', 'unknown', 'accepted', 'queued', 'sending'])
def test_attempts_are_not_outgoing_evidence(case, status):
    api, ref, cid = direct(case)
    message = outgoing(api, ref, cid) | {'status': status}
    with patch.object(api, 'history', return_value=page(ref, cid, [message])):
        assert has_contacted(api, ref, cid)['status'] == 'UNKNOWN'


def test_pagination_positive_and_budget(case):
    api, ref, cid = direct(case)
    first = page(ref, cid); first['next_cursor'] = 'next'
    second = page(ref, cid, [outgoing(api, ref, cid)])
    with patch.object(api, 'history', side_effect=[first, second]) as read:
        r = has_contacted(api, ref, cid)
        assert r['status'] == 'CONTACTED' and r['coverage']['pages_checked'] == 2
        assert read.call_args.kwargs['cursor'] == 'next'
    with patch.object(api, 'history', return_value=first):
        assert has_contacted(api, ref, cid, max_pages=1)['reason'] == 'page_budget_exhausted'
        assert has_contacted(api, ref, cid)['reason'] == 'pagination_cycle'


def test_negative_requires_explicit_exhaustion_certificate_from_head(case):
    api, ref, cid = direct(case)
    certified = page(ref, cid)
    certified.update(partial=False, exhausted=True, coverage='accessible_provider_history')
    with patch.object(api, 'history', return_value=certified):
        r = has_contacted(api, ref, cid, refresh=True)
        assert r['status'] == 'NOT_CONTACTED' and r['coverage']['exhausted']
        assert has_contacted(api, ref, cid, refresh=False)['status'] == 'UNKNOWN'
    for key in ('partial', 'exhausted', 'coverage', 'fresh', 'mode'):
        invalid = certified.copy(); invalid.pop(key)
        with patch.object(api, 'history', return_value=invalid):
            assert has_contacted(api, ref, cid, refresh=True)['status'] == 'UNKNOWN'


def test_identity_scope_and_provider_failure(case):
    api, ref, cid = direct(case)
    with pytest.raises(KeyError):
        has_contacted(api, 'telegram:foreign', cid)
    with patch.object(api, 'history', side_effect=TimeoutError('private provider body')):
        r = has_contacted(api, ref, cid, refresh=True)
        assert r['status'] == 'UNKNOWN' and r['reason'] == 'history_unavailable'
        assert 'private' not in json.dumps(r)
    message = outgoing(api, ref, cid)
    message['source']['account_ref'] = 'telegram:foreign'
    with patch.object(api, 'history', return_value=page(ref, cid, [message])):
        assert has_contacted(api, ref, cid)['reason'] == 'message_scope_invalid'
    with case[0]._connection:
        case[0]._connection.execute("UPDATE conversations SET conversation_kind='group' WHERE id=?", (cid,))
    assert has_contacted(api, ref, cid)['reason'] == 'identity_not_exact_direct_contact'


def test_uncertain_outgoing_blocks_negative_certificate(case):
    api, ref, cid = direct(case)
    certified = page(ref, cid, [outgoing(api, ref, cid) | {'delivery_status': 'unknown'}])
    certified.update(partial=False, exhausted=True, coverage='accessible_provider_history')
    with patch.object(api, 'history', return_value=certified):
        assert has_contacted(api, ref, cid, refresh=True)['status'] == 'UNKNOWN'


def test_page_scope_and_ack_are_validated(case):
    api, ref, cid = direct(case)
    for field, value in [('account_ref', 'foreign'), ('provider_read_acknowledged', True)]:
        invalid = page(ref, cid, [outgoing(api, ref, cid)])
        invalid[field] = value
        with patch.object(api, 'history', return_value=invalid):
            assert has_contacted(api, ref, cid)['status'] == 'UNKNOWN'


@pytest.mark.parametrize('status', ['sending', 'sent', 'unknown'])
def test_durable_attempts_cannot_be_negative_or_positive_proof(case, status):
    api, ref, cid = direct(case)
    with case[0]._connection:
        case[0]._connection.execute('INSERT INTO drafts(user_id,id,conversation_id,body,status,created_at) '
                                   'VALUES(?,?,?,?,?,?)', (api.user, 'attempt', cid, 'private', status, 10))
    certified = page(ref, cid)
    certified.update(partial=False, exhausted=True, coverage='accessible_provider_history')
    with patch.object(api, 'history', return_value=certified):
        result = has_contacted(api, ref, cid, refresh=True)
        assert result['status'] == 'UNKNOWN'
        assert result['reason'] == 'outgoing_receipt_without_history_proof'
        assert result['evidence'] == []


@pytest.mark.parametrize('legacy', [True, False])
def test_chat_grants_preserved(case, legacy):
    from universal_userio.mcp_dispatch import UserIOToolDispatcher
    api, ref, cid = direct(case)
    surface = UserIOMcpSurface(case[0], case[1]) if legacy else UserIOToolDispatcher(case[0], case[1])
    principal = replace(api.principal, messenger_accounts=(ref,), messenger_grants=(
        {'account_ref': ref, 'access': 'read_only', 'chats': [{'conversation_id': cid, 'access': 'read_only'}]},))
    args = dict(account_ref=ref, conversation_id=cid)
    assert surface.dispatch('userio.messenger.has_contacted', args, principal=principal)['status'] == 'UNKNOWN'
    refused = surface.dispatch('userio.messenger.has_contacted', args | {'conversation_id': 'foreign'}, principal=principal)
    assert not refused['ok'] and refused['error'] == 'chat_or_account_outside_token_scope'


@pytest.mark.parametrize('args', [{'limit': True}, {'limit': 101}, {'max_pages': 11}, {'refresh': 'true'}])
def test_bound_validation(case, args):
    api, ref, cid = direct(case)
    with pytest.raises(ValueError):
        has_contacted(api, ref, cid, **args)
