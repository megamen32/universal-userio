# Telegram mirror repair — original UserIO continuation

Owner:01a11747-7bb9-7bc0-8c0a-ce64db49105d. Direct priority: account
telegram:540308572 / peer381703703, native Telegram differs from UserIO.

## Confirmed trigger

Existing ingress skipped every outgoing message and omitted provider date.
Native incoming1983326 (08:28:02UTC) and owner response1983328 (08:37:41UTC)
were independently read from the account actually authenticated as Careviolan.
The response was missing in UserIO. The DB row1983325 was absent from a short
history, which alone is not deletion proof. No messages were sent.

## Delivered source, runtime acceptance pending

Ingress mirrors both directions and native date, edits and account-specific
read watermarks. Raw updates and durable watermarks preserve reads through late
history races. Outgoing/edited/reconciliation messages cannot trigger inbound
listeners, agent delivery or automatic replies. Existing polling cadence and
auth sessions remain unchanged. One existing-client bounded reconciliation
repairs the selected chat; explicit raw MessageEmpty is required to tombstone
a missing ID. Durable messages/events/drafts/receipts are retained.

Legacy IDs hydrate in place. Current Secretary selection and context use native
chronology. Cache invalidation and a contiguous arrival frontier prevent missing
late history or persisting stale metadata/deletions. Owner followups enter the
AI prompt as existing replies; they never fabricate provider-read status.

UI distinguishes local viewing from Telegram read state, displays outgoing
replies/time and explains «Оценить важность» versus «Подробный анализ».

## Verification

Independent read-only reviewer found and rechecked fixes for cache gaps, stale
read rollback, exact deletion evidence, deleted-message lookup and account
metadata authority. No unresolved source blocker in final delta.

30 selected Python cases passed: mirror9 + existing context16 + nativeSDK5.
Actual wall20.132s / RSS134784KiB / swap0 under256/384MiB, CPU1, tasks128,
wall120, project-local temp and32MiB file cap. The five SDK cases were rerun
only because their AI prompt dependency changed. Node metadata2 cases passed
in181ms under64/128MiB, CPU1, tasks32, wall30. Node12 syntax and diff checks pass.

App tsc-b and bounded Vite build passed:62.742s / RSS297324KiB / swap0.
Two Rolldown workers and CPU1 were retained. Measured reclaim at soft256MiB
caused excess reads; next equivalent build should use soft384MiB with unchanged
hard512MiB, CPU1/tasks64/wall120, subject to fresh capacity. No rerun needed.

Original7 last PASS on10f9 is historical, not coverage of this new mirror slice.
Its maintained packet owner must bind the new exact import bytes before a new
launch. SDK/install/dispatcher prior results are retained. Core rollback/install
uses scripts/runtime_release.py; Node connector has a separately preserved
managed file snapshot because the core manifest does not manage this connector.

## Remaining real acceptance

Publish/install, one bounded native reconciliation, authenticated API and Mini
UI readback with no sends. Then original same-card6202 accepted→session-link→
result, own dispatcher restoration, and included Mac source/SDK synchronization.
Do not recreate cards or replay old provider actions.
