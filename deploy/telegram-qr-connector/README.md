# Telegram ingress worker (`userio-telegram-ingress.service`)

This is a long-lived UserIO ingress worker, not a QR-only sidecar. It has two
surfaces in one process:

1. **Login leg** — QR / phone login pages that mint GramJS sessions into
   `/var/lib/universal-userio/telegram-qr/sessions/account-N.session` and
   register the account with UserIO (`POST /v1/accounts`).
2. **Ingest leg** — one connected client per saved session: backfills the
   recent top dialogs and pushes incoming messages into the UserIO inbox
   (`POST /v1/messages`, schema `universal.inbox.message.v1`), plus live
   `NewMessage` events and a 5-minute reconciliation backfill.

Canonical source: `/opt/universal-userio/deploy/telegram-qr-connector/` on
server-100. `/opt/userio-telegram-qr/` is only the installed Node runtime and
dependency directory; it is never a competing source of truth.

The systemd unit is `userio-telegram-ingress.service`; the historical
`userio-telegram-qr.service` name is retained only as an alias. QR/phone login
at `/telegram-qr/` is the account-enrollment UI inside the ingress worker.

New-message handlers are attached before any historical reconciliation.
Backfill is deliberately text-only so a stale voice/media download cannot
block live ingestion. `/state` exposes the actual `telegram:<id>` account and
the last successful sync timestamp without exposing session credentials.

## Constraints worth remembering

- Systemd runs `/usr/bin/node` = **v12**: no `??`, no `?.`, no
  `replaceAll` in this file.
- GramJS 2.26: `client.getPeerId(peer, true)` is **async** and returns the
  same marked id as `dialog.id` (`-100…` channels, `-…` groups, plain users).
  There is no `runUntilDisconnected`.
- Sharing a session auth key with another live client (one-shot scripts,
  other services) can steal the update stream — the ingest leg reconciles by
  polling precisely because of this. Never keep a second long-lived client on
  the same session.
- API credentials come from `age`-encrypted files under
  `/var/lib/universal-userio/telegram-qr/credentials/` (key:
  `/var/lib/universal-userio/secrets/telegram-qr.agekey`); env comes from
  `/etc/universal-userio.env` (`USERIO_API_TOKEN`, `USERIO_TELEGRAM_2FA_PASSWORD`).

Webpage-preview text handling mirrors
`megamen32/mcp-telegram@codex/read-webpage-preview-messages`
(`extractMessageText`): message text first, then the visible webpage
`title`/`description` when the body is empty.
