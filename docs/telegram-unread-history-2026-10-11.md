# Telegram unread counts and paged history, 2026-10-11

UserIO now takes the unread count from the provider's authenticated dialog metadata. Local stored message counts are not full Telegram unread totals. The native per-account count is nullable until a provider snapshot is available, and stale snapshots cannot overwrite a newer timestamp. Zero is a valid provider count. UserIO does not issue read receipts.

The Telegram ingress performs lightweight, bounded native unread snapshots for up to 100 dialogs (configurable, capped at 200) at startup and approximately every five minutes. This uses dialog metadata, not a replay of all messages. New inbound messages and provider read events also schedule a debounced per-dialog native count refresh, rate-limited to at most once every 15 seconds per chat. The existing bounded recent-message reconciliation continues independently. Individual chat reads request native metadata and recent messages on demand.

For older history, the read-only `userio.channels.read` MCP tool supports `channel="telegram", chat_id, before_id, history_limit`. The bridge uses Telegram's exclusive offset cursor with a maximum of 200 per request; MCP limits individual pages to 100. Freshness, next cursor, and more-available status are explicit. Each authenticated MCP chat read advances a sequential scan if its page follows the saved cursor, and the chat reports `history.status` as `unverified`, `partial`, or `complete` only after a contiguous scan reaches the provider's end. It does not claim all existing chats were fully backfilled.

Constraints: native snapshots and old-history reads must not send, mark Telegram messages read, alter local seen_at values, produce assistant replies, re-trigger workspace events, or create a second Telegram session. A full multi-year archive is not downloaded merely to obtain unread counts.

Validation: tests/test_telegram_unread_snapshot.py, tests/test_telegram_history_paging.py, tests/test_telegram_mirror.py, tests/test_telegram_read_parity.py and Telegram ingress Node regression tests.
