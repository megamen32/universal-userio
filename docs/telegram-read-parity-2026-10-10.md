# Telegram read parity, 2026-10-10

The owner requested comparing UserIO MCP reads against Telegram Helper for the same account and chats. The first direct-chat sample contained only 8 of Telegram's latest 20 messages. Outgoing stored messages exposed the peer label as sender. Captionless media and service events were skipped.

Explicit Telegram chat reads now ask the existing authenticated ingress to reconcile at most 50 recent provider messages, using the owned account and immutable peer ID. Responses distinguish refreshed history from cache-only and failed refreshes. No new provider session, polling worker, automatic-reply policy, or read receipt is created. This is a bounded recent-history repair, not a full archive import.

Read projections expose actual group authors and the configured owner for outgoing direct messages. Single-message reads include direction, native edit time and provider-read state. Metadata-only media/service messages are retained with placeholders and download references. Media and group routing metadata have separate attachment indices. History refresh preserves existing audio transcripts and exact native text whitespace.

Validation: 19 focused Python tests and 21 Node tests passed. Existing Node source-shape assertions were updated for the already-present Raw event import, the stronger outgoing/edit delivery guard, and the extracted reconcileDialog helper.

Runtime resource budgets remain unchanged: each of UserIO and Telegram ingress has one CPU, 256 MiB hard memory, and 64 tasks. Reads use a 25-second timeout, 50-message default reconciliation, and a 200-message server-side maximum. Tests run serially in bounded 30-second commands. No provider credentials are copied into source or test evidence.

Not addressed by this change: native total unread-count parity and complete historical pagination. Stored unread counts can differ from Telegram's full dialog counts when the archive is partial. No claim of full archive parity follows from the recent-message sample.
