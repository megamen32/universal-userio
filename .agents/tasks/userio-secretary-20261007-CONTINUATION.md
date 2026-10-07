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
The ru_maxrss report (852,860 KiB) is inconsistent with the configured
384MiB address-space cap; its cause is unverified and it is not used as a
workload peak measurement. Subsequent wrapper records /proc VmHWM/VmRSS
and the resolved limit directly; verify the peak on the next allowed run. Dispatcher check was gated out before pytest when PSI
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

Source publication confirmed: UserIOdb6c30b2999daeef110d6b57f333a4613bcace00
and dispatcher aa0f669eea7936233b714da6862d44da8c3b534e equal origin/main.
Dispatcher checkout is clean. UserIO has exactly three neighbor-owned
untracked validator/contract files, no own dirt. Compiled UI assets, semantic
dispatcher/UI checks, deploy, final business canary and Mac cleanup remain
required and are blocked by corrected UID reserve gate. Fresh UID memory
38,152,409,088 bytes, full PSI avg10 16.18/avg60 16.73: gate closed.

## Late Mac context beyond the staged manifest

Fresh read-only Mac source comparison found late http/service/store/App changes
and one request-gate test after the manifest capture. All snapshots/diffs are
saved in ignored .tmp/mac-late-sources and .tmp/mac-late-*.diff. The existing
late request-gate test was preserved in published79018ed. Exact failed-job
retry_of_job_id, immutable message_id, parent_job_id history and predecessor
migration are now merged with the server snapshot/claim/UI fixes. Two migration
regressions were added; the current seven-test backend file has NOT run after
this semantic delta. The previous five-test result does not verify it.

Reviewed corrections to the late migration: root partial index is created only
after parent_job_id exists; partial indexes do not look like old event-only
uniqueness; intermediate attempt history gets exact predecessor links and its
immutable journal message ID. Reopen must leave schema_version unchanged.
Retry rechecks UserIO policy for the exact failed event before creating a child.
The UI sends the precise failed job and keeps done/active state scoped to the
latest message, so completed old work does not disable a new incoming message.

Source AST/diff-check pass only. Resource gate remains closed; no further
pytest/node/build/browser/deploy/provider workload has started. Before Mac
cleanup, publish this late slice, prove its baseline/reachability, preserve all
11 dirty source/generated files with hashes in a private ignored backup, and
then ff-sync without overwriting any unowned path.
