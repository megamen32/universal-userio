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


## Handoff and remaining ownership

Installed ready packet was delivered directly to active consumer61 (native turn01a12226-d44f-7901-8452-e60065e3ab26) and Root24 (turn01a12258-fb65-77c3-ab19-a1082dee6c25), stable input pilot9-userio-installed-ready-3444ef6-v1, both admitted. Receiver source/dependency work is delivered. Sole61 now owns actual case2062 provider flow; UserIO owner remains available for an exact failure or scoped restore. Do not mint another case, send or restart during that flow.

The wrong SMS label in Telegram approval_hint was reproduced and repaired source-only in both mcp_dispatch.py and legacy mcp_surface.py. Generic Russian copy explains that the saved draft needs explicit confirmation. manual_lock, confirmation, body, immutable source, credentials and receipt behavior are unchanged. Five selected fast-unit cases RED2.92s -> GREEN1.94s cover both surfaces with locked/unlocked Telegram drafts plus the existing isolated SMS approval/receipt check. Bound: AS384MiB/CPU10s/wall15s/file16MiB inside existing81106/50CPU/128GB; no model/live send. Logs preserved in .tmp/pilot9-receiver-readiness/hint-tests/{red,green}.log. This is a prepared copy repair, not installed consumer proof. Installation remains deliberately held until61 captures the full case and restores the pair scope; the existing runtime remains3444ef6, no restart or new draft/send.

2026-10-09, sole consumer61 reported actual first-leg acceptance: real conversation conv_055a2aad9c5b777495326512 from provider source2531, normal draft_ba724e6991314558a82d30722d00c04e explicitly approved, actual provider2532 receipt exmanager-relay:telegram:8810909089:8634588930:2532:draft_ba724e6991314558a82d30722d00c04e. Existing real-conversation exclusion/baseline preserved by61. Full Pilot9 remains held by an independently owned AFC continuation classifier defect, not receiver wiring. No repeat of first leg, new buyer, start or provider send. Thin receiver real reply is accepted; parent cache and full business case are not declared complete.


## Dispatcher expiry renewal, 2026-10-09T21:40:37Z

Sole consumer61 requested only the existing pair rule deadline22UTC→23UTC (02:00MSK10Oct) while the captured first leg2532 remains retained and full AFC continuation is pending. Exact previousSHA1af59f9e2f27d3a6f8530d5d93985b50e7da50d05260cf5c9ffaf90827242614 checked before atomic replacement; old bytes preserved privately. New actualSHA a1c1d6d3c0babf3a89f613446c9284115e57e69d97f24958adb8bec04733b190. Only expires_at changed. Same dispatcher PID759490/invocation08e0cf43c9b643c1b0d7aa30fba8b0a4 active; native guard hot-read returned the purpose for the exact pair and no exclusion for wrong peer/account/source. Restart/source/model/provider calls0. BaselineABSENT and its original bytes unchanged. Protected receipt .tmp/pilot9-receiver-readiness/dispatcher-renew-0200msk.json, previous bytes guard-before-0200msk.json.

Current restore contract supersedes the earlier historical hash: after full case capture, remove only /var/lib/hermes-userio-dispatcher/pilot-pair-guard.json matching a1c1d6d3c0babf3a89f613446c9284115e57e69d97f24958adb8bec04733b190; hash mismatch preserves the file for owner reconciliation. No restart. Preserve the independently added real-conversation exclusion; relay renewal/restore stays9a-owned. Hint52408f5 and core-copy remain uninstalled while fullcase hold is active; source/runtime consumer not repeated.
