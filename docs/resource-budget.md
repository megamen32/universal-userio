# Universal UserIO resource budget

The2026-10-09 continuation uses finite native systemd jobs under the unchanged
user-1000.slice bounds; it does not require a fresh human permission for each
case. The reviewed original-seven isolation/controller is retained and its
infrastructure owner maintains source bindings. Independent small jobs use
actual free memory, service health and new OOM counters; PSI alone is not a
universal admission threshold. No live harness/tenant is moved or stopped.

Measured owning continuation jobs, serialized per agent:

- UI App TypeScript:256/512MiB,CPU1,swap0,tasks64,wall90s;10.164s,
  peak219796KiB RSS. Vite assets:256/512MiB,CPU1,swap0,tasks64,wall120s;
  3.175s,peak304176KiB RSS. The build command constrains Rolldown workers2,
  blocking threads2 and Rayon1; CPU affinity/Rayon alone left80 spinning
  native workers and timed out twice. No global or runtime service caps change.

- UI six pure cases:128/256MiB RAM, swap0,CPU1,tasks64,wall30s,IO10,
  zero temp output;6PASS/runtime581ms/CPU586ms.
- SDK environment setup:128/256MiB RAM, swap0,CPU1,tasks64,wall120s,
  download/install/build concurrency1,IO10,64MiB/file,1GiB artifact and256MiB
  temp ceilings;13.7s,136312KiB RSS,swap0,213184512bytes installed.
- SDK five local-provider cases:256/384MiB RAM, swap0,CPU1,tasks128,
  wall120s,IO10,16MiB/file, project-local fixture temp;5PASS in7.80s,
  peak126772KiB RSS,swap0. This is below the512/768MiB proposed ceiling.

Setup and test units have exact kernel memory/CPU/swap/task/runtime limits
and are collected afterward. Evidence stays under owning `.tmp/`. Actual
MiniMax and the production service working set still require a consumer
check before changing the production128/256MiB budget below.

This budget applies to the server-100 production service and to test runs for
this project. It was reviewed on 2026-10-04 against a 125 GiB / 104 CPU host
with 45 GiB RAM available and 554 GiB free on the backing filesystem.

The production `universal-userio.service` steady working set was 34 MiB with
five tasks. Its enforced budget is:

- RAM soft/hard: `MemoryHigh=128M`, `MemoryMax=256M`;
- swap: `MemorySwapMax=64M`;
- CPU: `CPUQuota=100%` (one CPU worth of time);
- processes/tasks: `TasksMax=64`;
- disk/I/O: `IOWeight=50`; SQLite state remains under
  `/var/lib/universal-userio`, and temporary files use the private service
  namespace;
- GPU: none.

The Gmail ingress is a single polling Python process. Its canonical unit keeps
the provider worker within a separate smaller budget: 64/128 MiB RAM
soft/hard, 32 MiB swap, 25% of one CPU, 16 tasks, `IOWeight=25`, and no GPU.
These limits cover mailbox polling and HTTP ingestion without multiplying the
core service allowance.

The Matrix ingress is also a single polling Python process. Its canonical unit
uses 64/128 MiB RAM soft/hard, 32 MiB swap, 50% of one CPU, 32 tasks,
`IOWeight=25`, and no GPU. The higher CPU/task headroom covers Matrix sync JSON
processing while remaining inside the reviewed project budget.

Conversation context and summary caching remain inside the core service budget:

- one adaptive triage/summary request runs at a time per claimed event;
- summary input advances in exact pages of at most 100 messages and cached
  output is capped at 4,096 estimated tokens per eligible conversation
  (1,200 by default);
- direct Telegram dialogs are the only cache-enabled category by default;
  retention is 90 days and is enforced on startup, settings changes, cache
  access, and an hourly `HTTPServer.service_actions` sweep with no extra daemon
  or worker process;
- each model operation hydrates at most four image candidates (one shared
  attempt budget across the initial triage window and every read-more page), stops each bridge
  response at 2 MiB before allocation can grow further, and the AI adapter caps
  combined raw image bytes at 4 MiB (about 5.34 MiB after base64 encoding).
  Provider URLs, paths and tokens
  are never included in model payloads;
- all summary state stays in the existing WAL-backed SQLite database under
  `/var/lib/universal-userio`; no GPU is used.

Broad tests must run one suite at a time. The initial bounded validation scope
uses at most 768 MiB RAM, 256 MiB swap, two CPUs, and 128 tasks. Increase these
limits only after measuring a legitimate failing workload, documenting the
host-reserve impact, and rerunning a real consumer canary.

## Accepted continuation measurements —2026-10-09

The original7 used512/768MiB,CPU1,swap0,tasks128,wall120: actual54,927,360B
peak /4.48s /OOM0. Post-payload cleanup was reconciled through the existing
exact-generation recovery API; no test replay or foreign process stop.

Mirror/context/SDK30 selected cases used256/384MiB,CPU1,tasks128,wall120:
134784KiB RSS /20.132s /swap0. Node2 used64/128MiB,CPU1,tasks32,wall30.
Compiled App type/build passed with two native workers under512MiBhard.
Its297324KiB working set reclaimed heavily at256MiBsoft; future equivalent
App builds use384MiBsoft with the same512MiBhard/CPU1/tasks64/wall120.

Mac SDK update used nativeShellMCP120s timeout,90CPU-seconds,128MiB/file,
one download/install/build worker and no source compilation. Darwin rejects
RLIMIT_DATA; no kernel RAM hard bound is claimed. Measured installer159,727,616B
RSS and import101,924,864B fit the reviewed512MiB target with16GiB free reserve.
SDK upgrade occurred only after no liveSDK consumers were found; an APFS-cloned
old environment is preserved for exact rollback. No daemon/session restart.

Server88 SDK installer used native256/512MiB,CPU1,swap0,tasks64,wall120 and
64MiB/file:140912KiB RSS /33.995s. Consumer native imports used128/256MiB,
CPU1,tasks32,wall30:96040KiB /2.938s /swap0. Units collected, dependency
check131 compatible; previous0.10.13 environment is preserved. Physical reserve
was90GiB withmemoryPSIfull0. Shared host/user/service limits remain unchanged.

Actual UserIO cache118 messages and ordinary native analysis, plus existing
same-card deep consumer, passed inside the unchanged128/256MiB core budget.
Evidence: .agents/tasks/userio-original-consumer-proof-20261009.json.

## Telegram ingress measured online soft-budget repair —2026-10-09

`userio-telegram-ingress.service` uses soft224MiB/hard256MiB, swap64MiB,
CPU1/tasks64/IOWeight50. This changes only ingress MemoryHigh; the core
128/256MiB service and other project/UID/native boundaries retain their values.

Five1s samples proved the main Node thread blocked in kernel
mem_cgroup_handle_over_high at128MiBsoft: resident139MiB, swap63.8MiB,
combined202.64MiB, localfullPSI82–86%, high events+170/4s, OOM0. Hostavailable
41.08GiB supported soft224MiB without raising the hard bound. The authorized
existing native controller applied MemoryHigh224M online, preserving PID/start,
Invocation, auth/session-file metadata, pair guards and all other limits.
Within1s the mainthread returned toep_poll; highcounter stopped advancing,
currentaccountedmemory fell to65MiB, swap42MiB. ONE cached /state HTTP200
in7.88ms proved eventloop recovery; no operator-issued providerRPC or restart.

The auxiliary881 account was absent from that cached state: HTTP recovery
is accepted, auxiliaryconnected readiness remains unproved and separately
blocked. Do not infer receiver readiness from database account registration
or another eXmanager connection. No new auth/import/send was performed.

Persistent delivery updates this canonical unit and its installed equivalent
and uses only existing `systemctl set-property userio-telegram-ingress.service
MemoryHigh=224M` (no --runtime) to retain the same native setting over reboot.
No manager-wide daemon-reload/restart, core deploy, polling/session change.
Rollback is the recorded original unit plus MemoryHigh128M through the same
controller, only for a new verified unsafe change; do not reset a successful
repair to the already proven bad limit as a test cleanup. Protected receipts
stay under .tmp/pilot9-receiver-readiness; own task contains safe proof.
