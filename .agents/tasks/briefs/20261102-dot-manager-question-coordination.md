# dot / ExManager addressed question coordination

Owner request: implement the existing Todo card 3122894f-e7ac-4404-9271-f90e6c9a6f5e, version 5 at claim, using UserIO's durable inbound event/claim and existing outbound operation receipt, without a new service/queue/scheduler/policy engine.

Owner and scope: current ChatGPT conversation 6ae86138-5840-83ed-8c3f-1b3a03e3dbe7. Herder scoped reservations on UserIO for question_coordination.py, its isolated tests, this brief. Base 17979cd, branch feat/20261102-dot-manager-question-coordination. Other branch scopes are not ours.

LHC fullcycle: 1) verify installed claim/send paths and owner overlap; 2) implement and test independent transaction-backed question state machine, true caller and source identity, question-level CAS/epoch, stop-on-UNKNOWN, append-only audit, journal integration seam; 3) coordinate integration into existing UserIO backend and ExManager/dot consumers with exact native owners; 4) review, guarded merge/build/deploy and live readback of both; 5) record all 11 cases, regressions, release receipts, change card via version CAS. Stage 1-2 can proceed independently; stage 3 must not touch other owners' backend/frontend/voice, forum topics, regex PR9, PR16 projection/migration69, or reproduce denied deployment.

No real test messages; use deterministic fixtures, existing harness. Verified independent source is NOT an installed feature. Real delivery and complete require a readback of the original request's journal receipt. Human approvals cannot be inferred from dot text. Default deny bots. Never log full lease tokens or private transcript.

Critical blockers at start: Herder does not identify the current ChatGPT session via identity; native session_info for UserIO regex cleanup owner chat ID on server-100 returned not found. The UserIO backend/send change needs explicit coordination; no production routing or deploy without it. ExManager native PR16 go air deploy safety denial remains known-unexecuted, not a route for this change.

## 2026-11-02 checkpoints

Card 3122894f-e7ac-4404-9271-f90e6c9a6f5e readback v5/review. Independent branch feat/20261102-dot-manager-question-coordination from 17979cd.
Herder 4 non-overlapping workspace reservations; current ChatGPT native identity unresolved (caller_session_unavailable), other ChatGPT release-owner ID not found. No cross-owner SendMessage sent.
Existing UserIO workspace_claims TTL reclaims without outgoing receipt; send_workspace_triage maintains its own sending/confirm path but no shared dot/ExManager fence.
New isolated candidate QuestionCoordination: authorized actor and saved binding injected by FUTURE server adapter, exact question identity/revision, epoch/lease CAS, stable request IDs, append-only audit and UNKNOWN no-replay. send_fenced in offline tests uses fake outgoing journal, real send untouched.
Candidate focused tests 16 PASS. Existing workspace_claims: 31 PASS and 1 FAIL contact_account_required; see .agents/bugs/20261102-workspace-triage-missing-account-ref.md. One pytest config warning.
NOT DONE: actual UserIO journal adapter, both processors, real send fencing, delivery/human approval/bot deny live tests, reviewed merge/build/deploy/readback and 11 real acceptance cases. No real external sends. PR16 safety-denied deployment not retried.
