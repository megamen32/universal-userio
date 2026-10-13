# Telegram voice calls and reserve44 history

Requested outcome: the existing AI Secretary calls the owner's personal
Telegram account and converses itself; completed reserve44 phone recordings
appear in UserIO with separate audio tracks and transcripts.

Keep the existing accounts, provider sessions and voice configuration. Native
Telegram media runs on server100, beside the existing UserIO runtime, through
the protected gateway control seam. Only the existing Secretary relay performs
the five official voice RPCs. Stable admission and receipts prevent redial on
unknown outcomes; Stop prevents a call starting after cancellation.

Acceptance: one real answered Secretary-to-owner dialogue with understandable
audio both ways, durable receipt, two-track archive and UserIO transcripts.
Reserve44 acceptance: actual completed call visible in its ordinary UserIO
conversation, both players/transcripts survive reload and exact WAV downloads.

Source verification: fast unit, focused integration, slow nightly. This task
uses the first two only. Fast framing/receipt tests expected2s/max20s;
combined surface checks expected15s/max150s; gateway tests expected2s/max20s;
native paired media expected3s/max30s; offline install expected5s/max50s.
Native source jobs use the existing userio-secretary-seven100 adaptive profile,
high=max512MiB/CPU1/tasks128/swap0/IO10/temp256MiB/files20000, fresh host reserve20GiB.
Production workers inherit the existing finite gateway limits (2GiB/CPU2/tasks128).

Scope: UserIO call lifecycle/tools/audio/worker, narrow gateway dispatch and
reserve44 archive importer. Existing relay's client/session/source belong to
its owner. Preserve unrelated mirror/provider WIP.
