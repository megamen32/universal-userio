---
type: quick
scope: readonly-user-surface-acceptance
status: complete
---

# Accept reserve-host call recordings in UserIO

Requested outcome: automatically imported reserve-host recordings are usable in the normal phone conversation, with independent caller/phone audio and both transcripts.

The recording importer and its deployment belong to the telephony owner. This slice changes only acceptance documentation. It must not create calls, send messages, change account/preferences, alter product source, or restart services.

Focused integration scenario: normal deployed site → phone account → existing recording → two players and transcripts → real mouse playback/progress → reload/reopen → native download of both WAVs → archive-byte comparison → owned browser/config/download cleanup. Expected consumer duration: 60 seconds; registered browser emergency budget: 600 seconds. Existing profile: userio-browser44, RAM high/max 1/2 GiB, swap 0, CPU 2, tasks 384, temp 128 MiB, host reserve 20 GiB. No source suites or nightly runs are required for this documentation-only slice.

Retain private identity, recording IDs, transcripts and screenshots exclusively under ignored `.tmp/reserve44-ui-20261014/`. Publish only this plan and its scoped summary. Preserve unrelated Telegram transport WIP.
