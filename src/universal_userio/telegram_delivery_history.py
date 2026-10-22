"""Exact, read-only Telegram history evidence for an uncertain text delivery."""
from __future__ import annotations

import math


def resolve_history(store, draft, *, conversation, account_ref, peer_id, reader,
                    user_id, expected_generation, expected_lease_owner):
    claim = store.draft_delivery_state(draft.id, user_id=user_id)
    # Telegram dates have whole-second precision; the local claim has fractions.
    start = math.floor(float(claim.get('sending_at') or claim.get('created_at') or 0))
    # Legacy drafts have only a creation lower bound. Never match an arbitrary
    # identical message from before the claim or from many days afterwards.
    end = start + (3600 if claim.get('sending_at') else 86400)
    context = store.telegram_delivery_history_context(
        draft.id, user_id=user_id, account_ref=account_ref, peer_id=peer_id, after=end)
    prior = claim.get('evidence', {}).get('history', {})
    cursor = prior.get('cursor_before_id', 0) if prior.get('generation') == expected_generation else 0
    before_id = cursor or context['before_id']
    evidence = {'account_ref': account_ref, 'peer_id': peer_id,
                'generation': expected_generation, 'window_start': start, 'window_end': end,
                'cursor_before_id': 0, 'reason': 'no_exact_match'}

    def unknown(reason):
        evidence['reason'] = reason
        store.mark_draft_delivery_state(
            draft.id, 'legacy_unknown', user_id=user_id,
            expected_generation=expected_generation, expected_lease_owner=expected_lease_owner)
        return {'resolution': 'unknown', 'draft_id': draft.id, 'status': claim['status'],
                'reason': 'provider_history_' + reason, 'retry_allowed': False,
                'evidence': {'history': evidence}}

    if context['competing_draft']:
        return unknown('competing_draft')
    page = reader(account_ref=account_ref, peer_id=peer_id, limit=100, before_id=before_id)
    if (not isinstance(page, dict) or page.get('account_id') != account_ref
            or str(page.get('peer_id')) != peer_id
            or page.get('provider_read_acknowledged') is not False):
        return unknown('receipt_identity_or_freshness_missing')
    ids = page.get('message_ids')
    if (not isinstance(ids, list) or len(ids) > 100
            or any(type(i) is not int or not 0 < i <= 2147483647 for i in ids)
            or len(set(ids)) != len(ids)):
        return unknown('receipt_message_ids_missing')
    matches = []
    dates = []
    for native_id in ids:
        # Only IDs in THIS provider response are eligible. Never accept a stale
        # cached body or an unscoped message from another connected account.
        mid = f'{account_ref}|{peer_id}:{native_id}'
        message = store.message(mid, source='telegram', user_id=user_id)
        if not message or message.get('conversation_id') != draft.conversation_id:
            continue
        timestamp = float(message.get('received_at') or 0)
        if not math.isfinite(timestamp):
            continue
        dates.append(timestamp)
        files = [a for a in message.get('attachments', [])
                 if a.get('kind') not in ('telegram_routing', 'webpage')]
        if (message.get('direction') == 'outgoing' and message.get('body') == draft.body
                and start <= timestamp <= end and not message.get('edited_at') and not files
                and not message.get('provider_reply_to') and not message.get('provider_thread_id')):
            matches.append(mid)
    evidence['fetched_message_count'] = len(ids)
    if len(matches) > 1:
        return unknown('duplicate_exact_matches')
    if len(matches) == 1:
        mid = matches[0]
        receipt = 'telegram-history:' + mid
        applied = store.approve_reconciled_delivery(
            draft.id, receipt, user_id=user_id, expected_generation=expected_generation,
            expected_lease_owner=expected_lease_owner, expected_text=draft.body,
            expected_conversation_id=draft.conversation_id)
        if not applied['applied']:
            return unknown('attempt_or_payload_changed')
        evidence.update(reason='exact_outgoing_match', matched_message_id=mid)
        return {'resolution': 'delivered', 'draft_id': draft.id, 'status': applied['status'],
                'receipt': receipt, 'reason': 'provider_history_exact_outgoing_match',
                'retry_allowed': False, 'evidence': {'history': evidence}}
    oldest = page.get('oldest_id')
    if (page.get('has_more') is True and type(oldest) is int and 0 < oldest <= 2147483647
            and (not before_id or oldest < before_id) and dates and min(dates) > start):
        evidence['cursor_before_id'] = oldest
        return unknown('scan_incomplete')
    return unknown('no_exact_match')
