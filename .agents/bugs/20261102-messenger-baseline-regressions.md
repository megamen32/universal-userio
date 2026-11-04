# Baseline Messenger regressions (real source, no hidden disables)

Wide suite 2026-11-02, source main 03fe592 plus isolated backend feature:
- tests/test_messenger.py::test_reaction_removal_uses_same_approved_path_without_whitespace: reaction_removal_not_supported. Test's Provider does not advertise removal support; safety check declines unsupported removal. No change to reaction policy.
- tests/test_messenger_telegram_reply.py::test_telegram_admin_proposals_are_exact_account_bound_and_deduplicated: use_telegram_admin_create. Test expects proposal for channel_create, current contract requires create endpoint.
- tests/test_messenger_telegram_reply.py::test_scoped_token_reads_and_downloads_only_its_exact_telegram_chat: scoped_messenger_only rather than expected chat_read_only_token, different fail-closed error classification.
- tests/test_messenger_telegram_reply.py::test_telegram_admin_recovers_a_lost_http_response_without_new_action: same channel_create proposal mismatch.
- One additional fail in wide run was our initial namespace expansion beyond messenger token; fixed by placing question tools under userio.messenger.question.*; focused existing credential manifest test and new MCP test both PASS.

Existing production policy must not be weakened just to satisfy stale tests. Recommend independent test maintenance owner update fixtures for source's documented current supported behavior, then rerun focused and broad regressions. No tests disabled or bypassed. Existing pytest config warning: Unknown config option asyncio_default_fixture_loop_scope.


## Three additional baseline Messenger/draft failures from PR15 independent review

- ID: USERIO-20261102-MESSENGER-DRAFT-BASELINE-3
- Status: OPEN / source-contract review and native owner confirmation required
- Evidence: review worktree based on published 0e45337 with PR15 changes applied, equivalent source to merged d5c1eb0; extended pytest run. The three failures were not skipped, xfailed, suppressed or patched in PR15.
- Scope owner to confirm: existing UserIO Messenger/MCP/service/claims integrator for merged PR #13 (0e45337); git author alone does not establish native ownership. No new worker or competing writer is assigned.
- File ownership boundary: src/universal_userio/mcp_surface.py, src/universal_userio/service.py, and their test contracts. Do not alter PR15 ChatGPT observability, mirror state, claims, provider send fences or live installations to settle these.
- The correct fix is NOT automatically to weaken current route/claim policy. Compare source contract vs obsolete fixtures with the owner, then make the minimum authorized test/source change.

| Existing failing test | Actual failure | Exact scope needing review |
| --- | --- | --- |
| tests/test_multi_user.py::test_users_with_same_provider_ids_are_isolated_and_approval_is_scoped | KeyError: 'draft' after UserIOMcpSurface.dispatch('userio.channels.send_draft', ...) | MCP surface/result schema vs currently supported send-draft operation and tenant ACL |
| tests/test_draft_retention.py::test_conversation_read_expires_only_old_proposed_drafts | AttributeError: UserIOService has no create_manual_draft | Service draft lifecycle and expiry test contract |
| tests/test_draft_retention.py::test_expiration_preserves_triage_draft_with_uncertain_delivery | AttributeError: UserIOService has no create_manual_draft | Service draft lifecycle and uncertain delivery preservation |

Release distinction: UserIO backend image d5c1eb0 is installed and healthy. These pre-existing contract discrepancies are not evidence for HTTP 429, and must not trigger a second UserIO rollout during root's Airlock work.

### Required independent closure

- [ ] Existing UserIO Messenger/service owner acknowledges exact file scope (do not infer from Git author).
- [ ] Contract decision preserving tenant boundaries, approval send claim and UNKNOWN semantics.
- [ ] Three exact tests rerun unmodified, with recorded result; no skips or test weakening.
- [ ] Relevant broad suite accepted before any code release by the authorized owner.
