# Exact task reply association

`universal_userio.task_reply_association` is a read-only join over the existing
UserIO `SQLiteUserIOStore`: canonical messages, task mandate operations and
draft claims, contact qualifications, conversations, and approved draft
receipts. `UserIOStoreTaskReplyReaders` scopes every lookup to one authenticated
UserIO user. It performs no provider calls, writes, sends, or retries.

The resolver starts with the exact `(provider, account_ref, conversation_id,
message_id)` key from UserIO's canonical message store. It then requires the
canonical message's exact
`reply_to_provider_message_id` to equal one accepted/sent receipt's provider
message ID. Provider, account, conversation, actor, project, task version,
contact, and source references are derived from and checked across the mandate,
operation, claim, qualification and conversation rows. They are not read from
provider-authored inbound content. Duplicate copies of the same immutable
receipt are collapsed; conflicting receipts are denied.

It does not infer association from endpoint, display name, recipient, recency,
last message, or conversation membership. Ambiguous scopes and nonterminal or
`UNKNOWN` operation outcomes remain unresolved. Missing canonical records,
orphan targets, invalid scope, and cross-account/provider references are denied.
Callers must preserve provider-native IDs and must never turn unresolved or
unknown outcomes into another send.

## Provider receipt mapping

MAX is currently supported when all stored evidence is present. The existing
`UserIOService._commit_accepted_draft` reads MAX by the returned receipt, checks
that the returned native message ID and sender match the sent message, then
stores the canonical outgoing message. The adapter requires the approved,
delivered draft receipt, exact task operation/claim/qualification binding, and
that persisted outgoing message in the same conversation. Custom MAX account
message IDs use the existing `<account_ref>|<peer_id>:<provider_id>` form; the
default MAX account uses the existing `max-<peer_id>-<provider_id>` form.
No result is returned if receipt hydration failed or the message row is absent.

Other providers remain fail-closed. Their `drafts.outbox_receipt` is opaque to
this adapter, or their current approval path does not persist a canonical sent
message that can be joined to the receipt. The smallest follow-up is an
owner-approved, provider-supplied native `provider_message_id` on the existing
durable draft/operation receipt, committed only after that adapter's exact
receipt readback; no endpoint, name, body or time heuristic may substitute.

The end-to-end regression executes the existing qualification, mandate reserve,
task outbox approval, MAX receipt hydration, Store message insertion and
inbound reply path through disposable SQLite data, then closes and reopens the
Store before resolving. It passes against the frozen U52 outbox repair. This is
local source-path evidence, not a live provider or full ExManager consumer
acceptance.
