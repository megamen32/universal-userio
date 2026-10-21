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

## MatrixRTC call attempt — 2026-10-21

- One explicitly authorized UserIO test call from `@aiadmin` to peer B was accepted, then ended `unanswered` with `TimeoutError`. `answered=false`, `acknowledged=false`, and `received_audio_bytes=0`; no audio or transcript was produced.
- UserIO call receipt: `b53e1228-4401-5658-8296-081db97c0d34`; underlying voice call: `f2082907-e335-4523-9964-8cfec16b3874`.
- This confirms the call request reached the MatrixRTC wait path, but does not prove an answered call or TTS playback. No retry was made. The original owner-call canary remains pending and separate.
