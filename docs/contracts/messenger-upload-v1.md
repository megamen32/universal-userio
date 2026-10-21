# New-file staging for approved messenger actions

This extends the [messenger contract](../messenger-contract.md). It sends new
document bytes; existing provider-reference `file` and `sticker` actions are
unchanged. The native Secretary consumer belongs to Exmanager. Installation
and native delivery acceptance are separate from this source contract.

## Discover, stage, propose, approve

1. Discover `userio.messenger.capabilities(account_ref)`. Both `file` and
   `file_upload` must be allowed by the real provider, account and credential.
   Private read-only credentials remain read-only.
2. Call `userio.messenger.upload` with exact `account_ref`, `conversation_id`,
   `source_message_id`, plain `filename`, `mime_type`, `data_base64`, lowercase
   hexadecimal `sha256` of the decoded bytes, and `request_id` (16–128 ASCII
   letters/digits/colon/underscore/hyphen). Use actual SDKStorage bytes, never
   a caller path, URL or existing provider attachment disguised as new content.
3. The response is `{ok:true, upload:{upload_id,filename,mime_type,size,sha256,
   expires_at}}`. No bytes are returned. The resource is private to this user,
   account, conversation, immutable source binding and source hash.
4. Propose the normal action `file` with payload
   `{upload_id,caption?,force_document?}`. By default Telegram classifies media
   from the filename as its native clients do; set `force_document:true` to send
   it as a document. The
   Telegram connector validates caption bytes against the selected account's
   current server-advertised limit. This branch is mutually exclusive with
   `{media_message_id,attachment_index,caption?}`. The immutable envelope payload
   becomes `{upload_id,filename,mime_type,size,sha256,caption?}` and `media_hash`
   is the empty string; the existing source/envelope digest formulas are unchanged.
5. Show and approve the exact action hash through the existing human or trusted
   dispatcher contract. Staging/proposing alone never contacts the provider.

## Bounds and lifecycle

Telegram staging follows the connected account's current MTProto upload ceiling
(`upload_max_fileparts_default` or `_premium`) and is bounded by the UserIO
SQLite BLOB limit. Other providers without an account-specific upload capability
retain their provider bound. There is no per-user file-count quota. Logical
staging TTL is 900 seconds; expired bytes are scrubbed on the next staging
operation, and delivery of expired resources is refused.
Request IDs deduplicate identical staging; different bytes/metadata/source with
the same ID are rejected. A consumed/expired request is not silently renewed.
Each resource binds to one action ID in the same SQLite writer transaction as
action creation. Multiple distinct human sends require separate resources/actions.

Source changes, content corruption, scope changes and expired resources are
refused before the durable sending claim. Source/account permissions are also
checked by the provider immediately before upload. The ordinary action journal
owns upload plus send: after a possibly submitted upload/send failure the state
remains `sending` (UNKNOWN). Read that same action; do not create another request
or repeat the provider upload. On accepted delivery bytes are scrubbed while
the metadata, source-bound envelope and receipt remain durable. Repeated approve
of an accepted action only returns its saved receipt.

## Relay provider packet

Relay providers can carry new file bytes in the existing
`/userio/messenger/execute` packet as one optional field:

```json
{"upload":{"data_base64":"base64 of the exact new bytes"}}
```

`approval.payload_sha256` and `approval.envelope_sha256` sign the filename,
MIME, size, bytes SHA, source and action ID in the immutable envelope. Providers
must compare size/SHA with the signed metadata, re-read the exact source, and
validate permission before uploading through their existing connected client.
They must not accept extra paths/URLs or perform an unjournaled pre-approval
upload. Normal receipt identity checks still apply.

The Telegram QR provider sends the raw staged bytes as a streamed HTTP request
to its existing session owner. The connector writes to a private temporary
file, checks the account's current upload and caption limits plus SHA-256, then
uses that same GramJS client to upload and send. The durable send-attempt journal
prevents replay after an uncertain provider response.

The MAX implementation follows the current PyMax implementation:
[upload service](https://github.com/MaxApiTeam/PyMax/blob/main/src/pymax/api/uploads/service.py),
[event mapping](https://github.com/MaxApiTeam/PyMax/blob/main/src/pymax/dispatch/mapping.py).
It requests FILE_UPLOAD87 metadata, POSTs new bytes to the returned provider
upload route and waits for matching FILE_READY136 before sending FILE/fileId.
No new session or credentials are created. UserIO accepts no arbitrary upload URL.

## Acceptance

Fast unit: filename/MIME/base64/SHA/bounds, expected 5s/max30s.
Focused integration: actual MCP staging/propose/approve signatures, scope,
expiry, one-action binding, unknown no-replay, MAX upload/ready frames and
preserved incoming dispatch; expected 20s/max60s. Combined ordinary checks,
including setup, must finish within 180s. Live delivery is a separate final
canary after reviewed source publication/install; native Secretary execution
must be evidenced by its owner, not substituted with an owner HTTP call.
