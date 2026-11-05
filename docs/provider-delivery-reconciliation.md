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
- Gmail SMTP: native mailbox inventory finds a unique Sent folder, including
  localized paths such as Gmail/Отправленные, without changing account settings.
  Bounded Sent search requires the original deterministic RFC Message-ID,
  configured sender, recipient, exact plain-text MIME body and no unexpected
  attachments. This proves an account Sent copy; it is represented as
  SMTP-accepted, not recipient-delivered. The same native folder is used for
  saving Sent copies after successful SMTP sends.
- MAX (text-only original approved drafts): persist a stable native CID before
  the first possible provider submission, then use bounded native history to
  locate outgoing candidates and GET_MESSAGE to verify the exact original CID,
  sender, chat, unedited body and absence of file/reply context. MAX file sends,
  older attempts without the recorded CID, ambiguous or missing provider
  details remain UNKNOWN; no resend is authorized.
- WhatsApp personal QR: the bridge reserves and durably fsyncs a Baileys
  messageId bound to draft, user, connected account, slot, peer and exact
  text before the first provider call. The same key is passed to sendMessage.
  Authenticated outbound fromMe messages.upsert/history events can positively
  confirm that ID; a source-scoped GET /send-attempt reads it without sending.
  Legacy unpinned attempts stay UNKNOWN, including crashes before submission.
  The shared backend keeps generation/lease CAS and never reissues a write.


Negative matches, inaccessible history, truncated updates, missing IDs,
duplicate matches, changed payloads and provider errors stay UNKNOWN.
They never authorize a second send. Older Telegram attempts without a
stored nonce cannot be resolved by inventing one.

- Android SMS (APK and Termux): exact Idempotency-Key, device identity,
  SHA-256 of original recipient/body, and native sent status from the owned
  gateway's read-only GET /v1/original-send. The UserIO draft and existing
  generation/lease CAS remain authoritative. A native sent callback means
  phone-side send acceptance, not recipient delivery. Missing/queued/unknown
  status keeps UNKNOWN; no second SMS send.

## Other channels

ChatGPT retains its existing durable
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


## ChatGPT CDP original-send recovery

Only the configured, unscoped, owner-bound ChatGPT CDP sender can read
the exact original draft idempotency key via the local sidecar tool
get_send_status. Its durable journal records the request before one
browser submission and persists the opaque messageRef after a positive
page-observed result. A matching original key, chatRef, text and
evidence=page_observed permits UserIO to close its original draft using
the existing generation/lease CAS. This is page acceptance, not a human
read receipt. Missing, inaccessible, ambiguous or foreign-account
records remain UNKNOWN, never allowing a second message submission.

The live UserIO container does not currently configure a CDP sidecar.
Do not invent a browser login, cookie, long-lived access or automatic
account assignment. Activation requires the existing owner's supported
CDP session/context and explicitly configured sidecar launcher.
