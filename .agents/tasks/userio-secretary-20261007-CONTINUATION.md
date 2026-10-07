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

Adjacent chat refresh race fixed in owned App.tsx: an old action's refresh
cannot invalidate or replace a newly selected chat; read revocation cancels
its generation. Uses the existing LatestRequest primitive and an active-chat
ref; no provider/action semantics changed. Final UI proof remains pending.

## Exact remaining blockers, 2026-10-07 18:42 UTC / server-100

Current published source: UserIOd7749bf21f79028560955dd0ea3694c738b63ed9;
dispatcher2a6a900. No own source dirt. UserIO's three neighbor-owned validator
files remain untracked and preserved.

Correct UID gate is closed: memory full avg10 36.43%, avg60 30.54%, current
36,895,985,664 bytes against high38,654,705,664. No additional test/node/build/
browser/provider workload was started. The final seven backend regressions,
dispatcher selection, UI request tests/typecheck and static rebuild still need
one serialized bounded slot. Source AST/diff-check alone is not acceptance.

Mac source sync did NOT execute: canonical127.0.0.1:2222 refused TCP; documented
LAN recovery192.168.2.8:22 returned No route to host; fresh GPTAdmin discovery
reports shell:MacBook-Pro-User.local and its AgentBrowser/GrepMesh offline.
All11 dirty Mac source/generated files remain untouched. Late source bytes,
hashes and diffs were already saved in server100 ignored .tmp/mac-late-sources.
Prepared .tmp/mac-source-sync.py verifies exact11 hashes and baseline26344fa,
creates a private archive, reverse-applies only saved task changes, moves
owned untracked artifacts into the ignored backup, and only then pulls ff.
It rejects new dirt and active synchronization hooks. Run only after the
canonical bridge returns and revalidate its expected snapshot first.

Existing mac100 bridge watchdog failed at21:40:30MSK; audit last exited0 at
21:00MSK. Both advertise unbounded MemoryMax/CPUQuota/TasksMax, so no manual
start or budget mutation was performed under the user's no-limit-change rule.
Infra/root owns safe bridge recovery. Never change fleet-codex-watch or restart
native chats/daemon. Mac identity alias is user-confirmed; availability is the
blocker, not hostname.

Production UserIO and dispatcher still have PIDs3672069/3672766, NRestarts0,
start timestamps2026-10-06 23:15:59/23:16:03MSK. No deploy/restart performed.
After guarded checks: coordinated clean install preserving /opt foreign work,
exact release verification, final same-card accepted→link→result canary, then
Mac cleanup/sync and clean-main audit. Delivery is incomplete until these pass.

## Resumed read-only audit after the handoff

Fresh authoritative fetch confirms UserIO main0b5da296 and dispatcher
main2a6a900c. Both canonical working trees are now clean. The three neighboring
same-channel validator/contract files are tracked in0b5da296, still unwired;
the earlier untracked-file warning describes the previous snapshot. Their
publication does not provide native SMS/Matrix delivery support.

The resource gate is still closed: UID memory.current36,515,405,824 against
memory.high38,654,705,664, UID full PSI avg10=33.12/avg60=27.11, host memory
full avg10=1.30, swap4,292,198,400 of4,294,967,296 bytes. No tests/build/browser
or runtime mutation was admitted. Reverse Mac SSH2222 still refuses TCP.

The exact installed units remain active: universal-userio.service PID3672069
and userio-hermes-dispatcher.service PID3672766, both NRestarts0 and the same
2026-10-06 start timestamps. The dispatcher unit is userio-hermes-dispatcher,
not a hermes-unified-inbox unit. Source changes are not yet installed.

Smallest next action is the current seven-test secretary file under the
corrected existing shared gate, followed sequentially by the dispatcher
selection and UI checks. Root44 must restore usable reserve and Mac transport;
no independent source fix or safe deploy remains pending before those proofs.

## Mac restored and task checkout synchronized, 2026-10-07 20:18 UTC

The user reported Mac available. Reverse2222 still refused and LAN192.168.2.8
still returned No route, but fresh GPTAdmin discovery and an executed native
shell proved the canonical M1 online. Identity: MacBook-Pro-User.local,
LocalHostName MacBook-Pro-User, arm64, MacBookPro18,2. No transport configuration,
watchdog, native chat or shared daemon was changed.

All11 source/generated file hashes and baseline26344fa matched the saved
snapshot exactly. Before removing their task delta, a private ignored backup
preserved all files plus binary tracked diff, manifest, archive and sync receipt:
`.tmp/mac-source-preserved-20261007T201815Z` inside the Mac task checkout.
Archive SHA256a7788621ea632177c3e4b63f69c4c7a6724dc30d6e9ab80c1b00a01eab5fc484.
Only the exact verified task patch was reverse-applied; owned untracked
artifacts were moved into that backup. Full-history main then fast-forwarded
to published d597141 and was clean, matching server100/origin/main. Late source
semantics were already integrated; no saved work was discarded or published
as generated secret-pattern assets.

This was a light synchronization, not a test/build moved around the closed
server100 gate: source1,136,225 bytes, observed Mac Python peak RSS17,088,512
bytes, CPU cap30seconds, file cap16MiB, one native command with65second timeout.
An initial optional RLIMIT_AS cap was rejected by macOS before any file action;
the synchronization used supported CPU/file limits and exact bounded inputs.
Local receipt: ignored `.tmp/mac-sync-result-20261007.json`.

Mac availability and task-source preservation/sync are no longer blockers.
After publishing this handoff, perform a final light clean ff-sync to its
remote-main commit. Seven backend tests, dispatcher selection, UI tests/typecheck/
build and coordinated runtime/live proof remain gated by server100 reserve.

The separate Mac dispatcher checkout also had three late task changes beyond
the manifest. Full source was saved and merged in dispatcher3d2080e, retaining
cached summaries, exact event identity and retry sessions. Its added delivery
guards require exact durable progress receipts and the same native session,
and preserve both late restart/admission regressions. AST/diff passed; semantic
checks have not run. Native Mac original README/script/test are preserved in
private `.tmp/mac-source-preserved-20261007T203219Z`; clean ff-sync reached
dispatcherb9dd7ab on Mac/server100. Archive SHA256
faffac58efa0df566202408d5c7ac7aa29894dc3cc4c9191e87b9ead1014e56a.
UserIO Mac/server100 reached clean6b17e93 including the first preservation
handoff. Herder owner independently confirmed both its copies clean0681005.
Final light ff-sync includes these latest handoff commits. No test/build,
provider prompt, runtime install or restart was performed during this recovery.
