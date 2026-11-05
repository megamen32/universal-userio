# UserIO dot adapter: transitional integration

This directory contains the account-scoped UserIO integration, not a second
standalone product. The portable text-mirror project is
[dot-telegram-mirror](https://github.com/megamen32/dot-telegram-mirror).
Its injected adapters are the portable source of truth. The modules here preserve
the existing UserIO enrollment, live Telegram client, owner-only routing and
browser transport while that integration is migrated deliberately. This change
does not replace the portable core or claim that this adapter runs standalone.

## Protected policy, separate from source

Personal account IDs, conversation URLs and browser transport coordinates must
never be committed. Before enabling this adapter, an operator provisions
`/var/lib/universal-userio/telegram-qr/dot-policy.json` as a regular, non-symlink
file owned by the connector UID, mode 0600. The public schema and synthetic
examples are in `dot-policy.mjs` and `dot-test-policy.mjs`. The latter is a
test fixture and must never be imported by the service.

The policy fixes the allowed owner, conversation, enrollment-source account,
browser URL and existing SSH/MCP transport. It does not contain Telegram tokens.
Missing or malformed policy fails closed. The optional
`USERIO_DOT_POLICY_PATH` override is trusted startup configuration, not a request
parameter. It exists for isolated tests and deployments with a different protected
state location. Policy is read at process start; changing scope requires a
reviewed configuration update and restart. Request-supplied values cannot select
another host, owner or conversation.

The existing protected `dot-binding.json` activates the dedicated bot and browser
page only within that policy. Enrollment reserves the exact bot identity before
live ingestion. Bot credentials remain in the existing encrypted, account-scoped
store. The Board service grant remains a distinct protected, expiring credential;
it permits enqueue and receipt reads only for its own requests.

## Delivery behavior

Only incoming private messages from the configured owner are admitted. Existing
accepted or uncertain operations are never replayed. Telegram replies map through
durable delivery receipts to native browser reply targets; if the original target
exists only in Telegram, a bounded, explicitly labelled safe quote is used.

Text mirroring starts from an activation baseline. Losing the saved cursor stops
forwarding instead of resetting the baseline. Recovery may load older messages
through the native UI until that exact cursor is visible, then resume only
previously authorized messages after it. It must not send pre-baseline history.

When the owner explicitly accepts an unavailable history gap, the account owner
may use the authenticated connector endpoints `GET /dot/resync-preview` and
`POST /dot/resync`. The latter accepts `requestId` (32 lowercase hex digits),
`expectedCursor` and `targetCursor` from the preview, and the explicit
`confirm: "skip_unavailable_gap"`. It requires the existing personal UserIO
management authorization and ownership of the bound live bot. A changed room,
head or journal, an active delivery, a previous unknown outgoing operation or a
visible old cursor refuses the request. Refresh the preview after a refusal;
never repeat an unknown provider send. A successful operation writes a private
exclusive backup of the complete old relay journal and the visible snapshot,
then records both cursor identities and `not_delivered` in the relay journal.
The intervening unavailable messages are not claimed as delivered.

Prepared photo support accepts JPEG, PNG and WebP only, up to 8 MiB each,
20 MiB per album and ten images. Downloads use the same authenticated live
Telegram client without read acknowledgments. Temporary files are owner-only,
content-hashed and bounded. Browser upload requires exact filenames, completed
previews, no upload error indicators and the expected composer. A ready Send
button alone is not proof that uploading completed. Unknown sends require review.

This source includes media support, but actual deployment and user-flow
acceptance must be recorded separately. Passing mocks does not establish a live
image-transfer result. Do not replay a photograph already delivered manually.

## Verification and release

Run the selected Node tests with production dependencies installed:
`node --test dot-*.test.mjs board-dot-*.test.mjs bot-enrollment.test.mjs`.
The tests use synthetic policy data and do not access Telegram or a real browser.

Publish through a reviewed feature PR. Deploy only its merged source with a
protected policy that preserves the already approved scope. Check active calls,
coordinate the existing release lock, preserve data and unrelated service
identities, retain rollback, verify installed source hashes and account inventory,
then verify an authorized real delivery. Do not broaden access to make a test pass.
