---
phase: 12-effective-channel-capabilities
plan: 01
status: complete
subsystem: api
tags: [userio, capabilities, mcp, airlock]
requires:
  - phase: 11-final-acceptance
    provides: Accepted everyday UserIO connections and credentials
provides:
  - Installed account/provider/credential capability intersection
  - Preserved Secretary sender and separate scoped native SDK resource
affects: [13-source-bound-history-and-approved-replies, 14-telegram-and-max-rich-operations]
tech-stack:
  added: []
  patterns: [shared HTTP/MCP dispatcher, effective per-account capabilities]
key-files:
  created: [src/universal_userio/messenger.py]
  modified: [src/universal_userio/mcp_dispatch.py, src/universal_userio/token_permissions.py]
key-decisions:
  - Effective discovery is complete; final per-operation/native MAX acceptance belongs to phases14–15.
requirements-completed: [CAP-01, CAP-02, CAP-03]
duration: historical implementation; evidence reconciliation only
completed: 2026-10-12
---

# Exact-account capabilities and preserved native Secretary sender

**Installed HTTP/MCP capability discovery uses the real account and credential permissions; the deployed Secretary retains its Telegram881 sender and separate restricted resource.**

## Accepted requirements

| Requirement | Evidence and claim boundary |
|---|---|
| CAP-01 | Retained `.tmp/messenger-v13-20261012/invocation-live-ready.json`: actual HTTP200, verified installed manifest, Telegram881/MAX effective operations and initialized bounded event heads. Advertising an operation does not prove that operation's whole native journey. |
| CAP-02 | Same shared HTTP/MCP catalog and focused parity checks; `.tmp/messenger-v13-20261012/live-contract-receipt.json` records management/foreign-account/legacy-path refusals and own-child revoke401. Existing native readonly accounts/TG/MAX reads are in Exmanager `20261012-userio-readonly-native-read.json`; restricted access was not replaced by sender authority. |
| CAP-03 | Actual deployed native `userio_messenger` resource selects the generic source/capability route and preserves account881. Exmanager `20261012-userio-native-vision-live.json` proves account881 source18→SDK→reply19 plus edits. Telegram540 remains a reader/real incoming actor, never an implicit replacement sender. MAX native completion remains phase15. |

Evidence root for Exmanager outcomes:
`/home/roomhacker/agents-projects/exmanager/.agents/tasks/outcomes/`.
Private bodies, credentials and provider access keys are not copied into planning.

## Publication and validation

Original source/install evidence is retained in the invocation manifest/verify
and live-ready files, source2544edc and later source refinements. Focused common
contract checks and real installed/native discovery were already run; this
summary reconciles those results, it does not execute another provider action.
The earlier `contract.json` with `live_verified:false` is an initial checkpoint,
superseded by the named later receipts, not edited into a pass.

## Next phase readiness

Generic provenance/approval is accepted separately in phase13. The full live
rich-action matrix and actual MAX Secretary/transport retirement remain open.
