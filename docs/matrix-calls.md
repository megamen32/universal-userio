# Matrix voice calls

Matrix calls use the existing AgentCall voice engine and its current settings.
UserIO supplies Matrix signalling and the audio transport. Each completed call
appears in the owner's Matrix conversation with separate received/sent audio
players and a transcript beneath each player.

The optional `matrix-calls` extra installs the LiveKit native SDK. AgentCall must
already be running on the same deployment. Its model, speech speed, interruption, hangup and recording/transcription
path are reused. Matrix does not use a phone. When the telephone profile selects
Android TTS, Matrix uses the existing server Silero speech endpoint instead;
the telephone profile is unaffected. The owner-selected server quality uses
native24kHz TTS → one whole-phrase tempo pass →48kHz Matrix audio. Audio
packets are framed after that pass; there is no intermediate8kHz conversion.
Both recording tracks retain48kHz. The existing VAD/STT receives its8kHz
analysis copy, which is never played or substituted into the recording.

Use `userio.calls.matrix.start` with `account_ref`, an existing two-person
`room_id`, the recipient's `target` Matrix ID, `purpose`, a stable `request_id`,
and explicit `confirm:true`. `userio.calls.matrix.get` reads the same receipt;
`userio.calls.matrix.stop` ends that call. The HTTP equivalents are
`POST /v1/calls/matrix`, `GET /v1/calls/matrix/{call_id}` and
`POST /v1/calls/matrix/{call_id}/stop`.

Keep the same request ID after a timeout. Accepted, active or unknown work must
never be replayed with another request ID. This first deployment binds the voice
engine to the UserIO owner; account secrets remain in the existing connector vault.

Notice Place can use its existing `matrix.call` adapter with
`/v1/calls/matrix/noticeplace` and the fixed `USERIO_MATRIX_NOTICE_*` configuration.
Its existing bridge token is accepted only by that endpoint. Change the live
Notice Place route after the real recipient dialogue and recording acceptance.

Current limitation: the raw MatrixRTC transport rejects encrypted rooms because
MatrixRTC E2EE key exchange is not implemented. It never downgrades a room's
encryption. A transport connection is not proof of a confidential call.

Call admission remains in UserIO's existing budget. The private control socket
starts the fixed media worker inside the existing AgentCall gateway budget
(2GiB RAM, 512MiB swap, 2 CPUs, 128 tasks), inheriting its effective voice
configuration. One Matrix call runs at a time, up to ten minutes of conversation.
The existing bounded private recording archive and quota apply.
