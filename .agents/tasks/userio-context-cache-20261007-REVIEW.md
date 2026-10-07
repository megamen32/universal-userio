---
phase: userio-context-cache-20261007
reviewed: 2026-10-07T16:00:13Z
depth: targeted-final
files_reviewed: 8
files_reviewed_list:
  - src/universal_userio/ai.py
  - src/universal_userio/adapters.py
  - src/universal_userio/runtime.py
  - src/universal_userio/service.py
  - src/universal_userio/store.py
  - web/universal-userio-web/src/components/context-settings-dialog.tsx
  - README.md
  - docs/resource-budget.md
findings:
  critical: 0
  warning: 0
  info: 0
  total: 0
status: CLEAN
---

# Phase userio-context-cache-20261007: Final Code Review

**Status:** CLEAN for the reviewed local implementation and documented boundaries.

The final review reproduced the Cyrillic adaptive-context case through the actual `SQLiteUserIOStore`, checked the accumulated provider-bound history using the store estimator, reran the affected tests, and verified the image-support documentation. Only this review artifact was changed by the reviewer; source and deployment were not changed.

## Real-store budget reproduction

Seeded 81 messages through `UserIOService.receive` into an actual in-memory SQLite store. Each sender contained 320 Cyrillic characters and each body contained 1,600 Cyrillic characters. Initial context used the configured three-message window and up to 1,000 tokens; the provider stub requested successive older pages before submitting triage. Every initial store result and returned store page was measured as compact, non-ASCII JSON with `store._estimated_tokens`. Every captured provider request was checked against the accumulated safe history from the initial window plus all preceding tool results, using that same estimator.

| Configured adaptive ceiling | Initial store estimate | Largest accumulated sent-history estimate | Final history entries |
| --- | --- | --- | --- |
| 64 | 1 | 1 | 0 |
| 128 | 1 | 1 | 0 |
| 512 | 512 | 483 | 1 |
| 1,024 | 999 | 941 | 2 |
| 2,048 | 999 | 1,702 | 4 |
| 4,096 | 999 | 3,984 | 10 |
| 6,400 | 999 | 6,151 | 17 |
| 12,000 | 999 | 11,661 | 32 |

All assertions passed: no initial window, individual store page, or accumulated sent history exceeded its configured ceiling. Very small budgets correctly return an empty history when sender and JSON metadata cannot fit. The adapter now charges compact safe-entry JSON, including long sender fields, mixed ASCII/Cyrillic content and separators. These are deterministic context estimates, not measurements of a provider tokenizer or a guarantee about the complete prompt, latest message, cached summary, tools, or vision-token accounting.

## Previous findings

- CR-01/02/03: exact summary paging, mutation invalidation and scoped anchors remain covered by passing context tests.
- CR-04: legacy sequence attachment metadata validates MIME and cannot forward a URL/path/token sentinel; the outbound regression passes.
- CR-05: bounded image downloads stop at the configured byte bound plus one overflow byte. Error-body reads are bounded. The service shares four hydration attempts across the initial triage and older pages, prioritizes the current image, and the adapter shares four accepted images / 4 MiB across the complete model operation. The targeted regressions pass.
- CR-06: the final supported default pixel loaders are account-scoped Telegram and WhatsApp. Unsupported channels, including VK, remove image declarations when bounded pixels are unavailable and remain on the text path. Historical attachment identity and size remain internal to store/service handling. README and the settings UI now explicitly describe this support boundary; this review does not claim VK pixel hydration.
- WR-01: settings and AI enforce the same minimum adaptive token ceiling of 64; API/UI boundary checks pass.
- WR-02: retention runs at startup, cache access, policy changes and the runtime's hourly `HTTPServer.service_actions` sweep. The runtime maintenance tests pass.
- WR-03: confirmed snapshot and ready/loading/error guards remain covered by all ten settings tests; TypeScript typecheck passes.

## Executed checks

- `python3 -m pytest tests/test_ai_fast_agent.py tests/test_ai_media_boundary.py tests/test_context_settings.py tests/test_ai.py tests/test_http_api.py -q`: **68 passed** in 15.91 s.
- `python3 -m pytest tests/test_runtime.py -q`: **11 passed** in 0.28 s.
- From `web/universal-userio-web`, `node --experimental-strip-types --test tests/context-settings.test.mjs`: **10 passed**.
- From that same directory, `npx tsc --noEmit`: **passed**.
- Independent real-store Cyrillic budget reproduction: **all eight ceilings passed**, including multiple older-page callbacks.
- README/UI image-support wording and resource-budget limits matched the inspected runtime paths.

## Acceptance boundary

CLEAN is a local code-review result, not production completion. No external provider call, deployment or live business canary was performed. The checkout contains shared uncommitted work; authoritative main synchronization, scoped delivery and the real consumer acceptance gate remain the owning agent's work.

_Reviewer: final targeted code review; 2026-10-07._
