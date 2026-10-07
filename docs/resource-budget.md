# Universal UserIO resource budget

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
