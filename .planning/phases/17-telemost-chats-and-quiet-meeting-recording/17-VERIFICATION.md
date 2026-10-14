# Phase17 live verification checkpoint

Status: IN_PROGRESS, TM-01/TM-02/TM-03 accepted; 3/4 requirements (75%). Owner: UserIO7bb; Secretary SDK integration belongs to Exmanager97.

| Requirement | Observed result | Closure evidence still required |
| --- | --- | --- |
| TM-01 Quiet automated join | ACCEPTED: one NEW normal UserIO start connected with mic/camera off, actual RTP and34.739s voice WAV. | Closed by native receive and normal web proof. |
| TM-02 Archive/transcription | ACCEPTED: automatic archive1111692B WAV and98character real speech transcript; headed44 playback progresses, reload persists, native download SHA matches. Browser manual clip remains separate. | Closed for this bounded single-speaker journey; mixed streams are not asserted to identify individual people. |
| TM-03 Yandex personal/group chats | ACCEPTED: ordinary same-account sign-in, exact Whoami identity, 12 chats, private/group authors and text, permanent polling/backfill. One authorized normal web send confirmed by native receipt/history; reload/reopen retains the exact body. | Closed for the connected owner account. Arbitrary-user enrollment and rich actions are separate unaccepted follow-ups. |
| TM-04 Web/MCP/Secretary | Normal web start and actual automatic audio/play/reload/download accepted. | Separate narrow Secretary recorder authorization and actual Secretary-started journey, owned by Exmanager97 after UserIO contract readiness. |

The browser clip is separate from failed native records. No synthetic audio or
historical clip is attached to them. Actual native failures remain retained.
The bounded browser scope and cgroup are gone, remote audio/PNG copies removed,
local owner-private audio/transcript preserved. No accepted transcription replay.

Native heartbeat now waits for serverHello, matching the official SDK. A real
loopback WebSocket with a six-second HTTP delay passed18.483s inside512MiB/swap0/
CPU1/tasks128/max180s; this is integration proof, not provider recovery.

A shared API restart raced new NoticePlace Matrix admission and interrupted its
caller. That uncertain call remains non-replayable. UserIO7bb owns the restart
race: future common restarts require an atomic admission/drain boundary, not
only a file preflight. No common API restart during the peer's new canary.

## Corrected native consumer and retained defects
Premature heartbeat raced the serverHello handshake. Deferring it until handshake
completion produced the first real subscriber offer/RTP on the next unique job.
The first readonly UI assertion failed on the native record's partial flag; a
second changed readonly consumer preserved that flag and proved audio/ASR/playback/
reload/download without another join or inference. The historical partial flag
and MediaStreamError remain; metadata was not rewritten to manufacture GREEN.
Requested PC shutdown has since been distinguished from unexpected media end in
source and the native artifact (two focused checks474ms). No new real join is
claimed for that small end-classification fix.

The secondary SFU lane had only a44byte empty WAV header. It is not voice and is
not sent for transcription. Completion notification now excludes empty lanes (published9c1bdf7 and installed).
No accepted meeting or transcription was repeated for that correction. The atomic
common API admission/drain defect remains open independently.

## Yandex native and normal web acceptance — 14 October
Published and installed9fba9ed: one existing owner-bound Yandex connection reads
personal and group chats via the official Xiva wire protocol. Verified account
identity precedes persistence; credentials and OTPs never enter the repository.
Existing account poller repeatedly advances live sync and fairly backfills older
history. Native system/control frames are not fabricated as human messages.

The first UI exposed dict-shaped text and reversed private/group classification.
Native Text.MessageText, PartnerInfo/ChatInfo and real From identity are now
projected correctly; second headed44 consumer passed text/authors/reload.
A retained focused run had9PASS/1FAIL (obsolete backfill fixture); corrected
fixture plus projection regression passed2focused checks. Sender checks2PASS;
normal production web build passed3.48s. None substitutes for provider proof.

User supplied one test recipient. The normal web Send button caused exactly one
send request. Native push receipt and exact history verify own author, PayloadId,
timestamp and full body; headed UI reload/reopen retained that message. Three
earlier harness failures all recorded send_attempts=0; only the final corrected
consumer sent. Unknown submission retains the standard durable draft claim and
cannot resend. Private proof: .tmp/telemost-20261014/yandex-web/send-web-proof.json.

Runtime manifest verification passed after capture; API NRestarts0. All four
owned sender browser scopes/Chrome/Xvfb/configs and remote PNG copies are gone;
private screenshots and safe receipts remain outside tracked Git. Provider ACK,
AI calls, new meeting joins and unrelated preference writes were blocked.

Remaining TM-04: UserIO7bb owns a narrow recorder credential contract;
Exmanager97 owns the separate native Secretary adapter. Closure requires one
new authorized Secretary-started meeting with silent join, real incoming voice,
audio/transcript receipt and ordinary UserIO reload, without upgrading readonly.
Yandex messenger.* integration, attachments/edits/reactions and all-user sign-in
are not advertised as accepted by this text-send checkpoint.
