---
type: quick
status: in-progress
---

# Per-chat secretary control

Outcome: owner selects actual work chats; channel discussions stop automatic
analysis. Every chat has a compact visible checkbox and chat kind/status.

1. Use supported policy MCP to remove the previous folder-derived group/channel
   allow rules outside the newly named work list. Preserve personal/mail rules,
   messages, provider read flags and already delivered results.
2. Expose existing durable owner-scoped policy in the web HTTP surface and sidebar.
   Checkbox changes only one chat, persists across reload and affects future
   arrivals. Read-only credentials can inspect, never mutate.
3. Focused HTTP integration (expected 3s/max20s) verifies cross-user refusal,
   exact boolean input, no old event replay, off→on→off and metadata. Bounded
   build expected10s/max120s; publish before normal release installation.
4. Real web acceptance: normal owner chat toggle, sidebar state and reload;
   restore exact policy baseline, close only owned browser target. No sends/models.

Owner: UserIO 01a11747-7bb9-7bc0-8c0a-ce64db49105d. Exmanager audit remains
independent; its current Telegram/business transport is untouched.
