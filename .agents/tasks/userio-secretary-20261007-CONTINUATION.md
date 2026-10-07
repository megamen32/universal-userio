# UserIO caching / secretary continuation

Original Mac thread: `01a116bf-61ec-73e2-adb8-ba586475a8f2`.
Current server-100 owner: `01a11747-7bb9-7bc0-8c0a-ce64db49105d`.

## Scope and restored state

UserIO summary cache/adaptive context baseline `26344fa` is published and now
fast-forwarded in the canonical checkout. Remaining staged secretary backend/UI
and hermes-unified-inbox dispatcher were restored without runtime mutations.
Manifest file hashes verified. Airlock core and same_channel_reply.py excluded.
Account 2 generic polling remains held; normal Secretary replies belong to
neighbor `01a11744-d8f4-7293-87be-602a32a41aec`.

## Herder overlap agreement, 2026-10-07

This owner has made no edits to Agent Herder, including src/web/server.ts.
Owner `01a11747-c694-7340-be48-f973e3c4e568` confirmed that our staged
managed/archive/readOnly semantics are integrated into his conflict resolution.
That owner coordinates the committed Herder slice with root
`01a11723-ff2a-7be3-8f15-015b6a7af066` and its fleet-load executor.
Preserve the executor's new safe active-adopt/arm guard in session-supervisor.ts
and any agreed minimal route addition. Do not interrupt the nine native parent
chats or restart the shared Codex daemon. Control-plane restart belongs after a
coordinated reviewed commit. UserIO owner does not authorize overwriting any
Herder neighbor's changes.

## Pending proof and smallest next action

Global resource gate: only bounded reads/edits until root confirms reserve.
No heavy tests, builds, browser or live canary have run in this continuation.
Finish restored review defects (UI read/action race, exact event snapshot,
immutable job identity and explicit retry), then ask root for one targeted test
slot. Commit/push the scoped reviewed source before deployment. Runtime
/opt/universal-userio contains neighbor-owned dirt; preserve and coordinate its
same-channel files before any managed release install. Herder runtime is still
old/readOnly, so deep live canary must wait for that owner's published deploy.

## Current lightweight implementation delta

- Preserved original event snapshot across arrivals during triage.
- Stored source/account/peer and attempt in each immutable deep job.
- Explicit failed retry creates a new job, retaining old worker/result state;
  dispatcher uses a separate named session for attempt 2+.
- Atomic BEGIN IMMEDIATE claim prevents two store connections claiming a job.
- UI separates read/action generations, blocks polling during POST, exposes
  failed GET with retry, and invalidates old chat action completion.
- Dispatcher passes cached summary into the deep prompt and checks stored
  account/peer against hydrated input before inference.

2026-10-07 17:23 UTC reserve check: UID MemoryCurrent 37,995,388,928 bytes,
MemoryHigh 38,654,705,664; memory PSI full avg10 11.39%. Gate closed. Targeted
Python/UI regressions written but not executed. Next action is one serialized
bounded targeted run after measured reserve proof, then review/publish.

Shared workload guard discovered at `/run/user/1000/server100-heavy-workload.lock`.
The focused proof wrapper takes that exact flock and checks UID reserve >=2 GiB,
memory/io full avg10 <1 before importing pytest. It enforces 384 MiB address
space, 30 CPU seconds, two CPU affinity slots, 16 MiB/file, no plugin autoload,
and project-local temp. One test file only (fixed two-thread claim race).
First attempt exited 75 before pytest: reserve 5,463,031,808 bytes but memory
full avg10 7.65%, IO full avg10 1.6%. No test workload started.

Server-100 native Fast Agent shared tool runtime0.10.42 matches fresh PyPI.
Mac source is preserved in the manifest; reverse route now reports
MBP-User.lan rather than required MacBook-Pro-User.local. No Mac mutation,
package update or final Mac synchronization is authorized by that identity
canary; root/topology owner must reconcile identity before those steps.

Neighbor Secretary owner copied and owns only same_channel_reply.py, its
contract test, and contract tracker in canonical UserIO. Keep those paths
un-staged in this task's source commit; all three are preparatory, unwired.

## User-confirmed Mac identity alias

The user explicitly confirmed that `MBP-User.lan` and
`MacBook-Pro-User.local` identify the same canonical Apple-silicon Mac.
The earlier hostname mismatch blocker is withdrawn. Keep the durable reverse
SSH endpoint `127.0.0.1:2222`; do not rename the Mac or change its transport.
This clarification was broadcast through a shared coordination note and sent
to the five original task owners/root. Resource gates remain unchanged.

## Executed targeted backend proof

One serialized run passed the shared flock/reserve gate (reserve4,192,772,096
bytes, memory full avg10 0.98%, IO full0%). Five secretary tests passed in
3.39seconds: exact identity, owner/service HTTP scope and read deny, new-message
snapshot race, explicit retry history, cross-connection exclusive claim.
Address-space ceiling384MiB, CPU30seconds, two CPU affinity slots enforced.
The ru_maxrss report includes pre-exec historical process high-water and is
not used as a workload peak measurement; subsequent wrapper records /proc
VmHWM/VmRSS directly. Dispatcher check was gated out before pytest when PSI
returned to2.47%; no broad suite ran. UI test/type/build remain pending.

## Resource gate correction

The initial focused wrapper checked HOST memory/io PSI only. Neighbor proved
UID full pressure can stay high while host PSI is low; the first five-test
result is functional proof, NOT proof of UID reserve. No further workload is
allowed on that old predicate. Corrected gate also requires UID
`memory.pressure` full avg10<1 and avg60<3, in addition to>=2GiB below
MemoryHigh, host memory/io full avg10<1, and the same shared lock. No limits
are raised or bypassed. ru_maxrss exceeded the address-space cap and is not
accepted as a workload peak; collect /proc VmHWM/VmRSS on the next allowed run.

Backend scoped commit067abef88bf22fb36390b9e3ce4aff87215d1bcc is published
to authoritative main. SSH443 push was rejected before handshake; per-command
HTTPS with existing gh credential helper succeeded, no global Git/auth/bridge
configuration changed. Remaining UI and dispatcher sources are reviewed but
not behavior/type/build verified and not deployed. Final delivery is incomplete
until those checks, coordinated clean runtime install, Herder readiness, same
card live canary and Mac source synchronization pass.
