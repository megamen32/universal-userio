---
status: verifying
trigger: Owner requested commit, push and Telegram call; call dropped during connection
created: 2026-10-15
updated: 2026-10-15
---

## Assignment and acceptance

Preserve the quiet-hours/ownership repair and accepted unlocked Matrix dialogue.
Owner requested longer Telegram waiting and then an actual Telegram call.
Publish scoped main changes, install exact source, and establish real recipient
dialogue. No old accepted/unknown operation may be replayed. Assignment:
`.agents/tasks/quiet-calls-20261015.md`; prior accepted boundaries remain in
`resolved/quiet-calls.md`. Milestone17 is independent and preserved.

## Confirmed failure

New requests01–05 each reached a provider receipt, then failed local media setup;
all five relay admissions were confirmed CLOSED before a later NEW request.
Owner reported eight seconds of connection waiting after answering. Neither
the120s answer nor60s media deadline had expired. Do not infer no provider dial
from the local answered=false flag: relay ACCEPTED means a request receipt.

Native callbacks exceeded64 eager signaling tasks; outbound-only serialization
did not fix the incoming32-event cap or the byte backlog. Merely raising caps
also failed the live call. Keep these failures:9954bcc,9768feb,908776d.
Native8.0.0 offline reproducer measured53packets/207307bytes, largest10358bytes.
Source host has66interfaces. Earlier13.0.0 loopback had SEGV/ABRT and directly
fed an uninitialized engine; it did not model the actual connector path.
No claim of general13.0.0 recipient acceptance is made.

## Installed change

Published13735c2: bounded256KiB/256items per direction,8RPC cap, callback-side
admission backpressure, and per-update acknowledgements. An independent worker
admission task preserves RPC replies while the incoming queue is full. Native
updates start after connect_p2p initialization; PLAYBACK attaches afterward.
Queue caps and service cgroups remain finite; see voice-ownership.md budget.

## Verification and live boundary

- Fast unit and focused integration suite:32PASS/1optional native skip0.60s;
  expected40s/max400s aggregate768MiB cgroup. Purpose: finite queues, producer
  backpressure, full-input/RPC coexistence, durable admission and teardown.
- Native8.0.0 bidirectional PCM with100ms signaling replies: passed2.59s,
  expected3s/max30s scenario; no Telegram session or provider action.
- Runtime release manifest/install/verify succeeded; idle core restarted with
  zero live claims/RTC workers/open relay admissions. Dependent ingresses restored.
- NEW live request06:76b8e913-ccf4-556e-a918-97b82a48731f, voice
  411b94a6-5060-4534-bd06-2d1f3c6b3b97. At01:46Moscow provider receipt existed
  and invitation remained ringing. Actual media/dialogue acceptance pending.

No more call is scheduled. Wait for this invitation's recipient outcome; never
replay it. Record its final receipt and only claim the demonstrated boundary.

Final request06 receipt: unanswered, answered=false, no transport error;
relay CLOSED, provider_attempted=true, two RPCs (DH/request), no local discard
RPC. Provider sent Waiting then Discarded; saved final receipt63.0s after start
(includes preparation), before the local120sanswer deadline. A provider/client
ended invitation cannot be extended by this local deadline. No live workers or
open relay admissions remain. Real post-fix dialogue is blocked on receiving an
answer to a fresh owner-requested call, not on publication/install. Preserve
that exact boundary; do not redial while the recipient is unavailable.

## Independent dependency found during ingress restoration

SMS ingress is not healthy: Android SMS Gateway is active but `/v1/history`
returns503/native_history_unavailable. Its configured S21 Termux reverse-SSH
route127.0.0.1:22220 returns Connection refused, preventing native SMS history.
S21 lease is free; no interactive phone use or new lease was performed. This is
an external phone-transport dependency, not a Telegram call outcome. Existing
data/cursors preserved; do not claim all ingresses healthy. Restore the canonical
S21 reverse SSH before restarting/resetting SMS ingress and checking history.
No Android permissions, phone software or unrelated source was changed.
