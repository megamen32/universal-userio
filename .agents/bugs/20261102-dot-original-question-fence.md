# Mirror question consumer: original source and single Messenger fence

2026-11-02. Source-only, mirror/relay adapter. No release, new provider requests, or lease.

## Ancestry
Installed and published main d5c1eb0fa0f8ef33c07de46ce23a2710c168852e includes media PR14 merge 8595d5f771180f171583b03751e19b9d6c2ecdb1 and native question PR13 merge 0e4533786bc30063caf5ec0ec8a4235163a20f35. Do not rebuild old media image or downgrade logging/claims. PR12 competing fence remains DRAFT; PR16 remains independent tests-only, ExManager PR17 owned elsewhere.

## Exact source/action/receipt contract
1. Browser dot ID room~room~CalpicoMessage~suffix locates the UI reply only; it is NOT canonical source_message_id.
2. Verify existing original Telegram bot account_ref and owner peer_id, numeric source_provider_message_id from EXACT accepted incoming echo and its original source_message_ids. Pending, inconsistent, UNKNOWN, ambiguous echo must block.
3. Canonical source_message_id comes ONLY from persisted UserIO workspace_events.message_id and original Messenger action envelope; never synthesize it from browser or provider ID. Backend also resolves conversation_id, event_seq and source revision from the same stored incoming source.
4. Current question epoch and opaque lease_token are accepted only by merged PR13 authenticated QuestionRuntime and original Messenger.approve(question=...). No new claim, permit, lease or ledger. Never persist tokens in browser, relay journal, logs or model arguments.
5. Original Messenger action owns request_id, action_id=action_<sha256>, source hash, approved payload and current human/send grants. The existing Messenger.approve is the ONLY provider boundary; it writes UNKNOWN before provider I/O and reconciles original receipt. No direct GramJS sendMessage or sendFile for question-correlated dot replies.
6. Verified original outcome must contain status=accepted, question_delivery_state=sent, receipt_ref=messenger:<action_id>, matching account_ref/peer_id/source_provider_message_id, authoritative stored source_message_id and actual numeric provider_message_id, event_seq, revision, epoch, original request_id, and SHA-256 of sent text. Mirror stores only nonsecret receipt identifiers and never replays accepted/UNKNOWN.
7. Question-correlated media and multipart messages are held: PR13 currently fences action=reply only. Reuse its existing provider fence after owner approval; do not create parallel media fence.

## Source implementation
Owned file set: deploy/telegram-qr-connector/dot-question-consumer.mjs and its test; dot-mirror.mjs and its test; dot-relay.mjs. The existing relay obtains an OPTIONAL trusted questionApproval callback. If not injected, source questions fail closed. It never calls the direct GramJS transport for original-question replies. Before a callback it records send_uncertain in the existing mirror journal; missing or invalid receipt is UNKNOWN and non-replayable across restart. Existing nonquestion messages remain unchanged. No server or backend files modified.

## Actual integration blocker
Merged backend exposes service-token /v1/questions/operation only for scoped dot question coordination. It is NOT an authorized dot-scoped Messenger.approve route. The connector must NOT reinterpret that route as a sending shortcut or mint a synthetic receipt. The claims owner must provide an authenticated exact-original Messenger approval consumer (account/peer/source/event/revision/epoch/action/proof already verified, human approval/send grants preserved) before wiring questionApproval in the existing server adapter. PR12 has a conflicting fence and must not be cherry-picked. ExManager consumer integration owned elsewhere.
Patch/regressions synthetic only, no live messages or sends, no runtime restart, no release. Browser history still blocked; no E2E claims. Keep review/Draft until owner acceptance and normal integration window.