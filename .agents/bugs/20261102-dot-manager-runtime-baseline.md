# Baseline failures reproduced on clean upstream 03fe592

Date 2026-11-02, independent clean detached worktree, no feature files changed. The same five failures occurred in the original untouched main and in the dot/manager runtime candidate. All are baseline, not effects of the question fencing code.

- tests/test_messenger.py::test_reaction_removal_uses_same_approved_path_without_whitespace: proposed reaction removal refused reaction_removal_not_supported by provider capabilities. Do not bypass safety by claiming unsupported removal worked.
- tests/test_triage_review_drafts.py::test_react_to_message_routes_telegram_conversation: synthetic react receipt omitted required removed field or source binding; error Telegram reaction receipt identity conflict.
- tests/test_triage_review_drafts.py::test_react_to_message_requires_exact_confirmation: test creates unbound Telegram conversation; Contact Messenger correctly refuses account_not_found.
- tests/test_draft_retention.py::test_conversation_read_expires_only_old_proposed_drafts and ::test_expiration_preserves_triage_draft_with_uncertain_delivery: test invokes removed/unsupported create_manual_draft symbol. Use current supported prepare_manual_message route with explicit saved source account if fixture semantics permit; never resurrect unsafe bypass.
- PytestConfigWarning: unknown asyncio_default_fixture_loop_scope (existing environment dependency, not suppressed).

Minimal owner fix: update only isolated test fixtures for supported account/capability and original receipt schema; distinguish unsupported operation refusals from expected successes, then rerun five cases and adjacent regressions. Product changes only if a real current supported contract fails. Until resolved the broad regression gate is red. Do not disable tests.
