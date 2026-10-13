---
type: execute
mode: quick
status: complete
---
# Matrix AI voice calls in UserIO

Owner: this native Codex session, canonical UserIO checkout on main.
Accepted outcome: The connected test caller calls the owner, converses in Russian, and UserIO shows separate received/sent audio with transcript directly beneath each player. Notice Place uses Matrix before the existing phone fallback. Telegram is the next stage.

1. Reuse the existing MatrixRTC/LiveKit authorization, existing AgentCall handle_call engine and its effective live configuration, per-user Matrix secrets, authenticated HTTP/MCP and call-audio UI. Durable request IDs prevent replay after unknown outcomes or restarts.
2. Verify focused admission/recipient/audio regressions and build the UI. Fast unit expected 1s/max 10s; focused integration expected 15s/max 45s. Expected durations are estimates; emergency wall limit is ten times the expected duration. Existing sealed cases keep their budgets. A controlled real recipient dialogue and browser playback are required acceptance; source GREEN is insufficient.
3. Deliver scoped main changes through the existing runtime_release manifest and rollback route, wire the existing Notice Place matrix.call adapter, then run one authorized AIadmin-to-owner call. Inspect recipient acknowledgement, both audio tracks, inline transcripts, reload and downloads.

Current limitation: existing test DM is unencrypted. Encrypted MatrixRTC key exchange is not implemented by the raw media adapter. It rejects encrypted rooms and never downgrades their encryption. Do not claim confidential production acceptance without resolving this boundary.

Preserved pre-existing WIP at entry: docs/guide/max.md and src/universal_userio/static/connect-accounts.html (later committed independently by their owner).

User clarification: do not build another voice dialogue. Matrix is only a transport around the existing preparation/handle_call engine, preserving TTS/model/tempo/VAD/barge-in/recording/transcription settings. No new call-start UI.

Final accepted voice: existing server Silero native24k, whole-phrase tempo1.35,
then48k Matrix transport/archive. The owner selected this sample and accepted
the fresh dialogue after removal of the extra relative20ms sender sleep.
NoticePlace's existing adapter is now configured to the local UserIO route;
its token, escalation timings and phone fallback are preserved. Telegram and
the subsequently requested telephone pacing check remain separate follow-ups.
