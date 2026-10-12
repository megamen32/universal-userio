---
status: resolved
trigger: live Secretary MAX history cursor stops on page2 despite a known older source
created: 2026-10-13
updated: 2026-10-13
---

## Current Focus

hypothesis: Native inclusive `from` consumes the boundary slot; limit+1 loses continuation lookahead after filtering it.
next_action: None for this defect; retain live RED and prior refusal. Continue only missing recipient progress and Exmanager-owned native acceptance.

## Symptoms

expected: Distinct timestamp pages keep a usable next cursor while older messages exist, without duplicates, read ACKs or dispatching history.
actual: Native Secretary MAX limit2 page1 read two actual messages with a cursor; page2 read two and returned cursorEMPTY although source117424822620729543 exists at native1791760599.071, older than both page2 entries.
errors: No provider error; successful page response silently ends navigation.

## Evidence

- timestamp: 2026-10-12T21:29:29.180449+00:00 (proof-write time)
  checked: native Secretary history, both channels
  found: TG two pages had cursors and preserved authors; MAX second cursor absent.
  implication: Defect is UserIO MAX pagination, not Secretary credentials or actor lookup.
- timestamp: 2026-10-12T21:32:28.433653+00:00 (proof-write time)
  checked: exact older source and normal MAX history
  found: confirmed older native timestamp below terminal page; private `.tmp/capabilities-rich-20261012/max-history-stall-red.json`.
  implication: A live RED exists; no send/model/action replay occurred.
- timestamp: source inspection within this debug cycle
  checked: existing source and test transport
  found: provider requests limit+1 then filters native boundary; test fixture assumed strict `<from`.
  implication: Add inclusive-frame regression, reserve boundary plus lookahead under native100 cap.
- timestamp: 2026-10-12T21:35:05.826398+00:00 (proof-write time)
  checked: original userio-secretary-seven100 bounded red case
  found: exit75 before payload; foreign memory.swap.max hard capacity unverified. No tests executed and no GREEN/RED test verdict claimed.
  implication: Preserve refusal; stock runner/capacity owner must repair the foreign accounting boundary. No cap raise, bypass or foreign process action.

## Candidate / verification

Small owned candidate in messenger_max.py: actual page capped98 with two native
lookahead slots; inclusive boundary is excluded as before, timestamp ties remain
honestly partial. Public result exposes page_limit. Two focused integration
regressions cover limit2 continuation and native100 ceiling/no duplicate loss.
Expected5/max12 seconds; full related52-test ordinary expected25/max70 seconds,
with the original hard180-second aggregate and unchanged finite runner budget.
Syntax checks passed. The old RED runner attempt executed no payload; the real
native cursor failure above is the RED. After supported accounting repair,
a new current-source ordinary case passed all52 related checks in25.79seconds,
helper0/run-u9235/peak91209728bytes/swap0/OOM0/same-generation cleanuptrue.
No old case or accepted provider action was replayed.

Source628812011477bcc216dece528de66884a54ee52e was scoped committed and pushed
to main, installed through the normal rollback/manifest route, then verified
against manifest4f4d782a12e53be2e58013cdfd2f7582e59bd20c09e901293677bce5b43e3c3e.
Core588228 and its normal ingress dependency are active/restarts0. No Exmanager
source/config/runtime was changed by UserIO.

Final native Secretary MCP read reached three consecutive limit2 MAX pages.
Page2 now retains its cursor; page3 includes known older117424822620729543.
All6 sources distinct, actual authors/scopes preserved, partial=true/readACK=false,
model0/send0. Proofs: .tmp/native-v13-final-20261013/history-green-output.json,
history-green.txt, history-verify.json, native-history-fixed.json. This closes
this pagination regression, not MAX model reasoning or transport retirement.

Resource executor: R38 actual native turn01a1320b-f7d1-7412-ab43-d70b8562a5ed,
confirmed by Root97 and accepted direct steering. Previously idle/skipped R38
message is not represented as active repair. Exmanager business/person-memory
work remains independent; this UserIO candidate creates no whole-project hold.
