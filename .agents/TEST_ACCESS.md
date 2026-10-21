# LHC test access — AgentCall Matrix voice

## Fixture owner and purpose

- Owner: project operator, issued through Matrix BotFather on server-88 at the operator's request.
- Purpose: isolated test identities for UserIO Matrix voice call behavior; these are not production users.
- Homeserver: `chat.bezrabotnyi.com` (Matrix on server-88).
- Fixture state: both BotFather accounts are active. Test peer B is joined with the existing UserIO Matrix service account in the private, unencrypted room below; test account A remains unjoined. A real MatrixRTC answer client is still not running.

## Test identities

| Role | Matrix ID | Display name |
| --- | --- | --- |
| Test account A (not used in the latest call) | `@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com` | LHC Voice Caller Test |
| Peer / callee | `@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com` | LHC Voice Peer Test |
| UserIO call service | `@aiadmin:chat.bezrabotnyi.com` | Existing connected Matrix caller account |

## Test room

- Name: `LHC AgentCall test 2026-10-21`
- Room ID: `!WcdQRAMzHhrxcZzQad:chat.bezrabotnyi.com`
- Visibility: private; federation disabled; encryption disabled because the current MatrixRTC transport does not support encrypted rooms.
- Joined members verified from Synapse: `@aiadmin:chat.bezrabotnyi.com` and test peer B only.

## Credential and authority boundary

- Passwords and access tokens are stored only in BotFather's encrypted vault at `/home/roomhacker/matrix-synapse/matrix-botfather/data/secrets.enc`.
- Do not copy credentials into this file, task notes, chat, logs, shell output, or UserIO fixtures.
- Use BotFather's owner-only credential and room commands; no direct login secrets are needed for routine verification.
- The operator account `@bezrabotnyi:chat.bezrabotnyi.com` is the owner only. Do not call, impersonate, or use it as a test peer.
- Do not use or invite `@hermesbot`.

## Allowed test journey

1. Use only the two listed identities on the listed homeserver.
2. Before a call test, confirm a private test room and a real MatrixRTC-capable receiving client for the peer identity. Account issuance alone does not provide an RTC answerer.
3. Exercise UserIO's existing Matrix call path only; capture the exact UserIO call receipt and recording/transcript references without copying secrets.
4. Check turn boundaries, stale speech suppression, Russian pronunciation of names, and contextual answers. Keep the existing owner-call canary separate.

## Prohibitions and cleanup

- No calls to personal accounts, phone numbers, `@hermesbot`, or any non-fixture recipient.
- No public rooms, unrelated invitations, or production messages.
- Keep the accounts until the authorized test run is complete. Then use BotFather's delete command for these exact usernames and verify their removal from its registry and homeserver. Preserve the non-secret test receipts before cleanup.

## Issuance evidence — 2026-10-21

- Both accounts were read back active, non-admin, BotFather-managed, and with password/token presence confirmed in the encrypted vault; no credentials were exposed.
- At issuance both accounts had zero joined rooms; the peer was later added to the isolated room above.
- The BotFather source fix for the MAS/Synapse registration-order defect is published on `main`; the running service was not restarted or updated. The two fixtures were issued through the corrected source handler, so do not treat this as a production BotFather repair.
- The original owner-call canary remains pending and was not replaced by these fixtures.

## MatrixRTC test calls — 2026-10-21

- Attempt 1 (`b53e1228-4401-5658-8296-081db97c0d34`, voice `f2082907-e335-4523-9964-8cfec16b3874`): accepted then `unanswered` / `TimeoutError`; no receiver client was online.
- Attempt 2 (`0040a758-8fc5-5394-8e18-599e8e46f3c8`, voice `3332210d-f3a7-4f21-9f06-64aec0ac0c5c`): the Element incoming-call card appeared, but joining was disabled by room state-event permissions. It ended failed; no audio was received.
- Root cause in the isolated room: `state_default=50` and no override for UserIO's MatrixRTC member event `org.matrix.msc3401.call.member`. Set only that event's permission to 0; readback confirmed, other room permissions were preserved. Element then enabled Join.
- Attempt 3 (`dab6de44-6bb5-5787-a499-a6c2f32a09cf`, voice `94de7cc0-bb81-4145-bacb-d384d2a2c8de`): completed with `answered=true`. The completed 48 kHz recording is `/var/lib/agentcall/recordings/94de7cc0-bb81-4145-bacb-d384d2a2c8de/assistant.wav`; it has 287 sent assistant frames and 0 received caller frames. Transcript: “Тест. Ты меня слышишь?” followed by “Пока не могу разобрать вашу речь. Повторите, пожалуйста.” The assistant transcript is visible in [UserIO](https://msg.bezrabotnyi.com/#/chat/conv_ed9ced03d47e7ef6b828e2a5?m=matrixcall-dab6de44-6bb5-5787-a499-a6c2f32a09cf-turn-1). The voice log confirms `audio_gate=True`, 87,000 greeting PCM bytes sent, a silence reprompt, and successful recording finalization. No caller speech was sent; the test browser microphone was disabled. Receipt carries a recoverable `voice_transport_error` warning, so this proves the answered path and sent TTS track, not a two-way speech exchange or what a human listener perceived.
- To load Element in the isolated server-88 Chromium, a temporary browser-only polyfill was needed for missing `Set.prototype.symmetricDifference`; no application source or deployed runtime was changed. Evidence screenshot of the initial Element failure: `/tmp/mxpeer-e2e-room-failed.png` on server-88. The temporary receiver browser session was closed.
- The original owner-call canary remains pending and separate.
