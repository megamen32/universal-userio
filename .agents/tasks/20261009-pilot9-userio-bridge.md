# Pilot9: normal UserIO on existing relay account

Owner: UserIO session 01a11747-7bb9. Business consumer: session 01a12061. Relay source/runtime: eXmanager owner 01a1209a, source 3660089. Parent cache work remains paused.

The account-scoped adapter uses the maintained authenticated relay at 30192 for only telegram:8810909089 / peer 8634588930. An explicit bounded relay-sync imports actual provider messages through normal UserIO ingress. It creates no empty conversations and runs no inference. Draft creation atomically captures immutable provider origin; normal explicit approval and its durable sending claim produce a short signed envelope. Relay validates approval/source/body/dedup before its existing connected client sends. Other accounts and peers retain their existing QR route.

Confirmed defect repaired: store.conversation omitted peer_id, preventing exact route selection. Ten focused bridge checks plus three existing QR checks passed in 3.95 seconds. Fast unit category, expected 4 seconds, maximum 30 seconds; models and actual sends zero. Existing SDK/UI/backend proofs are retained, not repeated.

Own core working set measured 217.7 MiB including swap at soft128/hard256; reclaim stalled metadata HTTP. The canonical core unit uses soft224 MiB inside unchanged hard256/swap64/CPU1/tasks64. Ingress224 repair was already delivered and is not repeated. Only own core activation is needed; no shared daemon or relay restart.

## Operator and consumer contract

Use existing UserIO owner Bearer in /etc/universal-userio.env without printing it. After the actual buyer inbound, POST http://127.0.0.1:18093/v1/telegram/relay-sync with JSON {}. Response returns real conversation_ids and cursor. The fixed account/peer cannot be chosen by the caller. Use that real conversation_id in existing userio.channels.read and userio.channels.send_draft. Approve through userio.draft.approve_send with confirm=true, draft_id, expected_text, expected_chat_id and expected_attachments=[] matching the saved draft. No raw Telegram send or new authorization.

Actual delivered receipt is exmanager-relay:telegram:8810909089:8634588930:<providerMessageId>:<draft_id>. Unknown/uncertain provider outcome is not replayed. Changed/deleted origin is refused before provider dispatch. Approval expiry is capped by both live guards and relay bridge scope.

Dispatcher guard renewed hot to 2026-10-09T22:00:00Z; current SHA 1af59f9e2f27d3a6f8530d5d93985b50e7da50d05260cf5c9ffaf90827242614, same PID759490. Original baseline absent, old9365 bytes preserved privately. After full consumer, remove only exact same-SHA /var/lib/hermes-userio-dispatcher/pilot-pair-guard.json without restart. Mismatch requires owner reconciliation. Relay guard/bridge expiry must be verified independently and restored by its owner.

## Installed receiver acceptance

2026-10-09T20:44:59Z: source3444ef6d96cbb168b6975b3ecebafee72d943b45 published and installed with rollback-first controller; full managed manifest65ddcdccf67b842965f85e71c2c19ee01a1452f77a1835309f0969ef4f5e8581 verified. Core PID2850194/invocation75636b38c98a4701934b016ad3933804 active, measured38.3MiB, soft224/hard256/swap64/CPU1/tasks64. Soft persisted through native settings and canonical unit. Only own core restarted once; ingress, relay, dispatcher, shared SDK and daemons unchanged.

Actual authenticated accounts returned registered enabled881/read/reply HTTP200 in116ms. Maintained relay cached actual881/exact863, live bridge and both guards expire22UTC. Actual normal UserIO relay-sync returned HTTP200 in26ms, imported0/conversation_ids=[]/cursor0 from independently checked empty provider journal; no fabricated conversation, draft, message, model, provider RPC or send. Private ready packet: .tmp/pilot9-receiver-readiness/ready-packet.json, rollback receipt bridge-rollback.json, protected environment rollback receipt relay-env-receipt.json. Five nonsecret exact-account config lines appended; existing credential values and QR fallback retained.

The receiver capability gap is closed. Business consumer61 must perform the single actual provider→normal UserIO draft→explicit approval→provider receipt flow and restore the scoped rules promptly; full Pilot9 delivery is not claimed by this receiver acceptance. Parent cache remains paused. Runtime inputs frozen until61 captures the result; do not repeat the empty readiness probe or tests.
