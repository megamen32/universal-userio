---
status: incomplete
---
# Gmail generic sender readiness — UserIO slice READY

UserIO source dcdac8d published/installed/manifest verified. Reuses existing Gmail inbox mirror and Himalaya SMTP; source/author/peer/account/RFC-ID are preserved. Numeric-only receipt gap fixed for Gmail only; Telegram/MAX IDs remain unchanged. Existing personal readonly stays readonly; new separate account-bound messenger_actions mail token is supported. Mail edits/reactions/typing/files not fabricated.

Actual connected MCP: both registered Gmail accounts configured/reply allowed, real sender identities distinct; history2 real messages PASS stored/freshfalse; exact RFC resolve PASS; events initialized at existing heads50982/50988 with no historical dispatch. No mail/TG/model/ACK business actions performed. Source/current runtime snapshot: `.tmp/mail-person-20261013/{verify,live-readiness}.json`.

Focused release101PASS39.18s/helper0/87.8MiB/swapOOM0/samegeneration cleanup. Initial99PASS+2new test assertions failed (RFC subject encoding and existing wrapped timeout exception), corrected against real wire/exception behavior; failed artifact retained. No accepted provider actions replayed. MIME wire fixtures prove actual From/To/Message-ID/In-Reply-To/subject/body, account scope, outgoing author, receipt reuse and UNKNOWN no-replay.

New QA mail input: normal POST /v1/conversations/conv_c469df75f0a50c91e69e4028/send with NEW body/request_id uses own careviolan→megamen932 account; then recipient Gmail ingress produces real new source. Generic reply uses that exact source+approval. Source IDs are RFC strings, not numeric guesses. Old existing aliases remain readable; new ingress uses registered canonical account IDs.

Existing TG540 QA path given separately: POST /v1/conversations/conv_904aae726d4a2a8c26d28916/send; same540 account/QA peer, no new login/session or substitution881.

Remaining acceptance owner: Exmanager Root01a11197, own native sender resource/linking/history plus ONE fresh Telegram→mail memory question and recipient-visible mail reply. SMTP delivery/person-first memory are NOT claimed from this readiness. Root owns sends; no UserIO wholeproject hold. Airlock core/Exmanager files not touched. This quick task remains incomplete until that actual native consumer receipt.
