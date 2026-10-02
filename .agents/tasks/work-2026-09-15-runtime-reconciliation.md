# Canonical/runtime reconciliation — v1.1 Phase 7

- Status: complete
- Canonical checkout: `/home/roomhacker/agents-projects/universal-userio`
- Canonical HEAD at audit: `cd9e4c7e9ee1d6fb79ffe6c025a7b3598010534a`
- Managed runtime: `/opt/universal-userio`
- Runtime Git HEAD: `d58dad08e376cb5a97ee6eb63f72115e476d7d5a`
- Runtime status snapshot: 2,482 entries before any runtime mutation

## Command

`python3 scripts/runtime_reconcile.py --canonical . --runtime /opt/universal-userio --policy deploy/runtime-reconciliation-policy-v1.1.json --output deploy/runtime-reconciliation-v1.1.json --require-clean-resolution`

The command completed with `unresolved_count: 0`. SHA-256 of the complete
NUL-delimited runtime status was identical before and after the command, so the
audit did not mutate the managed checkout.

## Reconciled counts

| Category | Count | Disposition |
| --- | ---: | --- |
| generated dependency | 2,365 | excluded runtime artifact |
| generated build | 25 | excluded runtime artifact |
| backup | 14 | excluded runtime artifact |
| diagnostic state | 1 | excluded runtime artifact |
| secret/session | 23 | excluded secret; content and path not emitted |
| source | 47 | reviewed against canonical source |
| config | 7 | reviewed against canonical source |
| **Total** | **2,482** | **0 unknown, 0 unresolved** |

Disposition totals are 2,406 excluded runtime artifacts, 23 excluded secrets,
38 already-canonical files, and 15 explicit canonical-source decisions.

The refined classifier differs from the preliminary reconnaissance because it
correctly recognizes timestamped `*.bak.*` files and `*.egg-info` trees before
the generic source suffix rules. The total stayed exactly 2,482.

## Reviewed source/config deltas

- Preserved from runtime: the active
  `deploy/userio-agentcall-ingress.service` and its historical
  `.agents/tasks/ai-calls-userio.md` evidence record.
- Canonical remains authoritative for `.env.example`, `.gitignore`,
  `pyproject.toml`, the BYOK lockfile, HTTP/MCP formatting-only deltas, and the
  AgentCall/Android tests.
- Canonical `agent_channel.py`, `chatgpt_sessions.py`, and `vault.py` contain
  stronger bounded-result, per-user namespace, and private-permission behavior
  than the runtime copies.
- Runtime `src/universal_userio/email.py` is an unreferenced duplicate of
  canonical `src/universal_userio/channels/email.py` and is excluded as a
  superseded runtime artifact.

Every safe-path decision and its reason is machine-readable in
`deploy/runtime-reconciliation-policy-v1.1.json`. Secret/session entries in the
ledger contain only a domain-separated path identifier, presence, and mode; no
secret path, content, or content digest is stored.

## Acceptance evidence

- Focused audit suite: 7 tests passing after the unreadable-generated-file
  regression was added.
- Active AgentCall systemd unit: preserved byte-for-byte in canonical source.
- Runtime mutation: none.
- Unknown entries: 0.
- Unresolved entries: 0.

