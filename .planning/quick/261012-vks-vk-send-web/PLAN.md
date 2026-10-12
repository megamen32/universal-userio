---
type: quick
status: executing
date: 2026-10-12
---
# Personal VK sending and normal web acceptance

User explicitly requests full personal VK connection, not reading alone.
Reuse the existing connected test200000000106 authorization. Implement native
messages.send with draft-derived stable random_id, account/peer/user checks,
real receipts and exact outgoing mirror. Keep existing read/poller/history,
community and identity-only accounts unchanged. User-written web text sends
immediately through the existing normal manual-draft/approve route.

Fast unit: official VK send fields/receipt/denial versus unknown; expected5s/max30.
Focused integration: tenant/peer binding, claim/no replay, receipt mirror and
normal HTTP web send; expected15s/max60. Combined ordinary <=180 including setup.
Publish/install before final canary. Actual headed web send to owned VKself,
refresh/reopen and native provider ID/body readback are required. No other
recipient, model, S21, credential replacement or old stimulus replay.
