---
phase: 13-source-bound-history-and-approved-replies
plan: 01
status: complete
subsystem: messaging
tags: [provenance, approval, media, receipts]
requires:
  - phase: 12-effective-channel-capabilities
    provides: Exact account and credential capabilities
provides:
  - Real source-bound native reply and durable same-provider-ID receipts
  - Bounded Telegram/MAX source/history/media reads with honest partial results
affects: [14-telegram-and-max-rich-operations, 15-native-secretary-parity-and-transport-retirement]
tech-stack:
  added: []
  patterns: [immutable source binding, durable approved actions, no ambiguous replay]
key-files:
  created: [src/universal_userio/messenger_relay.py, src/universal_userio/messenger_max.py]
  modified: [src/universal_userio/messenger.py]
key-decisions:
  - Shared contract acceptance is distinct from complete native MAX parity and retirement.
requirements-completed: [SRC-01, SRC-02, REPLY-01, REPLY-02]
duration: historical implementation; evidence reconciliation only
completed: 2026-10-12
---

# Native provenance, bounded media and immutable delivery receipts

**A real new Telegram source was read with its actual image and author, answered through the native SDK/UserIO route, and retained account-bound reply/edit receipts. MAX stored/native reads and exact media bytes are accepted independently.**

| Requirement | Accepted live evidence |
|---|---|
| SRC-01 | Exmanager `20261012-userio-native-vision-live.json`: actual account881/source18/image10, real author and native job succeeded; routing metadata no longer overwrites the image. [Quick261012-pxe](../../quick/261012-pxe-preserve-relay-media-when-routing-metada/VERIFICATION.md) preserves native source/media; group author capture and MAX correct owner/contact roles remain accepted v1.2. |
| SRC-02 | Exmanager `20261012-userio-readonly-native-read.json`: actual bounded TG3/MAX10 reads with partial flags. Retained `.tmp/messenger-v13-20261012/invocation-live-ready.json` has independently initialized TG/MAX heads, `has_more:false` and no provider read acknowledgement. Telegram90835-byte image and current MAX90258-byte sticker are retained by exact source/index; full unlimited history is not claimed. |
| REPLY-01 | Real native source18 led to one approved reply19, then three approved edits of the same native ID through source-bound actions. Whole-source/thread/media/expected-text binding regressions and prior refusal/unknown ledgers are retained. No old failed source6/12/14/16 was replayed, no private reader scope upgraded. |
| REPLY-02 | The native outcome records four accepted account/source-bound actions with separate durable receipts and providerID19; independent Telegram Helper readback matches the canonical final text. Source3477 media reconciliation and [MAX new-file verification](../14-telegram-and-max-rich-operations/14-new-file-VERIFICATION.md) preserve actual received bytes in UserIO. |

Exmanager outcome root:
`/home/roomhacker/agents-projects/exmanager/.agents/tasks/outcomes/`.
Private UserIO evidence is under `.tmp/messenger-v13-20261012/` and retained
quick-task proof directories. No credential/body copying or new live call
was required for this documentation reconciliation.

## Boundaries and preserved failures

This closes the shared source/history/approval contract, not native MAX final
delivery, reaction/sticker/typing parity or dedicated transport retirement.
Earlier Telegram model429, wrong scene interpretation, Markdown expected-text
failure, routing/media loss and MAX broken-pipe cases remain separate from
accepted later receipts. An uncertain write never becomes replay authority.
