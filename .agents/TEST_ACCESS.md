# LHC test access — AgentCall Matrix voice

## Fixture owner and purpose

- Owner: project operator, issued through Matrix BotFather on server-88 at the operator's request.
- Purpose: isolated test identities for UserIO Matrix voice call behavior; these are not production users.
- Homeserver: `chat.bezrabotnyi.com` (Matrix on server-88).
- Fixture state: both BotFather accounts are active and joined to the dedicated two-user room below. A disposable MatrixRTC peer answered earlier scripted probes and then exited; there is no persistent answer client. The older owner-caller room and its historical results remain separate.

## Test identities

| Role | Matrix ID | Display name |
| --- | --- | --- |
| Test account A (not used in the latest call) | `@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com` | LHC Voice Caller Test |
| Peer / callee | `@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com` | LHC Voice Peer Test |
| UserIO call service | `@aiadmin:chat.bezrabotnyi.com` | Existing connected Matrix caller account |
| Owner caller (only for the explicitly authorized Secretary SDK check) | `@bezrabotnyi:chat.bezrabotnyi.com` | Existing owner Matrix account; never a QA peer |

## Test room

- Name: `LHC AgentCall test 2026-10-21`
- Room ID: `!WcdQRAMzHhrxcZzQad:chat.bezrabotnyi.com`
- Visibility: private; federation disabled; encryption disabled because the current MatrixRTC transport does not support encrypted rooms.
- Joined members verified from Synapse: `@aiadmin:chat.bezrabotnyi.com` and test peer B only.

## Two-User MatrixRTC room

- Room `!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com` was created by test account A for the two-UserIO-principal call test.
- It is private/invite-only, non-federated and unencrypted; the only joined members are test accounts A and B. The `org.matrix.msc3401.call.member` power level is 0 so both fixture users can publish their RTC membership events.
- The room is ready for the two-user call flow. Scripted calls and their limitations are recorded below; the per-user UserIO route has not yet been deployed or exercised in this room.
- These fixture rooms do not include `@bezrabotnyi` unless a live membership check proves otherwise. An owner-originated canary needs an already joined private room with exactly one QA peer; do not infer membership from the UserIO account link.

## UserIO test principals

- Two non-owner UserIO test users were created through the owner-only UserIO API on 2026-10-21 for the requested per-user Matrix voice test:
  - `lhc_voice_user_a_20261021` (`user_b0fa237eee8f4793aad037971be582a3`)
  - `lhc_voice_user_b_20261021` (`user_ee857eff8b2e45f3887a33db98441519`)
- Both are ordinary `user` principals. Their passwords and one-time API tokens are not recorded here; passwords were generated for this test and are not used for browser login. The manual caller helper now mints a full-access token in memory for the exact fixture user, gives it a ten-minute DB expiry, and revokes it in `finally`. No token was minted by the helper yet.
- Their BotFather Matrix identities are connected through each user's own UserIO principal:
  - User A → `matrix:ab023a689e926bf99d924e933ebd` → `@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com`
  - User B → `matrix:37698708b623db45e1bfe61f4d01` → `@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com`
- Both per-user AI settings were read back successfully: endpoint `https://omniroute.bezrabotnyi.com/v1`, model `omniroute/minimax-combo-m3.1`, key present. No provider key is copied into this document.

## Credential and authority boundary

- Matrix bot passwords and access tokens are stored only in BotFather's encrypted vault at `/home/roomhacker/matrix-synapse/matrix-botfather/data/secrets.enc`. The one-time UserIO API tokens for the two principals are no longer available; they are not stored in BotFather's vault.
- Do not copy credentials into this file, task notes, chat, logs, shell output, or UserIO fixtures.
- Use BotFather's owner-only credential and room commands for Matrix fixtures. For UserIO caller access, use `tests/manual/start_matrix_rtc_fixture_call.py` immediately before the authorized fixture API call; it never prints or stores the bearer token and revokes it after reconciliation.
- `@bezrabotnyi:chat.bezrabotnyi.com` remains the owner identity. For the owner's explicit synthetic Secretary SDK acceptance only, it may be the authenticated caller to one existing private QA peer; never use the owner account as the QA peer, never impersonate it, and never place a phone/cellular call.
- Do not use or invite `@hermesbot`.

## Allowed test journey

1. QA principals remain the two listed ordinary fixture identities. The owner account may originate only the explicitly authorized Secretary SDK test into one of those QA peers; it never replaces or elevates a QA principal.
2. Before a call test, confirm that the selected private test room contains the authenticated caller and exactly one QA peer, and that a real MatrixRTC-capable receiving client is active for that peer. Account issuance alone does not provide an RTC answerer.
3. Exercise UserIO's existing Matrix call path only; capture the exact UserIO call receipt and recording/transcript references without copying secrets.
4. Check turn boundaries, stale speech suppression, Russian pronunciation of names, and contextual answers. Keep the existing owner-call canary separate.

## Prohibitions and cleanup

- No calls to personal accounts, phone numbers, `@hermesbot`, or any non-fixture recipient.
- No public rooms, unrelated invitations, or production messages.
- Keep the accounts until the authorized test run is complete. Then use BotFather's delete command for these exact usernames and verify their removal from its registry and homeserver. Preserve the non-secret test receipts before cleanup.

## Issuance evidence — 2026-10-21

- Both accounts were read back active, non-admin, BotFather-managed, and with password/token presence confirmed in the encrypted vault; no credentials were exposed.
- At issuance both accounts had zero joined rooms; the peer was later added to the isolated room above.
- The BotFather source fix for the MAS/Synapse registration-order defect is published on `main` and deployed on server-88; the live container was read back running the new image on 2026-10-21. The two fixtures were issued through the corrected source handler.
- The original owner-call canary remains pending and was not replaced by these fixtures.

## MatrixRTC test calls — 2026-10-21

- Attempt 1 (`b53e1228-4401-5658-8296-081db97c0d34`, voice `f2082907-e335-4523-9964-8cfec16b3874`): accepted then `unanswered` / `TimeoutError`; no receiver client was online.
- Attempt 2 (`0040a758-8fc5-5394-8e18-599e8e46f3c8`, voice `3332210d-f3a7-4f21-9f06-64aec0ac0c5c`): the Element incoming-call card appeared, but joining was disabled by room state-event permissions. It ended failed; no audio was received.
- Root cause in the isolated room: `state_default=50` and no override for UserIO's MatrixRTC member event `org.matrix.msc3401.call.member`. Set only that event's permission to 0; readback confirmed, other room permissions were preserved. Element then enabled Join.
- Attempt 3 (`dab6de44-6bb5-5787-a499-a6c2f32a09cf`, voice `94de7cc0-bb81-4145-bacb-d384d2a2c8de`): completed with `answered=true`. The completed 48 kHz recording is `/var/lib/agentcall/recordings/94de7cc0-bb81-4145-bacb-d384d2a2c8de/assistant.wav`; it has 287 sent assistant frames and 0 received caller frames. Transcript: “Тест. Ты меня слышишь?” followed by “Пока не могу разобрать вашу речь. Повторите, пожалуйста.” The assistant transcript is visible in [UserIO](https://msg.bezrabotnyi.com/#/chat/conv_ed9ced03d47e7ef6b828e2a5?m=matrixcall-dab6de44-6bb5-5787-a499-a6c2f32a09cf-turn-1). The voice log confirms `audio_gate=True`, 87,000 greeting PCM bytes sent, a silence reprompt, and successful recording finalization. No caller speech was sent; the test browser microphone was disabled. Receipt carries a recoverable `voice_transport_error` warning, so this proves the answered path and sent TTS track, not a two-way speech exchange or what a human listener perceived.
- To load Element in the isolated server-88 Chromium, a temporary browser-only polyfill was needed for missing `Set.prototype.symmetricDifference`; no application source or deployed runtime was changed. Evidence screenshot of the initial Element failure: `/tmp/mxpeer-e2e-room-failed.png` on server-88. The temporary receiver browser session was closed.
- The original owner-call canary remains pending and separate.

## Scripted MatrixRTC speech probes — 2026-10-21

- These probes used only the isolated `@aiadmin` → test peer B room above. They did not call the operator's personal account or phone. A temporary, one-process LiveKit peer authenticated as test user B, published locally synthesized Russian PCM, and saved its received assistant track under `/tmp/lhc-matrixrtc-test-peer/assistant.wav` inside `userio-voice`.
- Probe `924876b8-191b-5d6e-8527-ab665571b645` (`8da16e1f-6372-4dc2-8a05-0a773cc3cec4`) was accepted and then stopped after the disposable receiver hit a harness argument-shape error before joining LiveKit. It ended `failed`; no speech was sent. No replay of this request.
- Probe `348d7f8e-1267-5b91-a129-d4aaf726275a` (`dc76387c-5f77-4bc4-8f78-f6dbf34c69ea`) answered and completed. The first harness version started speech 2 seconds after answer, overlapping the assistant greeting; treat it as a harness-invalid turn-boundary observation. Both 48 kHz recording tracks completed at `/var/lib/agentcall/recordings/dc76387c-5f77-4bc4-8f78-f6dbf34c69ea/`. Offline recording transcription found the final clause, but the live transcript has no caller text and the assistant asked the caller to repeat. Receipt carried `voice_transport_error` and `received_audio_bytes=0` despite the completed caller recording.
- Probe `f3467070-323f-57f7-bcff-485da7612b96` (`3b1182f6-94e0-4ec5-addd-19778b4612b2`) was the valid turn-separated replay: the receiver waited 9 seconds after answer, then sent 5.94 seconds of prerecorded Russian speech, and left the call open for 35 seconds. The call answered and completed; the assistant transcript contains the greeting followed by two “не могу разобрать вашу речь” prompts, with no live `speaker=remote` transcript. Recordings are `/var/lib/agentcall/recordings/3b1182f6-94e0-4ec5-addd-19778b4612b2/{caller.wav,assistant.wav,live-transcript.json,transcript.json}`. Offline recording transcription recovered “Скажи, пожалуйста, что ты слышишь?” from the caller track. A direct replay of that same recorded utterance through the installed `LocalSpeechPipeline.transcribe` with the running voice process configuration also returned that text. The live call still did not deliver recognized speech to the conversation loop (`no recognized speech; reprompt=True/False`).
- Evidence narrows the failure to the live turn-capture/recognition path or its live request timing; it does not yet prove which internal step loses the turn. The identical recorded PCM is recognized on replay, while the active call asks the caller to repeat. Do not mark speech acceptance complete or attribute this to Notice Place/Secretary mode.
- No call was started from either non-owner UserIO fixture principal: the non-owner admission/model-routing diff is still unpublished and undeployed. Its 15 focused tests pass, but the exact source revision has no valid native `review --manifest`; current backend/voice runtime therefore cannot prove either fixture's saved `omniroute/minimax-combo-m3.1` route. Keep both fixture users and the separate original owner-call canary unchanged.

## Current STT sample-rate regression — 2026-10-21

- Reused the existing synthetic peer utterance from probe `3b1182f6-94e0-4ec5-addd-19778b4612b2`. A clean 5.9-second mono 48 kHz clip is staged at `.tmp/lhc-voice-20261021/caller_phrase_initial.wav`, mode 0600, SHA-256 `e02a1c8e0efd85d61bb2e760e493997458b9d7d36cbb99c0ff738967fed9f2e0`.
- Discriminating live STT check with the gateway child's existing protected configuration: native 48 kHz clip transcribed to “Скажи, пожалуйста, что ты слышишь?”; the same clip after 48→8 kHz, wrapped as 8 kHz WAV, returned an empty transcript; 48→8→48 kHz also transcribed correctly. No key value was displayed or copied.
- Source now upsamples the VAD stream from 8 kHz to 48 kHz before constructing the STT WAV. This preserves the 8 kHz VAD path and matches the verified Whisper input format. A focused regression asserts 48 kHz serialization and the live gateway-configured STT request returns the expected phrase.
- This is a source-path and STT-endpoint proof only: current `userio-voice` still runs the earlier image, and no post-fix Matrix call has been placed.

## Offline MatrixRTC peer harness — 2026-10-21

- Added a test-only harness at `tests/manual/matrix_rtc_ai_peer.py` with regression coverage in `tests/test_matrix_rtc_ai_peer.py`.
- It reads a prerecorded mono PCM WAV, resamples to 48 kHz, and calls injected STT, draft, TTS, and publish functions in order. The focused test passed; the combined voice/Matrix suite passed 80 tests.
- The harness does not connect to MatrixRTC or LiveKit and does not authenticate as either fixture user. It proves only the offline audio-processing seam; the live receiving peer and the two UserIO principals' call path remain unverified.

## BotFather restart — 2026-10-21

- On server-88, restarted only `klub-control-plane.service` at 07:20:32 MSK after confirming its canonical unit, current listener, and active state. The separate LAN proxy unit was left running.
- Service returned `active`, `NRestarts=0`, opened loopback port 8092, and returned `{"service":"klub-control-plane","status":"ok"}` from its local health endpoint. No fixture account or room state changed during restart.

## Two-principal live preflight — 2026-10-21

- Read-only readback from the running UserIO backend confirms both fixture principals still have ordinary `user` role, their own enabled Matrix accounts, matching `/whoami` identities, and membership in the isolated A↔B room. Both saved models remain `omniroute/minimax-combo-m3.1`; both keys are present, but values were never printed.
- The deployed backend image is still `universal-userio-telegram-delivery:f18b6e05b0c9`. Its `matrix_calls.py` does not contain non-owner admission or the ephemeral per-user `voice_ai` route present in the current checkout. Therefore the two-user UserIO call cannot yet be launched through the live product path. No call was attempted in this preflight.
- The running Secretary voice identity map has no `matrix:` entry for either fixture user. `brain_for_call()` selects the Secretary SDK only by exact `transport:target` mapping. The live peer runner now creates a private temporary config copy that maps only the active fixture caller to the existing owner phone identity, then deletes it; the host identity file remains untouched.

## Live fixture receiver preparation — 2026-10-21

- Added `tests/manual/matrix_rtc_ai_live_peer.py`. It is an answer-only test runner for exact fixture B and the dedicated A↔B room. It loads B's existing Matrix credential and saved model in memory, connects to the active MatrixRTC call from A, and passes received audio to the existing `handle_call` engine. It never originates calls or prints credentials.
- The runner fails closed unless there is exactly one owner phone identity. It creates a `0700` temporary directory with `0600` config/map files, adds only the active fixture caller, and removes the files/restores its process environment after the call. It records the engine log with mode `0600` and reports only turn counts/modes; expected acceptance requires caller/assistant turns plus both Secretary and incidents modes.
- Runner coverage: both manual scripts pass `py_compile`; the focused combined suite now has 85 passing tests and one existing pytest config warning. This remains source/offline proof. The live backend still lacks the source route, so no live A↔B call was started.

## UserIO fixture caller helper — 2026-10-21

- Added `tests/manual/start_matrix_rtc_fixture_call.py` for the actual `/v1/calls/matrix` consumer route. It accepts only alias `a` or `b`; room, opposite fixture target, request ID, purpose, model alias and account identity are fixed and verified from the live UserIO DB/vault.
- Before the HTTP request it creates an in-memory personal token through the app's `PersonalTokens` helper, sets a ten-minute expiry, and revokes it in `finally`. Request IDs are stable per direction; a repeat reconciles the existing receipt and never POSTs again. A lost POST response triggers GET reconciliation only.
- This helper has not made a live call. Current API/voice images still lack the local route changes. The peer runner will create and clean up the narrow caller mapping within its own process for the mode scenario.

## Live two-principal MatrixRTC calls — 2026-10-21 (supersedes preflight)

- The per-user route was published and deployed. Both fixture users' saved model reads resolve to `omniroute/minimax-combo-m3.1`; test A→B completed answered as UserIO call `5ffc5399-7ca5-558b-9325-14aeb8b72f47`, voice `7a78de21-0941-4d18-83cb-5adc9eceaca6`, receiver `cf2c1a0a-2eae-49f0-a549-808dce01f8fb`. Test B→A completed answered as UserIO call `aae40541-7af2-5c01-8952-e7c03a701c31`, voice `df049200-88c0-4cd8-b5da-2c562b8cd8fc`, receiver `dba458e3-5119-427d-8c34-689eca256800`.
- Both directions produced complete 48 kHz caller and assistant WAV tracks. Consumer-side caller transcripts contain speech from the opposite Matrix fixture; receiver-side transcripts contain the caller's voice plus replies. Archives remain under `/var/lib/agentcall/recordings/` using the voice IDs above. No owner or phone call was made.
- The peer ran as a bounded guest-style voice engine, not as the Secretary SDK: a synthetic receiver call ID is not admitted by ExManager's durable voice-call ledger. The test does not prove Secretary↔NoticePlace mode transitions or contextual task answers. The two models produced a repetitive, filler-heavy conversation, so conversation quality is still open.
- Before backend commit `03cbace`, both call receipts reported `received_audio_bytes=0` despite complete caller WAVs. The deployed backend now calculates the field from the validated caller track; a focused regression covers it. The two archived caller tracks independently measure 22,816,940 and 23,157,618 PCM bytes; their historical call JSON is unchanged.
- The root-only Secretary API URL was stale: it targeted the Airlock frontend container rather than its API. It now targets the published loopback API port; the previous mode-0600 configuration is preserved at `/var/backups/userio-agentcall/secretary-voice-20261021.json`. The synthetic fixture calls still cannot validate a Secretary turn because ExManager requires an admitted call record.
- Separate Notice Place change `4395ea3` stops escalation after any answered Matrix/Telegram call; phone escalation proceeds only when all preceding calls are unanswered. It is deployed as release `20261021T082537Z-4395ea34158b-2480428`; both services are active, `/v1/instructions` returns 200, and `tests/test_incident_call_chain.py` passes 23/23. No live owner escalation was sent.
