---
phase: 10-timely-notifications
plan: 01
subsystem: notifications
requirements-completed: [NOTIFY-01, WORK-01, MAIL-01]
tags: [fast-agent, telegram, mail, noticeplace]
completed: 2026-10-12
---

# Timely work-chat and email notifications

**Actual incoming work messages and mail now produce delivered Russian notifications explaining the source, substance and next action.**

## Delivered behavior

The maintained dispatcher refreshes its existing loaded credential and polls one durable deep-analysis lane without blocking ordinary claims. The immutable startup watermark and pending cards remain intact. Notice Place uses its required human-request idempotency key; an original conflict is no longer hidden by a subsequent missing lookup. No new daemon, credential copy or provider session was introduced.

Telegram Helper identified the owner's work folders. The owner chose explicitly added chats, rather than the folder's broad “all groups” rule. Existing policy APIs now explicitly allow 32 real conversations; two empty direct conversations already allow future messages through the unchanged direct default. Four bot entries retain the separate bot restriction. Personal/test groups, hard exclusions, other accounts and historical ineligible events are preserved. Missing conversations were registered from one actual provider message through normal reconciliation, without making historical analysis eligible.

The owner explicitly chose notifications for tasks and important messages: their existing importance threshold changed from 0.85 to 0.5; minimum confidence remains 0.75. No other user's settings changed. Ordinary chatter and marketing can remain silent while their analysis is retained.

Mail notifications originate from the analysis dispatcher, not a premature subject-only ingress notice. Cards retain bounded substantive summary and action, exact receiving mailbox, and a timezone-aware Moscow deadline. Explicit requests to check/confirm are actions; the native prompt distinguishes Moscow time from UTC.

## Actual consumer evidence

- An actual newly received work-group question after the allow rule was analysed with correct author/owner roles, specific subject and next action, then delivered through Notice Place. Earlier legitimate work delivery is preserved separately.
- An actual controlled message between registered owned Gmail accounts was automatically received, analysed once and delivered. The visible Telegram card contains sender, receiving mailbox, substantive facts, a concrete confirmation action and 12 October 2026, 13:00 Moscow deadline. The same notification arrived back through the real Telegram mirror. Independent simulated-reader review passed clarity/content.
- First control mails remained below the previous importance threshold and were not reanalysed or resent. They exposed the premature ingress notice and timezone mistake; their raw results remain separate.
- A controlled Yandex-bound message was definitively rejected by the recipient SMTP server as suspected spam before reaching its inbox. Its genuine Gmail bounce was automatically analysed and delivered. This does not prove new incoming Yandex delivery; no provider spam policy was bypassed.

Private evidence: `.tmp/notification-recovery-20261012/{live-49077,final-live-mail-consumer,owner-notification-preference,work-chat-policy-result}.json`; provider intents/results and SMTP refusal are retained alongside them. Notification bodies, contact lists, credentials and personal addresses are not copied into this public summary.

## Mail synchronization repair

Real Yandex full/date-sorted header listing timed out, while stable native IMAP UID search and exact header fetch worked. The maintained Himalaya 2.0 reader now snapshots UIDs, fetches the head first and only necessary batches of ten, and stops at the durable message anchor. Deletion cannot shift offsets and skip a remaining message. Limits, oldest-first ingestion and partial-failure refusal remain. Sustained normal polling preserved cursors without duplicate import or new authentication.

Published source: UserIO b083218, 6f3e53f, 33ca76a, a35b4c8, 4b45b79 and 1aa9430; Hermes 54c5870, 054a2e2 and dc0f62e. Exact installed manifests and source hashes are in the private recovery artifacts.

## Validation ledger

| Purpose / detected defect | Category | Result | Expected / maximum |
|---|---|---|---|
| Mail actor/context, UTC offset, substantive prompt, stable UID cursor / premature notice and slow or shifting metadata lists | fast unit | 35 passed, 2.30s; final head delta 23 passed, 0.29s | 10s / 60s |
| Dispatcher durable lane, credential/error/idempotency protocol, card content / queue starvation and hidden conflict | focused integration | 85 passed, 3.77s | 10s / 45s |
| Normal work ingestion and notification / real author and delivery | focused integration | real incoming and native delivery captured | 120s / 480s |
| Owned mail provider → UserIO → native analysis → delivered notification / source, summary, action, deadline | focused integration | actual complete path captured in 67s observation window | 120s / 480s |

The combined ordinary source release stayed below 180 seconds including setup. No slow nightly run, old provider send, browser canary or model intent was replayed.

## Additional Yandex live acceptance

Direct owner request: Gmail→Yandex arrived in maintained Inbox49346 and produced delivered notice6623. Exact current receiver caption fixed in Hermes c4a14a2 after a real mismatch; narrow RED/86GREEN5.95s. After install, separate correction-validation mail49363 produced correct delivered card6624, actual Telegram mirror49367, substantive explanation/action, exact receiving address. Both messages analyzed once; no historical send or inference replay. Final proof `.tmp/notification-recovery-20261012/yandex-label-final-acceptance.json`. This closes positive Yandex delivery previously not proved, preserving older SMTP rejection history.
