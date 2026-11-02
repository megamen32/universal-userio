# LHC bug: workspace triage missing account binding in baseline

Date: 2026-11-02; UserIO source 17979cd. Existing test command PYTHONPATH=src python3 -m pytest -q tests/test_workspace_claims.py produced 31 PASS and 1 FAIL.
Test: test_triage_send_rechecks_policy_is_idempotent_and_rejects_conflicting_choice, tests/test_workspace_claims.py:855.
Exception: universal_userio.contact_start.ContactStartError: contact_account_required.
Trace: service.send_workspace_triage -> _prepare_claimed_draft -> contact_start.account. Existing fixture creates Matrix source with no saved/send-authorized account_ref; cannot invent account or bypass policy.
Baseline: candidate new module is not imported by this test. Do not change existing backend/voice/frontend outside release-owner scope.
Suggested owner fix: provision an isolated authorized Matrix test account and saved binding, rerun the actual send test and regressions without external sends. Not fixed; broader acceptance remains FAIL.
Additional baseline warning: pytest Unknown config option asyncio_default_fixture_loop_scope, pyproject.toml line 35. Do not disable tests to hide it.
