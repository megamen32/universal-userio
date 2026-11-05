# Delivery reconciliation: existing attempts only

The existing draft ID, user, connected account, peer, payload, and attempt
generation remain the source of authorization. Reconciliation is read-only
provider work and never sends or retries an action. The UserIO store is the
only writer of approval/receipt state via generation/lease compare-and-swap.

## Supported positive readback

- Telegram: durable connector random ID to updateMessageID/getDifference
  mapping; source-bound replies also require exact provider readback.
- Matrix: account-scoped /sync and /messages with matching sender, room,
  text and unsigned transaction ID of the original draft.
- VK personal: messages.getHistory with the stable random ID, provider
  account, peer, body and no unexpected attachments or reply context.
- Yandex Messenger: native history with original stable PayloadId, account
  GUID, peer, unedited body and no unexpected media.

Negative matches, inaccessible history, truncated updates, missing IDs,
duplicate matches, changed payloads and provider errors stay UNKNOWN.
They never authorize a second send. Older Telegram attempts without a
stored nonce cannot be resolved by inventing one.

## Other channels

Gmail SMTP, WhatsApp, MAX, SMS and ChatGPT retain their existing durable
claim and UNKNOWN safety behavior. Each needs independently reviewed
native readback before enabling positive reconciliation. Do not infer
success from HTTP 200 or message text alone, or non-submission from
missing provider matches.

## Deployment

Only reviewed, published source reaches the image. Keep dirty checkout
unchanged. Stock replace-backend validates revision, hashes, exact live
container CAS, idle calls/meetings, account IDs, health and rollback.
Preserve Telegram journal and historical UNKNOWN drafts. No real-person
sends or phone tests for acceptance.
