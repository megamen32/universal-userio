---
status: passed
---

Acceptance: real physical provider login + normal deployed webform + maintainedUserIO read + new process persistedcredential read. No MCP/ownerAPI stand-in for the form submission. Readonlybrowser6B2 and freshformF9ED evidence stay separate. NativeIDs and text match, one self6 row after reload/reconciliation. Root-free onboarding uses an ordinary browser, not extracted APK credentials.

Technical checks:26 focused fast unit/integration checks, max70s/ordinary180; actual5.99s. Docsbuilder/check pass, boundedhelper0/fullcleanup. No focus group is needed for the protocol/persistence fix; normal UI evidence is scoped to the accepted flow and does not claim one-click OAuth or general UX usability.

Limits: personal VK is read-only; auto-refresh not proven; provider session expiry/revocation may require reconnect. Exact token TTL remains provider-controlled.
