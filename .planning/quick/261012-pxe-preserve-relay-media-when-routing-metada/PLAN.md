---
status: executing
date: 2026-10-12
---

# Preserve actual relay media during UserIO reconciliation

Fix the proven slot collision in RelayMessengerProvider.import_projection. Routing metadata must follow native attachment indexes. Preserve exact provider source, author and invocation, without new credentials or provider/model writes.

1. Focused integration regression: first image plus second attachment survives resolve twice and stored history. Expected 10s, max90s; ordinary aggregate max180s.
2. Scoped source review, publish main, install exact runtime once.
3. Actual existing QA Telegram881/peer-1009000002001/message10: generic resolve retains native attachment5361575419839556313; generic media returns original90835bytes/SHA; stored history preserves it. No new intent, send, mark-read or model inference.

VK continuation remains active independently; no Exmanager code or service changes.
