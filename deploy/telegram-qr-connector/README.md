# Telegram QR connector (`universal-inbox-telegram-qr.service`)

Single Node service with two legs:

1. **Login leg** — QR / phone login pages that mint GramJS sessions into
   `/var/lib/universal-inbox/telegram-qr/sessions/account-N.session` and
   register the account with UserIO (`POST /v1/accounts`).
2. **Ingest leg** — one connected client per saved session: backfills the
   recent top dialogs and pushes incoming messages into the UserIO inbox
   (`POST /v1/messages`, schema `universal.inbox.message.v1`), plus live
   `NewMessage` events and a 5-minute reconciliation backfill.

Deployed copy: `/opt/universal-inbox-telegram-qr/server.mjs` (this file is
the source of truth; copy with `install -m 644`).

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
  `/var/lib/universal-inbox/telegram-qr/credentials/` (key:
  `/var/lib/universal-inbox/secret-agent/telegram-qr.agekey`); env comes from
  `/etc/universal-userio.env` (`USERIO_API_TOKEN`, `USERIO_TELEGRAM_2FA_PASSWORD`).

Webpage-preview text handling mirrors
`megamen32/mcp-telegram@codex/read-webpage-preview-messages`
(`extractMessageText`): message text first, then the visible webpage
`title`/`description` when the body is empty.
