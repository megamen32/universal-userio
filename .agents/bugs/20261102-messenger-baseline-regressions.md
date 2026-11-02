# Baseline Messenger regressions (real source, no hidden disables)

Wide suite 2026-11-02, source main 03fe592 plus isolated backend feature:
- tests/test_messenger.py::test_reaction_removal_uses_same_approved_path_without_whitespace: reaction_removal_not_supported. Test's Provider does not advertise removal support; safety check declines unsupported removal. No change to reaction policy.
- tests/test_messenger_telegram_reply.py::test_telegram_admin_proposals_are_exact_account_bound_and_deduplicated: use_telegram_admin_create. Test expects proposal for channel_create, current contract requires create endpoint.
- tests/test_messenger_telegram_reply.py::test_scoped_token_reads_and_downloads_only_its_exact_telegram_chat: scoped_messenger_only rather than expected chat_read_only_token, different fail-closed error classification.
- tests/test_messenger_telegram_reply.py::test_telegram_admin_recovers_a_lost_http_response_without_new_action: same channel_create proposal mismatch.
- One additional fail in wide run was our initial namespace expansion beyond messenger token; fixed by placing question tools under userio.messenger.question.*; focused existing credential manifest test and new MCP test both PASS.

Existing production policy must not be weakened just to satisfy stale tests. Recommend independent test maintenance owner update fixtures for source's documented current supported behavior, then rerun focused and broad regressions. No tests disabled or bypassed. Existing pytest config warning: Unknown config option asyncio_default_fixture_loop_scope.
