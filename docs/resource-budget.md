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

Broad tests must run one suite at a time. The initial bounded validation scope
uses at most 768 MiB RAM, 256 MiB swap, two CPUs, and 128 tasks. Increase these
limits only after measuring a legitimate failing workload, documenting the
host-reserve impact, and rerunning a real consumer canary.
