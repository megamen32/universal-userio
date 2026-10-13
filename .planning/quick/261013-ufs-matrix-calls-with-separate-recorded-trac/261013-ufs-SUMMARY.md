---
type: summary
mode: quick
status: complete
---
# Matrix AI calls accepted

The real connected AI caller rang the owner, held a Russian conversation,
and closed after the owner's farewell. UserIO exposes separate caller/AI
recordings and transcripts using its existing private call-audio surface.
The existing NoticePlace adapter returned a genuine acknowledged Matrix
dialogue and now targets the local UserIO endpoint. No old send was replayed.

The owner initially rejected the sound, selected blind sample3, then rejected
the first48k call too. Its transcript records persistent hiss with VPN off and
after switching to4G. The confirmed defect was the relative20ms pause added
after each RTC capture: actual SDK elapsed1.0463s per1s PCM; removal restored
1.0008s. The fresh call was accepted: “Нет, не осталось, всё прекрасно”.
Both48k mono recordings are complete at37.4905s, final transcription COMPLETE,
both transcripts returned in the authenticated conversation and both WAV
header range reads returned206/48k. The initial real browser acceptance covered
both players, progress, transcripts, reload and range downloads. New unbounded
browser launches were refused after discovering the shared launcher's missing
cgroup bounds; that infrastructure defect remains in its owning tracker.

Validation:58 selected engine/recording/Matrix tests8.917s;33 existing voice
stream regressions8.904s;17 UserIO call/admission/recording tests1.36s; focused
RTC tail regression4tests1.116s. Source tests invoke no device, model or external
transcriber. Review additionally found late interruption during final RTC-tail
playout; the fix observes live flags and clears the bounded queue.

Scope limits: E2EE rooms are rejected without downgrade; encryption key exchange
is not implemented. Telegram calls remain a separate stage, using the existing
Secretary client owned by Exmanager. The user requested a scoped published tag
for this Matrix milestone, then a telephone-quality check under the shared lease.

Public release notes: docs/releases/matrix-calls-v1-2026.10.13.md.
Private call/HTTP/source/rollback proofs: .tmp/matrix-calls-20261013/.
