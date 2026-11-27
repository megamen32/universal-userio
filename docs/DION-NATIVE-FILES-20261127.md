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
