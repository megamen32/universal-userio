---
type: quick
status: complete
date: 2026-10-12
---

# Personal VK connection

User authorized the rooted S21's dedicated VK Messenger test account and a
usable personal VK connection for normal UserIO users. Infrastructure gateway
work and Exmanager implementation remain separate.

1. Verify the current phone account and consented session without printing
   credentials. Validate real VK identity, nonempty dialogs/history and a new
   server process reading the same credential. Preserve phone/session state.
2. Repair the normal authenticated personal VK form to accept the actual VK
   authorization result. Keep exact provider identity checks and fail closed
   on denied message permissions; no permanent browser or per-user worker.
3. Publish owned source, install once, connect through the supported personal
   account endpoint, and verify maintained poller import/cursor/dedup.

Fast unit: authorization parsing, identity mismatch and provider denial;
expected 5 seconds, maximum 30. Focused integration: account enrollment and
poller ownership/cursor/reconnect, expected 15 seconds, maximum 60. Combined
ordinary validation including setup maximum 180 seconds. Live proof is
separate and must distinguish connection, stored reads and ongoing updates.

Previous Telegram/MAX/SMS proof is retained. VK is not claimed connected until
real provider and UserIO consumer evidence exists. Temporary instrumentation
and the S21 lease are cleaned up in this task.
