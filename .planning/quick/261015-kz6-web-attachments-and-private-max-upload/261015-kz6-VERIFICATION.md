---
status: passed
---
# Real web acceptance

Tested installed source7cbb286 on authenticated https://msg.bezrabotnyi.com in own headed server44 session.

| Claim | Observed |
|---|---|
| Existing3PDFs | All named/size cards visible; exact original bytes for all3; reload/reopen retained |
| Composer | File selected, size shown, removed before send; empty send disabled; image preview decoded |
| Private delivery | ONE file and ONE image sent through normal web composer into own saved chat; fresh native history count1each |
| Persistence/download | Reload/reopen file+image retained; image96x64; both downloaded exactly |
| Narrow layout | All3download buttons remain inside1050px viewport after repair |
| No replay | Three company sends unchanged, each native count1; accepted new writes never retried |
| Checks | 38backendPASS,9frontendPASS,strictTypeScriptPASS,bounded buildPASS,runtime verifyPASS |

Tool timing/selector ambiguities and old-layout blocked clicks have pre-retry screenshots and are retained separately from accepted final results. No extra focus-group agent was created: user required exactly two implementation workers; actual private consumer and width variant were checked directly.

Repository acceptance follows explicit owner instructions: owned paths on remote main, unrelated WIP preserved without reset/stash. Only docs/state completion follows final runtime canary; it changes no deployed behavior.
