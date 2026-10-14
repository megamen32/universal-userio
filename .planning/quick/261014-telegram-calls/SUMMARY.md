# Current outcome — 14 October

Reserve44 recordings: COMPLETE. Automatic importer bdff3d6 installed and enabled.
Both completed historical calls imported with stable IDs; repeat passes create
no duplicates. The latest human-confirmed clean-voice call passed actual headed
UserIO acceptance: both tracks, transcripts, playback progress, reload and exact
WAV downloads. Browser owner published e0fe1d8; evidence and accepted summary in
the sibling quick261014-reserve44-recording-ui task. Earlier inaudible call's
FAILED acceptance is preserved; importing its archive does not change that.

Telegram: COMPLETE for the authorized Secretary-to-owner call and UserIO archive.
UserIO6d9bbd7 supplies durable start/get/stop and authenticated two-track
playback. Gateway e99dd010 runs the fixed native worker inside its existing
budget. Offline ntgcalls3.0.3 install completed, peak263266304bytes, no swap/OOM,
native helper cleanup complete. No new provider sessions/credentials/accounts.

Actual MCP exposes all three tools and confirm:false refuses before provider
activity. Installed gateway worker rejects invalid recipient before any RPC or
synthesis, then closes its control stream. Source56PASS/1native opt-in skip,
actual GramJS constructor tests7PASS, gateway4PASS. Separately accepted paired
native48k test passed1.98s with both nonzero PCM directions and full cleanup.
Independent review found/fixed late dial after Stop and final-RPC teardown loss;
new regression tests cover both. Real recipient acceptance is recorded below.

Relay installation boundary is CLOSED: existing Secretary client seam f84822d
installed, exact caller/recipient capabilities200/configuredtrue and five RPCs.
Same session/token/mounts retained. Scoped Secretary MCP access ac4224c installed,
46focused checks and independent account-bound review PASS. Read-only stays get;
other-account start/get/stop, Matrix and legacy access stay denied.

Exactly one authorized actual attempt was submitted through UserIO MCP. Telegram
accepted its request and persisted the receipt. No answer/nativeCONNECTED
followed; the invitation timed out, final discard RPC completed and admission
CLOSED. No conversation audio was created. This does not prove recipient
delivery or voice quality. The initial local failed/TimeoutError result is
preserved; source now distinguishes unanswered invitation from media/provider
failure and reports a normal unanswered end in UserIO.

At00:03UTC14October the human asked not to call for three hours because they are
sleeping. No further calls/questions, automatic retries or wake-up call scheduled.
Recipient acceptance is explicitly deferred until the human returns (at least
03:03UTC/06:03Moscow). Resume only the actual dialogue/audio/transcript check;
do not repeat the first admitted call. Private receipts/hold:
.tmp/telegram-call-runtime-install-20261014/.

## Accepted fresh dialogue after the human returned

The human explicitly resumed testing and requested a Telegram call. One NEW
request was submitted through the installed UserIO MCP start tool; the first
closed attempt was preserved. Native transport connected, the assistant heard
the caller and answered. The human confirmed: “Да, поговорили, голос чистый”.
Local outcome completed/answeredtrue/no error; provider admission CLOSED with
its accepted receipt. No follow-up call was made.

Actual two-track archive COMPLETE: 48000Hz/mono/PCM16, equal1444949samples,
30.103104s, each WAV2889942bytes. Final transcript and transcription job COMPLETE.
Authenticated normal UserIO conversation contains the completed call with both
players/transcripts (37/341characters). Both byte-range reads206/RIFF and full
authenticated WAV downloads200 independently match the original track SHAs.
Initial probe expected peer_id in the summary list and a wrapper in the detail;
both diagnostic assumptions were corrected against the actual API shape.
No runtime/source changes, sends, read acknowledgements or call repeats occurred
during acceptance. Existing API/gateway processes retained NRestarts0.

Verdict PASS: real Telegram dialogue and authenticated UserIO archive consumer.
The archive UI component itself was already accepted in a headed browser for
Matrix/reserve44; this Telegram check used the real recipient plus HTTP archive,
not a new headed-browser run. No broader onboarding/Secretary migration claim.
Published producer candidate4898751 with existing relayf84822d and gatewaye99dd010;
private evidence canary02-start-result/canary02-final-relay-status/
canary02-userio-archive-proof.json in .tmp/telegram-call-runtime-install-20261014/.
Human call hold is revoked by the direct resume; no automatic repeat is scheduled.
