# Dot Telegram mirror: production connection / delivery gaps

Observed: 2026-11-02 10:35 UTC. Status: open, runtime acceptance missing.

Current UserIO backend image PR9 started at 03:54 UTC. Existing dot-relay
journal was last updated 03:50 UTC and records 113 snapshot_read_failed
attempts; no later runtime progress is verified. Enabled owner-only binding
and a known allowed DM do not prove the mirror poller is active.

The existing Mac mini Chrome process is running; bounded read-only tab
inventory found no chatgpt.com/dots/ route. The protected policy required by
PR6 is absent at its default host path. An alternative container-local path
has not been proved or excluded. The original authorized dot URL and MCP
script coordinates are not in available non-secret evidence. Never guess
their values, change audience/owner, start a second poller or browser, or
bypass a rejected runtime read.

Confirmed code defect repaired: reverse-direction dot replies discarded
their original reply links. PR6 now binds native replies to exact durable
accepted provider receipts (also to verified Telegram-origin echoes),
preserves reply targets in all text chunks, and holds unknown/ambiguous
targets instead of sending unrelated plain text. 88 selected tests PASS.
This is a source fix, not deployed delivery.

Unresolved: recover exact original authorized dot room and protected policy,
verify installed source and unique poller, and prove real text/reply/image
delivery both ways with provider receipts and no duplicates. Dot-origin
attachment forwarding is not established by the text mirror.

No replay: all 232 journal outgoing entries are marked accepted, which is
not a provider delivery readback. Reconcile UNKNOWN by prior receipt; do not
reset cursor, replay accepted/UNKNOWN, or edit other owners' claims,
manual pauses, bot, voice, PR9 cleanup, proxy or broker.

Acceptance: same exact room and DM, one poller, unchanged access,
installed source hash and provider-confirmed current text/reply/image
events in both directions, with zero duplicates.
