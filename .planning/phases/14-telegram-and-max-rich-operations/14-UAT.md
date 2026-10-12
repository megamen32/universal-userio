---
status: partial
phase: 14-telegram-and-max-rich-operations
source: installed source eb14bb6; actual new provider QA and retained native edit/file evidence
started: 2026-10-12
last_updated: 2026-10-12
---

## Current Test

number: 9
name: Recipient-visible typing/progress
expected: Observed native indicator/expiry or truthful unsupported fallback, distinct from provider ACK.
awaiting: explicitly deferred until a second recipient test account is supplied; native SDK remains Exmanager-owned

## Tests

### 1. Telegram repeated own-message edit
expected: Same provider ID survives repeated edits and final native readback.
result: pass
evidence: Exmanager native-vision outcome source18/reply19, three accepted edits; no replay.

### 2. MAX repeated own-message edit
expected: Same provider ID survives URL replacement/removal and normal reload.
result: pass
evidence: Accepted phase04/STATE normal web evidence plus new native Secretary final reply117430265913625196 changed twice and original body restored with same-ID fresh readbacks; see phase15 native-max-final verification. Later auth refusal retained separately.

### 3. Telegram reaction lifecycle
expected: Add/change/remove through exact generic approved action; independent native reaction readback.
result: pass
evidence: tg-react-add/change/remove durable receipts; independent Helper source21 readback 👍1→🔥1→none, original baseline restored.
owner: UserIO7bb provider proof; Exmanager97 retains phase15 SDK journey.

### 4. MAX reaction lifecycle
expected: Add/change/remove through the same contract and exact MAX readback, or honest unsupported result if provider/account disallows it.
result: pass
evidence: max-rich actual native178/179/180 add/change/remove on exact source117429468690188397; original empty baseline restored.
owner: UserIO7bb provider proof; Exmanager97 retains phase15 SDK journey.

### 5. Telegram native-reference sticker
expected: Approved exact source/index/attachment identity reaches the QA recipient as a sticker and agrees with receipt/readback.
result: pass
evidence: Actual generic sender881 outgoing23 has native sticker1391391008142393345 matching QA incoming22; independent recipient preview accepted.
owner: UserIO7bb generic provider acceptance; Exmanager97 phase15 SDK.

### 6. MAX native-reference sticker
expected: Approved exact source/index/attachment identity reaches the QA recipient as a sticker and agrees with receipt/readback.
result: pass
evidence: Different actual source117424822620729543→outgoing117429937932742855 native sticker4830150843. Earlier unknown attempt retained and never replayed.
owner: UserIO7bb provider/contract; Exmanager97 phase15 SDK.

### 7. Telegram file through the deployed Secretary
expected: Exported SDKStorage bytes pass generic source-bound approval; recipient's native bytes/name/MIME/SHA match and receipt persists.
result: pass
evidence: Existing verified Exmanager97 handoff: savedDoc2bf, new native source2625→file2627, independent Telegram Helper1985454;191bytes/SHA2569a797f3674a25b4a63f431d0ef364cae6c86ff8b2e4aca1ac1ef5aff2aff0ad9. Reuse this accepted SDK/recipient evidence, no send replay.
owner: Exmanager97 native SDK/relay; UserIO7bb staging contract.

### 8. MAX file through generic UserIO; native Secretary remains phase15
expected: Generic source-bound new document reaches provider with exact staged byte hash and durable receipt.
result: pass
evidence: New117429938010356944; actual native download171bytes/SHA3ed5bf98… matches staging. Native Secretary SDK export/send journey remains unaccepted in phase15; this does not close that claim.
owner: UserIO7bb provider; Exmanager97 phase15 native SDK.

### 9. Telegram typing/progress
expected: Recipient observes typing/progress during the new normal native response, or exact unsupported reason and readable fallback.
result: pending
evidence: Generic start/stop receipts accepted; recipient indicator not observed.
owner: UserIO7bb recipient observation; Exmanager97 native SDK/relay.

### 10. MAX typing/progress
expected: Same visible provider behavior or truthful unsupported fallback for the exact current account.
result: pending
evidence: Generic native start accepted; discovery explicitly says expires_automatically; recipient indicator/expiry not observed. User hook explicitly defers until second recipient test account; existing sender is sufficient for send/read/edit, no password in chat.
owner: UserIO7bb recipient observation; Exmanager97 native SDK.

## Summary

total: 10
passed: 8
issues: 0
pending: 2
skipped: 0

## Gaps

The two pending rows are **missing recipient acceptance**, not reproduced
provider failures. ACT-01–03 are accepted,3/4 (75%); scenario8/10 (80%) is a
different count. The current user's independent-project instruction separates
this provider matrix from native SDK parity: original native MAX file/sticker/
reaction journeys remain phase15 and are not silently called successful.

Earlier relay document24 caption substitution is repaired by its owner:
current live Secretary media returns actual150bytes/body/SHA9575a93d…, matching
independent recipient download. Old failure is preserved; no send repeated.
See phase15 native-read verification for actual SDK read evidence.

No historical UNKNOWN is replayed. New browsers use managed44/88 only. Each
scenario is focused integration, expected120/max600s under a finite owning
budget; source ordinary verification remains hard180s. Proofs and remaining
recipient authentication/control boundary are in14-rich-provider-VERIFICATION.
