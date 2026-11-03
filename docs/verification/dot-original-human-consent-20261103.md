# Human-consented original question reply — backend source candidate

**Date:** 2026-11-03 (UTC). **Base:** installed/published UserIO `d5c1eb0` with merged question coordinator PR13. **Stage:** isolated source-only draft, NOT installed, no production sender/credential changes.

## Purpose

Bridge the exact missing approval contract found in Mirror PR19 while retaining **ONE** UserIO `Messenger.approve` / `QuestionRuntime.begin_original_send` outgoing fence and the original `messenger_actions` receipt. Dot's service credential and `confirm=true` are **not** user approval.

## Existing permissions, no new service

The existing UserIO bearer authenticates the bound dot service principal. The existing logged-in UserIO owner web session (cookie `userio_web_session`) is the only actor allowed to record human consent for the exact action. No new bearer, impersonated owner, second delivery journal, worker or provider sender is created. The new SQLite `question_action_consents` table is **audit metadata** bound to an existing action+question epoch, not a send queue.

### Minimal flow

1. Existing `POST /v1/questions/operation` acquires a verified UserIO `event_seq` and current source `revision` for the exact dedicated dot binding. A lease holder has `epoch,lease_token`; no model-provided role.
2. **Propose only**, no send: `POST /v1/questions/propose-original-reply`, existing dot service bearer, JSON with *exactly* `event_seq,revision,epoch,lease_token,request_id,body`. Checks active dot lease and saved account/source binding, calls existing `Messenger.propose(reply, original source_message_id, original request_id)` and `result_saved(messenger:<action_id>)`. Returns `status=awaiting_human_approval`, `action_id`, `expected_hash`, and relative `approval_path` only. Repeated request ID cannot change body. **No provider I/O**.
3. User opens `GET /v1/questions/actions/<action_id>/review` in their existing authenticated UserIO browser session. HTML shows **original question, exact outgoing text, account, target, epoch**. Session-specific HMAC CSRF, strict same-origin POST, CSP and no-cache protect the click.
4. User presses **Подтверждаю именно этот ответ**. `POST /v1/questions/actions/<action_id>/approve` accepts only the exact form from that live interactive owner web session and records `approved_by,approved_at,approval_ref` with original action/hash, body hash, source id, question and epoch. This step **does not send**. A service token, bearer/API-only request, model-generated `confirm=true`, expired/foreign session, CSRF mismatch, revoked account, changed text/source or changed epoch cannot produce consent.
5. Dot calls `POST /v1/questions/approved-original-send` with the **existing dot service bearer** and `action_id,expected_hash,event_seq,revision,epoch,lease_token` (no separate sender). Backend reads the saved consent at the **existing PR13 provider-entry fence**, rechecks immutable source/grants/lease, marks the original action as UNKNOWN *before* provider I/O, then calls the existing `Messenger.approve` once. Without human consent it fails closed **before** the provider.
6. If the original provider result is uncertain, `POST /v1/questions/approved-original-send` returns `HTTP 202` with `{"status":"unknown","retryable":false,"action_id":"<original-id>"}`. Do NOT call the send endpoint again while UNKNOWN: query the unchanged original action/receipt, and use a distinct receipt-read request ID for each later reconciliation. An accepted repeat is a *read-only* projection, not another provider call.
7. Only the original saved `messenger_actions` receipt is projected to the Mirror's verified shape: `status=accepted`, `question_delivery_state=sent`, exact `account_ref,peer_id,source_provider_message_id,source_message_id,conversation_id,event_seq,revision,epoch,request_id,action_id,receipt_ref=messenger:<action_id>,provider_message_id,message_sha256`. Repeated accepted requests return the original result without provider replay. Missing/mismatched consent, changed source, missing/forged provider ID or UNKNOWN => refused, never fabricated success. `userio.messenger.question.reconcile` may read the original journal later; it does not send.
8. Hold media, multipart and edit for claimed questions until the original-action-specific policy is independently reviewed. The existing `Messenger` now rejects an additional non-reply action aimed at the **same** already claimed source.

## Security and integration gates

- **Only one provider fence**: merged `QuestionRuntime.begin_original_send` remains the sole provider-entry send CAS. This candidate adds a *prerequisite* (audited human action consent), not an alternate `_question_fence` from PR12.
- Full server and DOM path requires action ID and current source/owner grant; no long-lived lease_token stored in a browser consent record or mirror journal.
- `QuestionRuntime` direct unit fixtures without the production HTTP handler remain backward-compatible for existing offline source tests. Production handler installs the guarded consent component; independent review must verify there are no alternative production entrypoints constructing a runtime without it.
- **Mirror successor (PR19)** must adapt `questionApproval` to this exact staged approval URL and later authorized dispatch, holding the correlated reply until human action + original receipt. The current Mirror callback is explicitly unwired; this PR does NOT change its JS files.
- **ExManager owner (PR17)** must keep its own exact native human approval and owner-principal source gate; current Go adapter only consumes claims/receipts. Its existing `userio_approve_send` is a **draft** gate, NOT equivalent to this consent record.
- **PR16** 71/71 fixture-only baseline fix remains under independent human review. PR12 conflicting second fence remains unmerged. Logging owner PR15 files and working voice/UI are untouched.
- During root Airlock critical window: **no release lease, no merge/deploy/restart, no provider sends, no new credentials or rights**. Offline tests use fake provider and loopback HTTP only.

## Before production acceptance

Independent backend security review and dedicated source regression, update Mirror PR19 and ExManager PR17 adapters by their actual owners, review normal build/deploy gates, then verify the **11 actual user acceptance cases** and original provider receipts. No offline/synthetic result alone is runtime acceptance.

## Additional provider-entry and cross-consumer review

- The standalone UserIO stdio MCP entrypoint in `src/universal_userio/__main__.py` constructs `UserIOMcpSurface` without an HTTP-installed `QuestionRuntime`. The same existing `Messenger` now **fails closed** for a persisted claimed original source when the runtime is absent; a process cannot escape the PR13 fence by selecting stdio instead of HTTP. This does not grant a new transport or route.
- **Exact PR19 validator interoperability (offline):** from fake UserIO provider consent → backend flat original receipt JSON → loaded the `dot-question-consumer.mjs` `verifiedOriginalMessengerReceipt` from Mirror commit `fb4509f` into an isolated `/tmp` copy. The validator accepted the exact original action/receipt and rejected a mutated `question_delivery_state=unknown`. Receipt: `EXACT_PR19_VALIDATOR_PASS`; this is a source-only cross-language smoke check, NOT runtime arrival or production sending.
- The staged approval callback is **not installed in Mirror PR19**. The human must open the returned `approval_path` in a logged-in UserIO browser session; the callback cannot click it or impersonate that session. The callback can return `HTTP 202 unknown` with `retryable=false` when the original provider result cannot be proven, and must never be replayed without read-only original action reconciliation.

Remaining security review: verify every possible in-process `Messenger` constructor, source edits during approval, web Host/Origin forwarding, production cookie path/domain, identical original provider receipt across connector implementations, and human UI discovery/notification with existing NoticePlace policy. These are review gates, not claims of acceptance.

## Original action uniqueness follow-up

Source-only regression: a live dot claim with an already persisted `result_ref=messenger:<action_id>` cannot stage a second proposed action under a different request ID. Identical original request+exact body may retrieve the same existing proposed action without provider effects; mismatched text fails closed. A fake-provider dot→manager handoff after result_saved also reuses the SAME original action, requires a fresh owner human approval for the resumed epoch, and completes only on the original receipt. Both tests PASS, no live sends. These checks do not replace a future concurrency/restart integration review.


## PR16 + PR20 isolated source-matrix receipt (2026-11-03)

- A **temporary detached worktree** at `/home/roomhacker/agents-projects/universal-userio/.worktrees/dot-consent-pr16-source-matrix-20261103` was created from PR20 HEAD `7511b9d` (with PR20's original consent and idempotency changes). Applied PR16 `1d70cb8` with `git cherry-pick --no-commit` **ONLY in that throwaway detached worktree** for compatibility testing. Neither PR branch, shared main, installed backend, nor source-owner files were overwritten or merged.
- Composite no-send command: `PYTHONPATH=src python3 -m pytest -q tests/test_messenger.py tests/test_messenger_telegram_reply.py tests/test_messenger_relay.py tests/test_messenger_formatting_contract.py tests/test_workspace_claims.py tests/test_question_action_human_consent.py tests/test_question_runtime_adapter.py tests/test_question_coordination_native_recovery.py tests/test_question_coordination.py` → **115 passed, 1 pre-existing pytest warning, duration 90.07s**, no external provider calls.
- The 4 baseline failures on unmodified current `main d5c1eb0` are covered only by PR16; human review of that PR remains outstanding. Matrix test is NOT independent approval, merge, installation, or 11 genuine E2E acceptance receipts.

## Concurrent approved-action request regression

Two simultaneous fake-provider requests using the exact same approved action and lease result in **exactly one** provider execute and **one** saved original `messenger_actions` row. The losing request either reads the existing accepted receipt or refuses. This test is source-only and does not authorize or claim a real provider send. The additional concurrent test, dot→manager handoff and different-request-id preflight all passed (3/3).
