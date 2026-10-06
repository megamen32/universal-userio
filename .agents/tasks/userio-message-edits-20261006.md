# UserIO message-edit delivery — 2026-10-06

Status: implemented, tested, and **deployed to production** (2026-10-06
~22:40 MSK). Real API-level canary passed; canary rows removed.

## Production deployment record

- `/opt/userio-telegram-qr/server.mjs` refreshed from repo
  `deploy/telegram-qr-connector/server.mjs` (root:root 0644; previous file
  kept as `server.mjs.pre-edits-20261006`); `userio-telegram-ingress.service`
  restarted. Connector healthy: `live`, 20 chats, no errors.
- The Python store fix was already running: the `/opt/universal-userio`
  working-tree overlay (core restarted 21:56 MSK by the runtime-coordination
  slice) already carried the edited_at changes; no core restart was needed.
- **Incident during rollout:** the first restart crashed the worker with
  `SyntaxError: Named export 'EditedMessage' not found` — GramJS 2.26
  `events/index.js` exports only `Raw`/`NewMessage`/`NewMessageEvent`.
  Fixed in `e598304` by importing the builder from
  `telegram/events/EditedMessage.js` directly; reinstall + restart recovered
  in ~2 minutes (the gap was covered by reconciliation backfill; no canary
  was in flight).
- **Canary (production, then removed):** posted
  `...|canary-edits-20261006:777000` as «Начинаю проверку...», re-posted the
  same message id as «Найдено в топе: 3 (canary)» with `edited_at`. Result:
  first delivery `accepted=true`, edit `accepted=false`; ONE message row with
  the edited body and non-null `edited_at`; exactly one workspace event
  (`eligible=0` — new synthetic chat is policy-denied, so no triage, no
  drafts, no dispatcher activity). Canary rows deleted from
  `userio.sqlite3` afterwards.

## Reported defect

A YouTube-check bot edits its own Telegram status message
(«Начинаю проверку...» → «Найдено в топе: N»), but the UserIO mirror kept the
stale original forever. Two independent causes:

1. `deploy/telegram-qr-connector/server.mjs` subscribed only to GramJS
   `NewMessage`; edits (`UpdateEditMessage` / `UpdateEditChannelMessage`)
   never triggered a live push. The 5-minute reconciliation re-post did carry
   the new text, but:
2. `store.ingest` discarded any duplicate `(user_id, source, message_id)`
   unless the stored body was an attachment placeholder — so a re-delivered
   edited body was dropped on the floor.

## Delivered

- `contracts.py` / `adapters.py`: optional `edited_at` (unix seconds) on
  `InboxMessage`, parsed from `universal.inbox.message.v1` envelopes with
  strict validation; absent means "never edited".
- `store.py`: `messages.edited_at REAL` column (fresh schema + in-place
  `ALTER TABLE` migration). Duplicate re-delivery with a genuinely different
  non-placeholder body now rewrites the mirror body in place, records
  `edited_at` (provider timestamp when provided, else ingest time), and bumps
  `conversations.updated_at` — without creating a new workspace event, so
  agents/triage are not re-fired. Unchanged replays and placeholder
  enrichment keep their previous semantics.
- `store.conversation()` serializes `edited_at`, so HTTP/MCP consumers and the
  dashboard can render an «(изменено)» marker from the same payload.
- Connector: `EditedMessage` handler beside `NewMessage` (verified against the
  installed GramJS 2.26.22 — matches only `UpdateEdit*`, no double-post),
  envelopes carry `message.editDate`, and edits skip `debounceAgentDeliver`
  so a bot editing its status cannot reschedule agent delivery.
- Matrix: `MatrixReader` resolves `m.replace` edits to the ORIGINAL event id
  (`m.new_content.body`), so the mirror rewrites that message instead of
  appending `"* edited"` noise; malformed replace events are skipped.
- WhatsApp note: the Baileys connector (`universal-inbox` repo) is not
  touched — its edit protobuf enum cannot be verified without the deployed
  dependency tree. `chats.upsert` re-posts `lastMessage`, so the store fix
  already surfaces edits of the newest message there.

## Evidence

- `tests/test_message_edits.py` (8 tests), `tests/test_matrix_ingress.py`
  (+2), `tests/telegram_ingress_edits.test.mjs` (4): 14 pytest + 4 node on
  the edit path.
- Full suites: pytest `287 passed`, node `18 passed` (all ingress mjs tests),
  `node --check server.mjs` OK.

## Deployment notes

- Store migration is additive (`ALTER TABLE messages ADD COLUMN edited_at`);
  old databases upgrade in place at first start.
- Production pickup requires redeploying this checkout to `/opt/universal-userio`
  and restarting `universal-userio.service` plus
  `userio-telegram-ingress.service`. Deploy window belongs to the coordinated
  slice in `userio-topic-production-acceptance-20261006.md`; this slice only
  lands source on `origin/main`.
- Real-consumer canary after deploy: post a message from the check bot, edit
  it, confirm the mirror shows the edited text with `edited_at` set and
  exactly one workspace event.
