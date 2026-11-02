# 2026-11-02 — Dot/ExManager shared-question backend integration

- Original user card: 3122894f-e7ac-4404-9271-f90e6c9a6f5e.
- Direct user approval for exact backend/consumer scope on 02.11.2026.
- Base: real upstream UserIO main 03fe592 (merge PR11); isolated worktree branch feat/20261102-dot-manager-backend-integration.
- No edits to dirty shared main or Mirror successor relay, UserIO regex PR9 cleanup, ExManager board-origin PR16.
- Objective: per-question epoch/claim/CAS, no replay on UNKNOWN, original Messenger receipt, real send fence; no second transport/store/queue/service.
- Implementation: question_runtime.py authorizes current server principal, exact existing dot binding and stored conversation source, persisted messenger_actions result and original request ID. HTTP /v1/questions/operation service token only, no provider sends. MCP tools share existing messenger authorization, read-only excluded; approved Messenger replies check question lease and atomically persist UNKNOWN before provider IO. Legacy claimed draft send cannot bypass exact question, neighbor unaffected. TTL may only adopt settled receipt for completion.
- Tests: 29 new focused PASS. 35 selected legacy Messenger and workspace claim regressions PASS. Wide Messenger + workspace test had 5 failed/65 passed before new namespace change; one regression introduced by new namespace was corrected and exact focused test passed; four pre-existing failures remain listed in .agents/bugs/20261102-messenger-baseline-regressions.md.
- Known limitations: dot-relay and ExManager actual consumers NOT integrated; real original authorization source vs static service token must be independently reviewed; actual external delivery and 11 real acceptance cases NOT proven. No real test messages.
- Stage: independent source only. Do not call it installed feature; do not merge/deploy until both consumer owners, permissions, build guards and E2E are verified. Existing PR16 denied deploy remains untouched.
