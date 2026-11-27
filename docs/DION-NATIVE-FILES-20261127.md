# DION native file and message acceptance boundary

## Public wire evidence

The published DION web environment at /platform/envs.json identifies
dc-data-storage.dion.vc as its file API and chats-api.dion.vc as its crypto
API, with crypto encryption enabled. The public client modules
7403.d0c0e99c.js, 9552.7bcc8766.js and 8122.7173700a.js define:

- message attachments: id, name, mime_type, size, fingerprint;
- GET /v1/attachments/{attachment_id}: binary nonce || ciphertext || GCM tag;
- POST /v1/attachments: multipart file with encrypted binary part;
- batch send message uses attachments_ids, not attachments or file_id;
- exact message lookup /v1/messages/{message_id}.

These are public protocol definitions, not live file delivery receipts.

UserIO stores attachment metadata with the owning message and verifies the
actual provider message and attachment identity before downloading. New files
use the existing scoped MessengerUploads and durable confirmation journal.
Unknown upload or send outcomes are not replayed. HTTP and MCP use the same
native file adapter. No browser credentials are copied or imported here.

Poll summaries record whether a concrete conversation array was returned.
An empty response body is not evidence that an account has no chats.

## Remaining gates

Live text/file send and receive require an isolated authorized test chat.
The deployed account previously had no stored messages or attachments.
The M1 browser returns to login after the earlier exclusive handoff.
A live speech call is not implemented: the existing helper is a VP8 data
tunnel, not an audio bridge. It must not join ordinary conferences because
its upstream call core may disconnect existing participants. Isolated tunnel
operations retain their separate confirmation and lifecycle contracts.


## Live isolated group acceptance, 2026-11-27

The owner created an isolated DION group (one member) and posted five
messages containing three PNG files and one Markdown document. The published
backend synchronized the messages, downloaded and authenticated all four
attachments, and matched their advertised sizes (5,082; 16,115; 70,490;
17,036 bytes). UserIO then submitted a text reply, a 42-byte Markdown file,
and a 68-byte PNG using the existing durable approval journal. Both uploaded
files matched their original bytes on a second native download. Browser
readback confirmed the text and Markdown were visible in the original DION
group. This establishes live browser-to-UserIO and UserIO-to-provider
round trips on the same account, not incoming messages from another person.

## Conversation creation

`userio.messenger.capabilities` reports `dion_admin` with separate
`group_create` and `channel_create` actions. The caller proposes
`userio.messenger.dion_admin.propose` with an owned account, operation,
title, optional exact participant IDs, and a stable request ID. The
existing `userio.messenger.approve` confirms the immutable action hash.
A timeout leaves the action uncertain; never submit another creation
under a different request ID to work around it. Scoped credentials
require explicit `dion_admin` permission, read/write access, and all
chats of that account. No additional credentials are granted by an
ordinary reply permission.

The native public web contract is POST /v1/conversations with name,
conversation_type (GROUP or CHANNEL), and members_id. The accepted
provider ID is retained even if the follow-up GET fails; readback status
is explicit. Empty groups/channels project using the same canonical
conversation key as ingress, without generating fictitious messages.
