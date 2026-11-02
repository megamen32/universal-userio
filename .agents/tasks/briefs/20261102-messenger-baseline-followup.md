# LHC follow-up: Messenger baseline regressions (2026-11-02)

Intent: rectify the four RED tests on merged UserIO upstream main 0e45337 without changing production ACL, provider operation semantics, or PR15 ChatGPT-request diagnostics. Isolated branch fix/20261102-messenger-baseline-followup.

Bounded path: preserve actual deny-on-unsupported reaction, direct channel_create tool, original action-id replay on Telegram-admin lost response, and fail-closed scoped read-only behavior; repair stale test harness expectations. Add explicit negative regression, run focused RED/GREEN + wide native Messenger/Workspace tests, commit/push, independent review, merge only on guards. No external messages.

Findings: initial 4 FAIL reproduced on upstream 0e45337. Fixed synthetic reaction removal capability and negative deny fixture, create-route migration in two tests, and error-code expectation on unrelated legacy send route while asserting no draft. Focused 5 PASS. Full Messenger/Workspace 71 PASS, 1 pre-existing pytest configuration warning. Source contracts untouched. Diagnostic owner PR15 bb75fed owns adapters.py, chatgpt_request_trace.py, tests/test_chatgpt_request_trace.py; no changes to those or their two send baseline tests.

LHC scope ownership: isolated worktree in UserIO; native Herder workspace/lease reported owners [] and lease null when inspected at 16:24 UTC, but no validated current ChatGPT nativeSessionId was available to publish a durable Herder reservation. Do not misattribute another owner's session. No release lease acquired. Feature PR13 is already merged at 0e45337; live backend at last read still 03fe5925, so E2E and release are not done.
