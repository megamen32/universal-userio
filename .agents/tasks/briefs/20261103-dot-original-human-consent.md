# LHC task — dot original human-confirmed Messenger action

Base: current UserIO main d5c1eb0 in independent clean worktree /home/roomhacker/agents-projects/universal-userio/.worktrees/dot-action-consent-20261103. Scoped objective: fill the exact missing user-action approval bridge identified in Mirror PR19, using only the original Messenger action, PR13 UNKNOWN provider fence and existing owner web session/dot service bearer.

Code scope (own isolated branch only): new question_action_consent.py; minimum additions to question_runtime.py, messenger.py and http_api.py; new test_question_action_human_consent.py; source runbook. No edits to Mirror PR19 files, ExManager PR17 Go files, logging PR15 files, shared dirty main or voice. No second provider fence, new token, direct provider sender, runtime rollout or real provider sends.

Current acceptance: source-only fake-provider tests verify direct confirm without human record fails, original action approval idempotent, stable receipt projection, unknown and no replay, invalid epoch, revoked account and edited source, optional duplicate action bypass, authenticated owner-cookie CSRF form and service-to-original dispatch, original approval record before send. Existing QuestionRuntime tests remain green (29/29). Human consent is separate from boolean confirm, and both are enforced by backend at existing provider entry in production HTTP handler.

Human review required (including explicit audit of all production constructors and origin/CSRF behavior). Original Todo card stays REVIEW until both consumers wired, review completed, guarded deploy and 11 true acceptance receipts. Prior PR16 independent reviewer still absent. No Codex/Work or background timer.

Additional 2026-11-03 source receipt: fake-provider HTTP 202 UNKNOWN/nonretryable + follow-up 409/zero replay test passed (12 new tests PASS). The existing guarded review accepts only current owner web session, CSRF and the exact original action; Mirror PR19 and ExManager PR17 will need owner-specific wiring, not completed by this branch.
