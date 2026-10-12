# Messenger contract for the Airlock Secretary

The Secretary uses one authenticated UserIO MCP connection for messenger
operations. UserIO owns accounts, real source projections, approvals and durable
receipts. The official Airlock SDK owns reasoning, group roles, memory, business
tasks and the human confirmation. No provider login or Telegram session is copied.

Implementation milestone v1.3 is active. **This source contract is not a claim
that the live Secretary migration has completed.** Native acceptance and scoped
transport retirement are phase15.

## Normal MCP tools

The HTTP and stdio surfaces share `mcp_catalog.MESSENGER_SPECS`.

| Tool | Inputs | Result |
|---|---|---|
| `userio.messenger.capabilities` | `account_ref` | Actual configured provider/account/credential intersection: each operation has `supported`, `allowed`, `reason` |
| `userio.messenger.resolve` | `account_ref`, `peer_id`, `provider_message_id` | Actual source fetched on the current provider client and imported normally; canonical `conversation_id`, source and author. No fake conversation or read ACK |
| `userio.messenger.history` | `account_ref`, `conversation_id`, `limit`1–100, optional `cursor`, `refresh`false | Chronological page; stored mode is partial/unverified, provider mode returns its actual cursor and freshness. Cursors belong to that mode/account/conversation |
| `userio.messenger.media` | `account_ref`, `conversation_id`, `message_id`, optional `attachment_index`0–7 | Actual bounded image bytes or transcript/status. Unsupported media is explicit |
| `userio.messenger.propose` | `account_ref`, `conversation_id`, `source_message_id`, `action`, `payload`, `request_id`16–128 characters | Immutable action with `id`, `hash`, `created_at`, `status`, `envelope`, `receipt`. No provider mutation |
| `userio.messenger.approve` | `action_id`, `expected_hash`, `confirm:true` | Execute exactly the human-approved action. Durable accepted receipt or explicit failure/unknown |
| `userio.messenger.get` | `action_id` | Read the durable outcome; never generate a second intent to resolve `sending` |

Payloads: reply`{text}`, edit`{text,expected_text}`; edit source is the exact
outgoing target. Reaction`{emoji}`; sticker`{media_message_id,attachment_index}`;
file additionally allows `caption`; typing`{active}`. Media references must be
actual messages in this owned conversation. Each provider advertises only
implemented operations; unsupported file upload is not inferred from media reading.

Provider IDs are opaque **strings**, including MAX IDs above the JavaScript
safe-integer range. Do not substitute the SDK `stable_ref_id` hash. Telegram
canonical message ID is `account_ref + '|' + peer_id + ':' + provider_id`.
MAX canonical message ID is `max-` + actual chat ID + `-` + actual provider ID.

## Source binding

Messages expose `source`, `author`, `text`, `at`, `edited_at`, `media`.
Source includes exact account/conversation/peer/provider/local message IDs,
channel, direction, kind, reply-to and thread IDs. Author ID is the actual
participant; name/first/last/username are display fields and never authority.

Approval binds the following exact provider fields:

```json
{
  "account_ref": "telegram:900000103",
  "peer_id": "-1009000002001",
  "provider_message_id": "123",
  "author_id": "456",
  "direction": "incoming",
  "text_sha256": "SHA256 of the exact raw UTF-8 source text",
  "thread_id": "",
  "reply_to_provider_message_id": "",
  "edited_at": 0
}
```

All IDs are strings. `edited_at` is integer Unix seconds, zero when absent.
`source_hash = SHA256(canonical(source_binding))`. Canonical JSON sorts object
keys, uses UTF-8 without ASCII escaping, no whitespace, no NaN; no floating
point values occur in the signed envelope. Display names, local IDs and local
arrival time do not participate in the provider binding.

The action envelope contains `schema:userio.messenger.v1`, `user_id`,
`account_ref`, `conversation_id`, `peer_id`, `channel`, `source_message_id`,
`source_provider_message_id`, `source_hash`, `source_binding`, `thread_id`,
`action`, `payload`, `media_hash`, `request_id`, `action_id`.
The exact whole-envelope digest is the approval `expected_hash`.

For sticker/file actions, `media_hash` is the digest of exactly
`{source_binding, attachment_id, attachment_index}` for the actual media source
in the same peer. `source_binding` uses the provider fields above,
`attachment_id` is the native photo/document/sticker ID as a string,
`attachment_index` is an integer. It contains no access hash, provider URL,
local display name or local timestamp. The provider re-reads this actual media
source and verifies its attachment identity before claiming the mutation.

## Current sender and protected provider route

The Secretary Telegram sender is **900000103**, owned by the existing relay
client. The private owner900000101 is a separate account. The UserIO QR connector
does not currently have an881 slot; never send via540 as fallback. The original
private read-only Airlock `userio` resource remains unchanged.

Generic account adapter configuration uses the existing protected
`/etc/universal-userio.env`, with nonsecret metadata:

```ini
USERIO_MESSENGER_RELAY_URL=http://127.0.0.1:30192
USERIO_MESSENGER_RELAY_ACCOUNT=telegram:900000103
USERIO_MESSENGER_RELAY_USER_ID=user_owner
USERIO_MESSENGER_RELAY_TOKEN_FILE=/home/roomhacker/agents-projects/exmanager/var/airlock-relay.token
```

The account must already be assigned/enabled/reply-capable for this UserIO user.
The existing relay Bearer remains in its protected file; the existing
`USERIO_API_TOKEN` is read in memory. Neither value belongs in this document,
logs, messages, a new session file or a copied credential.

Relay30192 supports GET`/userio/messenger/capabilities` and POST
`/userio/messenger/history`, `/message`, `/execute`. Message/history take exact
account, peer and canonical conversation ID; message also takes the raw
provider ID and optional`read_media`. Read responses retain actual native source.
Execute receives `{envelope, approval, proof}`. Provider callbacks use the current
connected client, re-read actual source/author/thread and own-edit rights, and
retain their own durable no-replay receipt journal.

Approval flat fields are schema`userio.messenger.approved-action.v1`, `action_id`,
`account_ref`, `peer_id`, `user_id`, `conversation_id`,
`source_provider_message_id`, `thread_id`, `action`, `payload_sha256`,
`source_sha256`, `envelope_sha256`, `idempotency_key`, `approved_by`, `issued_at`,
`expires_at`. Payload/envelope digests use canonical JSON above; source digest is
the provider binding. Idempotency is`userio:<user_id>:<action_id>`, approved_by is
the authoritative UserIO user. TTL is at most300s.
`proof=HMAC-SHA256(existing USERIO_API_TOKEN, schema + '\n' + canonical(approval))`
as lowercase hex. UserIO signs only an exact durable `sending` action claim;
a Boolean from a model, bare draft ID or relay Bearer is not approval.

Receipt is `{account_ref,peer_id,provider_message_id:string,receipt:string}`.
Edit receipt must have the same raw provider ID. Acceptance is persisted before
mirror hydration. A timeout/invalid receipt retains `sending`; it is not safe
to retry a fresh intent. Only proved pre-submission failures release the claim.

## Separate native sender credential

The owner can issue a distinct app-specific Bearer using the existing normal
endpoint, authenticated with its existing protected owner credential:

```http
POST /v1/personal-tokens
Content-Type: application/json
```
```json
{"name":"Secretary messenger sender","scope":"messenger_actions","account_refs":["telegram:900000103","max"]}
```

The token is returned once and must go straight into the normal encrypted
Airlock resource, never chat/output. Native slug`userio_messenger` is separate
from read-only`userio`. The new scope permits only generic messenger MCP tools
and these explicit accounts; legacy APIs/resources, token management and other
accounts are denied. No private readonly token is upgraded/replaced/revoked.
MAX actions are limited by the native SDK to the explicitly authorized QA
conversation and exact confirmation during acceptance. Existing business
request135, other chats and historical sends are not canaries.

## Real acceptance

After reviewed source, focused checks, publication, install and all intentional
restarts, run the same native Secretary journey through Telegram QA
`-1009000002001` and the authorized MAX test conversation: real source/media,
exact approved reply, durable receipt, same-ID repeated edit, then supported
rich actions. Capture provider/recipient and native SDK results before retirement.
Only the Exmanager owner changes its transport and SDK files. Retain native
memory, roles, business tasks, current identities, old receipts and rollback.
