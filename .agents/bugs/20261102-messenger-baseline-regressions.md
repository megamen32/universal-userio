# Baseline Messenger regressions (real source, no hidden disables)

Wide suite 2026-11-02, source main 03fe592 plus isolated backend feature:
- tests/test_messenger.py::test_reaction_removal_uses_same_approved_path_without_whitespace: reaction_removal_not_supported. Test's Provider does not advertise removal support; safety check declines unsupported removal. No change to reaction policy.
- tests/test_messenger_telegram_reply.py::test_telegram_admin_proposals_are_exact_account_bound_and_deduplicated: use_telegram_admin_create. Test expects proposal for channel_create, current contract requires create endpoint.
- tests/test_messenger_telegram_reply.py::test_scoped_token_reads_and_downloads_only_its_exact_telegram_chat: scoped_messenger_only rather than expected chat_read_only_token, different fail-closed error classification.
- tests/test_messenger_telegram_reply.py::test_telegram_admin_recovers_a_lost_http_response_without_new_action: same channel_create proposal mismatch.
- One additional fail in wide run was our initial namespace expansion beyond messenger token; fixed by placing question tools under userio.messenger.question.*; focused existing credential manifest test and new MCP test both PASS.

Existing production policy must not be weakened just to satisfy stale tests. Recommend independent test maintenance owner update fixtures for source's documented current supported behavior, then rerun focused and broad regressions. No tests disabled or bypassed. Existing pytest config warning: Unknown config option asyncio_default_fixture_loop_scope.


## 2026-11-02 16:30 UTC: repaired on upstream 0e45337 (isolated fixture-only follow-up)

This incident was reproduced against the published merged UserIO `main`, independent of PR15 and without altering the product's authorization or provider behavior.

- **RED** `PYTHONPATH=src python3 -m pytest -q tests/test_messenger.py::test_reaction_removal_uses_same_approved_path_without_whitespace tests/test_messenger_telegram_reply.py::test_telegram_admin_proposals_are_exact_account_bound_and_deduplicated tests/test_messenger_telegram_reply.py::test_scoped_token_reads_and_downloads_only_its_exact_telegram_chat tests/test_messenger_telegram_reply.py::test_telegram_admin_recovers_a_lost_http_response_without_new_action`: 4 failed.
- **GREEN** updated four existing scenarios plus one additional deny-path test: **5 passed**.
- **Regression** `PYTHONPATH=src python3 -m pytest -q tests/test_messenger.py tests/test_messenger_telegram_reply.py tests/test_messenger_relay.py tests/test_messenger_formatting_contract.py tests/test_workspace_claims.py`: **71 passed, 1 existing PytestConfigWarning**, duration 57.13 seconds, no real outgoing actions.

Exact repairs, no disabled tests:
1. Reaction-removal positive fixture explicitly advertises `reaction_remove_supported=True`. Added rejection test for a provider without that capability; verifies no Messenger action or send is created. Production default deny remains unchanged.
2. Channel creation test uses the current dedicated `userio.messenger.telegram_admin.create` API rather than the prohibited `telegram_admin.propose` path. A separate assertion verifies the old path fails before an effect. Existing immutable action, idempotent repeat, account scope and Telegram-admin permission checks remain covered.
3. Scoped read-only credential's legacy `userio.channels.send_draft` call must fail closed as `scoped_messenger_only` rather than claiming that specific legacy route knows the chat's permission. Assert no outgoing draft is created; effective read/download allowed only for bound account.
4. Lost response recovery uses the original persisted `messenger_actions` request ID via the new `telegram_admin.create` entrypoint, asserts `sending` before reconciliation, a single durable action and stable provider action key on repeated calls, with no new request ID.

No changes in `src/universal_userio/`, `tests/test_chatgpt_request_trace.py`, `src/universal_userio/adapters.py`, `src/universal_userio/chatgpt_request_trace.py` or the diagnostic owner's other test scope. The pre-existing pytest warning `asyncio_default_fixture_loop_scope` remains an environment/config compatibility issue to be handled under its own dependency-maintainer scope; do not silence/disable checks to make it disappear.

This fixes **the four baseline regression failures** only. It is NOT proof that both dot and ExManager consumers use the shared question lease, nor of installed version or real delivery; separate acceptance and guarded rollout remain open.
