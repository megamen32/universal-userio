# Phase17 live verification checkpoint

Status: INCOMPLETE. Owner: UserIO7bb; Secretary SDK integration belongs to Exmanager97.

| Requirement | Observed result | Closure evidence still required |
| --- | --- | --- |
| TM-01 Quiet automated join | Official managed44 browser joins with mic/camera off; native recorder hello accepted but no audio negotiation in five terminal attempts. | One normal UserIO start remains connected with actual received audio. |
| TM-02 Archive/transcription | Browser received two audio streams, real speech645695B, existing Whisper produced539characters. SHA and private copies verified. | Automatic archive and transcription from the UserIO start, owner playback/download and reload. |
| TM-03 Meeting chat | Not implemented; web/API explicitly show unsupported. | Actual meeting messages with verified author/source, reload and owner boundaries. |
| TM-04 Web/MCP/Secretary | Web start/error/reload accepted; unknown joins never replay. | Successful web audio journey and separate narrow Secretary recorder authorization plus actual Secretary start. |

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
