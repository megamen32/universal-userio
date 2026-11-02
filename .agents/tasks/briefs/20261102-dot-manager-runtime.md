# dot / ExManager address-level question coordination: native adapter slice

Owner direction: explicitly approved implementation of existing card 3122894f-e7ac-4404-9271-f90e6c9a6f5e. Prior PR11 merged at 03fe592. This worktree is from that exact merged commit, not the dirty shared main.

## Changed files and safety boundary

- src/universal_userio/question_coordination_adapter.py: binds a question to a real UserIO saved account/conversation/source revision and original messenger_actions or source-anchored drafts. Existing sent/unknown receipt is read by original operation; no second sender or queue.
- src/universal_userio/messenger.py: existing Messenger._execute_action rejects a question-correlated plain approve without a trusted in-process fence. Ordinary non-question actions unchanged.
- src/universal_userio/service.py: existing draft/triage provider send path also refuses a correlated question without internal permit; existing human approval and transport routes unchanged.
- tests/test_question_coordination_runtime.py: actual SQLiteUserIOStore schema, original receipt, wrong caller, UNKNOWN, handoff, source rebinding, and direct-provider-entry fences; no external sends.

Nothing touches network/proxy/access credentials, PR16 board projection, UserIO regex/Forum worktrees, mirror successor runtime, the earlier denied tests/test_question_coordination.py edit, or an unapproved deployment.

## Verified receipts

- Native focused 6 PASS (2026-11-02 ~13:25 UTC).
- Broad affected regression: 63 PASS / 5 FAIL (same-day). All five reproduced unchanged on clean upstream 03fe592; exact details in the sibling bug record. No tests disabled.
- Pre-existing PytestConfigWarning about asyncio_default_fixture_loop_scope remains.
- New question module previously merged PR11 had 20 focused offline PASS. This does not prove installed feature.
- Both ExManager runtime and dot mirror still need to call the same verified server-side coordinator, not separately deduplicate.

## Critical release blockers

1. This adapter is not yet bound to a live, authenticated caller identity. Existing UserIO principals do not prove dot or ExManager service role just from user-supplied worker_id. Do not invent a role, switch tokens, or widen grants. The registered current application/source binding must be verified by the owner.
2. No installed UserIO question API/MCP tool exposes the trusted adapter; neither dot relay nor ExManager uses it in production. A source-only merge is not an installed handoff.
3. The native UserIO server needs a trusted transport closure to call Messenger.approve or Service.approve with the private _question_fence after result_saved + original operation journal reservation + same request_id. Never accept the internal permit from an HTTP/MCP argument.
4. Mirror successor owns dot-relay/dot-mirror/server, and UserIO backend is separately scoped. ExManager userio.go/telegram_chat.go have foreign WIP. Coordinate these exact owners, do not overwrite or retry existing PR16 safety-denied deploy.
5. No verified 11-case end-to-end receipt, review, build, deploy or installed readback. Do not mark Done.

Rollback: keep the currently installed UserIO backend/frontend/voice unchanged; this isolated worktree is not deployed.
