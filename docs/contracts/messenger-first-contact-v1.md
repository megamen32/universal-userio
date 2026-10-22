# First contact and first delivery

This extends the [messenger contract](../messenger-contract.md). It covers
opening a conversation with a contact that is not in the history yet, and what
an outbound answer actually proves. Existing read, reply, edit and reaction
operations are unchanged.

## A first contact is a separate capability

`userio.contacts.resolve` and `userio.contacts.start` are the only operations
that open a conversation with a contact that has never written to the account.
They need more than a stored chat: they need a provider-native peer id for an
identifier the account has never seen.

For mail, SMS and WhatsApp the identifier is normalized locally. For Telegram
it cannot be: turning a username into a peer id needs the telegram-qr
connector's own live Telegram session, because UserIO never holds a Telegram
auth key. The connector may therefore be down, or the account may have no live
session, while the account itself still reads and sends normally.

So the account's capabilities carry a `first_contact` block:

```json
"first_contact": {
  "supported": false,
  "allowed": false,
  "reason": "connector_unreachable",
  "availability": "unavailable",
  "operations": {
    "resolve": {"supported": false, "allowed": false, "reason": "connector_unreachable",
                "availability": "unavailable"},
    "start":   {"supported": false, "allowed": false, "reason": "connector_unreachable",
                "availability": "unavailable"}
  }
}
```

- `supported` — the provider can resolve a new peer for this account right now.
- `allowed` — this principal may use it (read-only tokens never may).
- `availability` — `available`, `unavailable` or `unknown`.
- `reason` — machine-readable: `connector_unreachable`, `account_not_connected`,
  `contact_account_disabled`, `contact_account_not_found`,
  `contact_resolution_unsupported`, `read_only_token`,
  `account_capability_disabled`, `user_capability_disabled`,
  `account_outside_credential_scope`, `account_scope_required_for_new_contact`.

`availability: unknown` means the connector's own state could not be read. The
operation is not declared impossible, but the answer is not a promise either.
A consumer must read `first_contact` before it promises a first contact, and
must not infer availability from `provider_status`.

The answer of a failed resolve carries the same reason as `code`:

```json
{"ok": false, "error": "connector_unreachable", "code": "connector_unreachable"}
```

Nothing was sent and nothing was resolved, so the call is safe to retry once
the connector is back.

## A receipt proves only what the transport proved

An outbound answer separates what was handed to the transport from what was
delivered:

- `delivery_status: delivered` — the provider confirmed the message with its
  own message id. `sent` is `true`.
- `delivery_status: smtp_accepted` — the mail transport accepted the message.
  Nothing confirmed that the recipient received it. `sent` is `false`,
  `delivery_unconfirmed` is `true`.
- `delivery_status: unknown` — the outcome is not known.

The answer of `userio.draft.approve_send` and of `userio.contacts.start`
carries `sent`, `delivery_status`, `delivery_confirmed`,
`delivery_unconfirmed`, `message_id`, `idempotency_key`, `sent_copy`,
`retry_allowed` and `replay`. `sent` is true only for a confirmed delivery.
A consumer that reports "sent" to a human must read `delivery_status`, not
`sent`.

The Gmail receipt is `himalaya:<account>:<draft_id>:<message_id>:<sent_copy>`.
The `<message_id>` is the RFC Message-ID the transport accepted, so it is the
provider's own id for that letter and the same id a mailbox shows. The
`<sent_copy>` field is `saved`, `not_saved_command_failed` or
`not_saved_rejected`.

A sent letter is also appended to the account's Sent mailbox, so the mailbox
keeps the evidence. When the append fails the letter was still accepted by the
transport; the receipt then names the reason instead of pretending a copy
exists.

## An approval is idempotent by its draft

`userio.draft.approve_send` on an already approved draft returns the stored
outcome again and sends nothing a second time. The answer says so with
`replay: true`; the first answer has `replay: false`. Both answers carry the
same `receipt` and the same `idempotency_key`.

`retry_allowed` is `false` on every approval answer. The draft keeps its
receipt, so a retry is never a blind resend: repeat the same approval and read
the same outcome, or open a new draft for a new message.

## A kept draft is a sent draft

A draft that was sent stays on record, so the history of the conversation is
complete. Its `status` is `approved`, which means the transport took the
message; it never means "approved but not sent". Every draft projection
carries `sent`, and `pending_drafts` lists only drafts that still wait.

| `status`    | meaning                                            |
|-------------|----------------------------------------------------|
| `proposed`  | waiting for an approval                            |
| `sending`   | claimed; the outcome is unknown                    |
| `approved`  | the transport took the message                     |
| `rejected`  | the draft was refused                              |

## Acceptance

Fast unit: capabilities report an unreachable connector and an account
without a live session; a resolve failure carries its code; a Gmail receipt
carries the provider message id and the Sent-copy state; a receipt cannot
claim a delivery without a confirmation; a second approval replays the outcome
and sends nothing; a sent draft says `sent` and leaves the pending list.
Expected 5s/max30s.

Live check on an owner account: resolve a Telegram username and read the
first-contact block of the same account; send one letter to an owner test
address and find it in the account's Sent mailbox; approve the same draft
twice and see one letter. Live delivery is a separate final canary after
reviewed source publication; it is never substituted by a unit result.
