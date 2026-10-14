# Telegram ingress worker (`userio-telegram-ingress.service`)

This is a long-lived UserIO ingress worker, not a QR-only sidecar. It has two
surfaces in one process:

1. **Login leg** — QR / phone login pages that mint GramJS sessions into
   `/var/lib/universal-userio/telegram-qr/sessions/account-N.session` and
   register the account with UserIO (`POST /v1/accounts`).
2. **Ingest leg** — one connected client per saved session: backfills the
   recent top dialogs and pushes incoming and outgoing messages into the UserIO inbox
   (`POST /v1/messages`, schema `universal.inbox.message.v1`), plus live
   `NewMessage` and `EditedMessage` events and a 5-minute reconciliation
   backfill. Edited messages are re-posted under the same message id with the
   provider `editDate`, and never reschedule agent delivery.

Canonical source: `/home/roomhacker/agents-projects/universal-userio/deploy/telegram-qr-connector/` on
server-100. `/opt/userio-telegram-qr/` is only the installed Node runtime and
dependency directory; it is never a competing source of truth.

`TELEGRAM_HTTP_PROXY_URL` selects an explicit HTTP CONNECT proxy for the existing
GramJS clients. Unset retains the existing SOCKS/MTProxy/direct configuration.
It changes transport, not saved sessions, API credentials, account selection
or read receipts.

The systemd unit is `userio-telegram-ingress.service`; the historical
`userio-telegram-qr.service` name is retained only as an alias. QR/phone login
at `/telegram-qr/` is the account-enrollment UI inside the ingress worker.

New-message handlers are attached before any historical reconciliation.
Backfill is deliberately text-only so a stale voice/media download cannot
block live ingestion. `/state` exposes the actual `telegram:<id>` account and
the last successful sync timestamp without exposing session credentials.

## Operator-assisted code login

The enrollment surface also exposes bearer-protected JSON endpoints for an
operator who can already read the account's official Telegram service chat
through an independent authorized client:

- `POST /login/phone` with `{ "phone": "+79990001122" }` starts a new,
  independent login slot and returns its `account-N` id;
- `POST /login/code` with `{ "slot": "account-N", "code": "12345" }`
  completes the Telegram-delivered code prompt.

These endpoints never import or copy another client's auth key. The resulting
GramJS session is independently minted and stored with mode `0600` under the
ingress state directory. The browser QR and phone forms remain as a human
fallback.

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

## Native mirror contract

`message.out` and `message.date` are preserved as direction and provider Unix
time. Dialog inbox/outbox read maxima and live Raw read updates populate a
separate provider-read field; passive API/MCP history reads never send a Telegram read receipt. Human web viewing uses the opt-out read setting described below.
Outgoing, edited and historical messages never schedule agent delivery.

The existing reconciliation loop repairs recent history without notifying or
sending. A bearer-protected `POST /reconcile` with exact `account_id` and
`peer_id` performs one bounded200-message repair on the connected client. It
creates no new session or polling worker. Missing database IDs within the
returned history range are individually probed with getMessages(ids); only a
successful exact-ID absence or native deletion update creates a tombstone.
Message/event/draft/receipt identities remain retained. No inferred deletion
is made outside that range. Runtime Node budget224/256MiB (measured soft adjustment2026-10-09), CPU1, tasks64 and
existing20-dialog/5-minute reconciliation cadence are unchanged.

## Human web read receipts

Authenticated `POST /read` takes exact `account_id`, numeric `peer_id` and a positive
`max_id`; it uses the already connected account client and never falls back to a
different account. Official Telegram `messages.readHistory` / `channels.readHistory`
are selected from the actual input peer. No mentions-clearing call is added.
UserIO's normal conversation read endpoint validates owner/account/message provenance
and updates provider state using the native ID watermark only after acknowledgement.
Web viewing defaults to automatic reading; the persistent per-user “Режим скрытности”
setting disables it. API/MCP history fetching and background subscription remain
read-only. MAX/SMS/phone currently record reading locally in UserIO; no unsupported
external receipt is implied.

## Account-bound folder metadata

Protected `GET /folders?account_id=telegram:<id>&folder_ids=10,15` reads only the
selected existing connected account, verifies its actual identity and returns
folder title, marked include/pinned/excluded peer IDs, category flags and
mute/read/archive exclusions. It never sends, marks read, imports sessions or
starts another client. These are filter rules, not a guessed complete folder
membership; apply all flags and exclusions before changing UserIO chat policy.
Telegram Helper's older folder summary exposes only counts, which is insufficient
for this selection. Original source-first TL/Android/tweb/installed GramJS gate
and focused exact-account metadata checks are retained with the owning release.
`resolve=1` additionally batches only explicitly included/pinned, nonexcluded
InputPeers through the same client. It returns marked peer ID, name and kind,
without phone/access-hash/session values or message text. Category flags remain
separate; this option does not silently turn an automatic “all groups” filter
into a UserIO allow-all policy. Missing known chats can then use the existing
exact-account `/reconcile` history route; historical ingestion remains silent.


## Personal browser enrollment

NEW browser slots resolve the actual personal UserIO cookie through
`GET /v1/connection-principal`. `slot-owners.json` stores only slot→user identity
with mode0600; the cookie stays transient until registration and is never stored.
`/state` and code/password/start are limited to that user's slots. Existing slots
without metadata retain the deployment owner and their sessions are untouched.
New accounts register through that same user's cookie, with normal reply support;
failed registration does not start ingestion as the operator. Service ingress
uses the existing unique registered account owner and refuses ambiguous bindings.
Install `enrollment-owner.mjs` with `server.mjs` before the worker restart.
