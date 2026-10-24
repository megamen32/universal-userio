"""Metadata-only contact truth over authorized, normalized history.

This does not resolve identities or inspect drafts/action attempts. A negative
answer requires an explicit provider exhaustion certificate, never an empty page.
"""
from __future__ import annotations

import math
import re
import time


def _unreconciled_receipts(api, account_ref, conversation_id):
    """Receipt attempts cannot override missing historical message evidence."""
    with api.store._lock:
        db = api.store._connection
        if db.execute("SELECT 1 FROM drafts WHERE user_id=? AND conversation_id=? "
                      "AND status IN ('sending','sent','unknown') LIMIT 1",
                      (api.user, conversation_id)).fetchone():
            return True
        if api.store._table_exists('messenger_actions'):
            return db.execute("SELECT 1 FROM messenger_actions WHERE user_id=? "
                              "AND status IN ('sending','accepted','unknown') "
                              "AND json_extract(envelope,'$.account_ref')=? "
                              "AND json_extract(envelope,'$.conversation_id')=? "
                              "AND json_extract(envelope,'$.action') IN ('reply','file','sticker') LIMIT 1",
                              (api.user, account_ref, conversation_id)).fetchone() is not None
    return False


def _username_conversation(api, account_ref, username):
    """Read a strictly typed username; numeric input must never reach phone import."""
    if not isinstance(username, str) or not re.fullmatch(r'@?[A-Za-z][A-Za-z0-9_]{4,31}', username):
        raise ValueError('invalid_telegram_username')
    if api._account(account_ref)['provider'] != 'telegram':
        raise ValueError('telegram_account_required')
    if api.principal.messenger_grants is not None:
        grant = next((g for g in api.principal.messenger_grants if g['account_ref'] == account_ref), None)
        if grant is None or grant['chats'] is not None:
            raise PermissionError('account_scope_required_for_identity_history')
    try:
        provider = api._provider(account_ref)
        peer = provider.resolve_username(username.lstrip('@'))
        if (peer.get('account_ref') != account_ref
                or not re.fullmatch(r'[1-9][0-9]{0,18}', peer.get('peer_id', ''))):
            return None
        # Reuse the ingress key and local projection; no draft or provider chat is created.
        return api.store.ensure_contact_conversation('telegram', peer['peer_id'], user_id=api.user,
            account_ref=account_ref, peer_id=peer['peer_id'], conversation_kind='direct', route_id='telegram')
    except PermissionError:
        raise
    except Exception:
        return None


def has_contacted(api, account_ref, conversation_id='', *, telegram_username='', limit=100,
                  max_pages=5, refresh=False):
    api._allow(account_ref, 'history')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('limit_must_be_1_to_100')
    if type(max_pages) is not int or not 1 <= max_pages <= 10:
        raise ValueError('max_pages_must_be_1_to_10')
    if type(refresh) is not bool:
        raise ValueError('refresh_must_be_boolean')
    if bool(conversation_id) == bool(telegram_username):
        raise ValueError('exactly_one_conversation_or_telegram_username_required')
    if telegram_username and not refresh:
        raise ValueError('username_history_requires_refresh')
    conv = (_username_conversation(api, account_ref, telegram_username) if telegram_username
            else api._conversation(account_ref, conversation_id))
    identity_unavailable = conv is None
    conv = conv or {'source': 'telegram', 'id': '', 'peer_id': '', 'conversation_kind': 'unknown'}
    conversation_id = conv['id']
    result = {
        'schema': 'userio.contact-check.v1', 'status': 'UNKNOWN', 'reason': 'partial_history',
        'scope': {'principal_user_id': api.user, 'account_ref': account_ref, 'channel': conv['source'],
                  'conversation_id': conv['id'], 'peer_id': str(conv['peer_id']),
                  'identity': 'resolved_telegram_username' if telegram_username else 'exact_existing_conversation',
                  'history': 'accessible_provider_history' if refresh else 'retained_history',
                  'other_accounts_channels_checked': False,
                  'team_history_checked': False,
                  'deleted_inaccessible_history_checked': False},
        'evidence': [], 'coverage': {'pages_checked': 0, 'messages_checked': 0,
                                    'partial': True, 'exhausted': False, 'next_cursor': ''},
        'fresh': False, 'observed_at': None, 'checked_at': time.time(),
        'provider_read_acknowledged': False,
    }
    if identity_unavailable:
        result['reason'] = 'identity_unavailable'
        return result
    if conv['conversation_kind'] != 'direct' or not conv['peer_id']:
        result['reason'] = 'identity_not_exact_direct_contact'
        return result
    cursor, seen, all_fresh, uncertain_outgoing, all_provider = '', set(), True, False, True
    all_projection_complete = True
    for _ in range(max_pages):
        try:
            page = api.history(account_ref, conversation_id, limit=limit, cursor=cursor, refresh=refresh)
        except PermissionError:
            raise
        except Exception:
            result['reason'] = 'history_unavailable'
            result['fresh'] = False
            return result
        coverage = result['coverage']
        coverage['pages_checked'] += 1
        all_fresh = all_fresh and page.get('fresh') is True
        all_provider = all_provider and page.get('mode') == 'provider'
        all_projection_complete = all_projection_complete and page.get('projection_complete') is True
        result['fresh'] = all_fresh
        result['observed_at'] = page.get('observed_at')
        # Validate the normalized page before accepting either evidence or a
        # certificate. Provider text/errors are deliberately never returned.
        if (page.get('account_ref') != account_ref or page.get('conversation_id') != conv['id']
                or page.get('provider_read_acknowledged', False) is not False
                or not isinstance(page.get('messages'), list)
                or not isinstance(page.get('next_cursor'), str)):
            result['reason'] = 'history_scope_or_receipt_invalid'
            return result
        coverage['next_cursor'] = page['next_cursor']
        for message in page['messages']:
            coverage['messages_checked'] += 1
            if not isinstance(message, dict) or not isinstance(message.get('source'), dict):
                result['reason'] = 'message_scope_invalid'
                return result
            source = message.get('source') or {}
            if (source.get('account_ref') != account_ref
                    or source.get('conversation_id') != conv['id']
                    or str(source.get('peer_id')) != str(conv['peer_id'])
                    or source.get('channel') != conv['source']):
                result['reason'] = 'message_scope_invalid'
                return result
            if source.get('direction') not in {'incoming', 'outgoing'}:
                result['reason'] = 'message_direction_unknown'
                return result
            if source.get('direction') != 'outgoing':
                continue
            # Only normalized historical messages count. Status-bearing
            # pending/failed/draft/unknown records cannot become proof.
            if any(message.get(k) not in (None, 'sent', 'delivered', 'read', 'complete')
                   for k in ('status', 'delivery_status')):
                uncertain_outgoing = True
                continue
            if message.get('draft') or message.get('is_draft'):
                continue
            at = message.get('at')
            if (not source.get('message_id') or not source.get('provider_message_id')
                    or type(at) not in (int, float) or not math.isfinite(at) or at <= 0):
                uncertain_outgoing = True
                continue
            result.update(status='CONTACTED', reason='historical_outgoing_message')
            result['evidence'] = [{k: source[k] for k in (
                'account_ref', 'channel', 'conversation_id', 'message_id', 'provider_message_id')}
                | {'at': at, 'direction': 'outgoing', 'kind': 'normalized_history_message'}]
            return result
        next_cursor = page['next_cursor']
        coverage['next_cursor'] = next_cursor
        if not next_cursor:
            certified = (refresh and all_fresh and all_provider and all_projection_complete and not uncertain_outgoing
                         and page.get('partial') is False and page.get('exhausted') is True
                         and page.get('coverage') == 'accessible_provider_history')
            if certified:
                try:
                    if _unreconciled_receipts(api, account_ref, conversation_id):
                        result['reason'] = 'outgoing_receipt_without_history_proof'
                        return result
                except Exception:
                    result['reason'] = 'receipt_coverage_unavailable'
                    return result
            coverage.update(partial=not certified, exhausted=certified)
            if certified:
                result.update(status='NOT_CONTACTED', reason='declared_scope_exhausted')
            return result
        if next_cursor in seen:
            result['reason'] = 'pagination_cycle'
            return result
        seen.add(next_cursor)
        cursor = next_cursor
    result['reason'] = 'page_budget_exhausted'
    return result
