# Phase17 live verification checkpoint

Status: IN_PROGRESS, TM-01/TM-02 accepted; 2/4 requirements (50%). Owner: UserIO7bb; Secretary SDK integration belongs to Exmanager97.

| Requirement | Observed result | Closure evidence still required |
| --- | --- | --- |
| TM-01 Quiet automated join | ACCEPTED: one NEW normal UserIO start connected with mic/camera off, actual RTP and34.739s voice WAV. | Closed by native receive and normal web proof. |
| TM-02 Archive/transcription | ACCEPTED: automatic archive1111692B WAV and98character real speech transcript; headed44 playback progresses, reload persists, native download SHA matches. Browser manual clip remains separate. | Closed for this bounded single-speaker journey; mixed streams are not asserted to identify individual people. |
| TM-03 Meeting chat | User explicitly requests ALL personal/group chats of the same Yandex account already linked for mail; not implemented. | New ordinary owner-verified Yandex sign-in, personal/group history, live sync and normal UserIO read/send proof. |
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
not sent for transcription. Current completion notification still counts it when
announcing transcript readiness; this is an owned pending human-status defect.
Fix it in the next common API source cycle, with atomic call drain before restart.
