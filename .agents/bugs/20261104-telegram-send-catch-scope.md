# Telegram send error-handler scope failure

Status: source fix verified; runtime acceptance requires an installed-image receipt.
Owner: existing ChatGPT session 6ae89a43-fa08-83eb-860f-c4acf947463e.
User explicitly requested repairing UserIO on 2026-11-04.

## Failure and narrow fix
The /send end callback declared chatId, wantAccount and attemptId inside try,
then referenced them in catch. Provider rejection, transport failure, a refused
reply or malformed JSON caused a secondary ReferenceError instead of a durable
error receipt and a completed HTTP response.
Move only these three bindings to callback scope. Do not alter provider routing,
authentication, human consent, the ledger format or conservative UNKNOWN policy.

## Verification
The regression executes the actual /send source branch in a VM with a disposable
SendAttemptLedger and provider doubles. No server import, live connector, network
request or message to a person is performed. Before the fix 5 of 12 regressions
failed with attemptId is not defined. After the fix all 12 pass, plus all 8 existing
send-attempt journal tests. Syntax validation passes. Tests cover rejection,
transport errors, known pre-send refusal, malformed JSON, missing authentication,
account isolation, successful sends/replies, preserved reply identity, UNKNOWN
no-replay and persistence across a ledger restart.

## Publication and operational limits
The shared main checkout is behind published main and contains unrelated WIP.
Publish only this three-file change atop the exact published base
 d5c1eb0fa0f8ef33c07de46ce23a2710c168852e
using an isolated Git index; do not switch/reset the shared checkout or stage its
unrelated changes. The deployment must use the native guarded backend-only
replacement over the accepted immutable image, preserving sessions, databases,
account identities, frontend and voice. Take a native project release lease and
retain rollback/health receipts.

This fix neither removes a Telegram account restriction nor reconstructs missing
outcomes of old sends. Existing pending/unknown attempts remain non-replayable.
No old attempt may be reset, removed or retried merely because this code is fixed.
