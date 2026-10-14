---
status: complete
mode: quick
completed: 2026-10-15
---
# Delayed unread-only automatic analysis, token grants and global pause

Direct mandate: wait5–10minutes and analyze related arrivals together; BEFORE
LLM exclude read/answered messages and AI/notification sources. Extensions:
fix the ineffective web checkbox; per-token read/write by platform/account/chat;
persistent global pause/resume button. Secretary's existing global read-only
credential remains unchanged by the owner's latest instruction.

Delivered source: UserIO fcf63da + c09f37e; dispatcher0c8496a + dc52f19.
Current installed UserIO c09f37e is exact published source with rollback receipts,
verified managed files/services and actual authenticated `/v1/runtime`.
Existing foreign messenger/QR and subsequent voice-worker WIP is preserved.
Only scoped owned paths were committed/pushed to main; no branch/worktree created.

## Actual acceptance —PASS

- Live normalized HTTP arrivals from an isolated QA user: immediate automatic
  claim refused; after302.063s TWO sources yielded ONE leader/batch, then no
  duplicate claim. Actual receipt `.tmp/batched-unread-release/batch-canary.json`.
  This queue canary deliberately started zero models/notifications.
- Actual existing human-read incoming and Secretary Excode source both returned
  `needed=false` through authenticated preflight, reasons already_read and
  automated_source. No marking/read acknowledgement or source replay.
- Ordinary headed server44 browser: sending disabled → analysis ON → OFF →
  reload/reopen → OFF retained; screenshot `toggle-off-reload.png`.
- Ordinary token form: selected account, chat1read-only/chat2read-write,
  created ONE token. Actual MCP read chat1 succeeded; write chat1 refused BEFORE
  provider. Existing token editor then removed chat2, saved, reload/reopened;
  token unchanged, exact selected chat rights retained (`token-rights-reload.png`).
- Ordinary global button: pause → reload → «Возобновить ИИ»/paused persisted;
  installed per-user preflight refused automatic_ai_paused; owner's other account
  remained unpaused. Resume → reload → «Приостановить ИИ» retained.
  Screenshots `global-ai-paused-reload.png` / `global-ai-resumed-reload.png`.
- A pre-existing disabled-chat approval loop (event52599) was repaired to terminal
  quarantine; live pending row terminal1, no more periodic submission attempts.
  Accepted/uncertain outcomes and old receipts are retained.

The owned browser used a finite native2GiB/swap0/CPU2/tasks384/IO10/1200s unit on44,
measured about291MiB/168tasks after launch; unrelated browser sessions untouched.
A restart-time502 was captured/inspected in the SAME page before reload. HTTP
readiness was confirmed before the final successful journeys; the failed frame
is retained as `restart-502.png` and never counted as successful acceptance.
Focus group skipped: explicit straightforward controls and deterministic policy;
actual semantic actions, persisted state and provider-bound refusals are decisive.

## Focused checks

Fast unit / focused HTTP integration only; no nightly run needed.
Initial relevant61cases31.11s; summary/context/token delta54cases38.21s;
pause delta22cases14.22s; final dispatcher91cases7.80s. Bounds768MiB high/hard,
256MiB swap,CPU2,tasks128,IO10; expected40s/emergency400s, and200s for short
subsets. UI builds26.63s and24.60s under512MiB/swap0/CPU1/tasks64/IO10.
Batch-to-model regression captures ALL related text in one triage call; read,
answered, bots, Excode and paused cases capture ZERO generation calls, including
summary-before-model and read-during-generation notification suppression.

Explicit limits: batches remain within each user/account/conversation (up to100
sources), with existing context/image budgets. Manual explicit analysis remains
available; pause governs UserIO automatic analysis, not other independent agents.
No real counterpart received a QA message or call. Other milestones and voice
work remain independent; no whole-project completion is claimed.
