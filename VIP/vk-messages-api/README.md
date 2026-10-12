# VIP / VK messages — future permission-gated experiment

**Status: archived, not connected to production.**

In October 2026 we tested a read-only VK API integration in UserIO
(commit ae7dc0a). It only succeeds if a *user* VK token has the personal
messages permission, which the current VK ID app does **not** grant.
We removed this unreachable flow from the active account connector. This
folder is for a future day when VK grants message access.

Contents:
- prototype_vk_native.py — read-only VK users / conversations / history transport.
- prototype-account-integration.md — preserved user-scoped enrollment,
  message projection and polling logic.
- prototype_vk_native_tests.py.txt — original simulated tests (not live auth).

Do not import these files in production or use bot/community tokens to
claim access to a user's personal messages. Community bot inboxes
have different authorization and ownership semantics. If VK grants
personal-message permission, first verify it with a user-authorized
token and real read-only calls, then review tenant isolation, full
pagination, TTL, 2FA/session boundaries, anti-rate-limit behavior,
provider read state, and security before re-enabling.

No credentials, cookies, session exports, or real messages are archived.
