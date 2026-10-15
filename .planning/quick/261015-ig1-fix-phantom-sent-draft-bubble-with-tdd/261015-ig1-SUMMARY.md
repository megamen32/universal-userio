---
status: complete
quick_id: 261015-ig1
completed: 2026-10-15
---
# Phantom white draft card — TDD delivery

The 6October approved draft “Сейчас проверю” was appended after current messages.
The Telegram provider receipt cannot equal the native message ID; receipt-based
filtering also depends incorrectly on the loaded history page. bg-primary/90
made the non-message card white in dark mode. Completed approved/rejected draft
records now stay out of the timeline independently of provider/window; canonical
messages retain their native dates. Proposed/sending records remain visible,
including the no-replay warning, with explicit light/dark background and text.
Durable receipt, timestamps and message history remain unchanged.

TDD: extracted the old filter unchanged, then3regression cases failed as expected
(.tmp/phantom-draft/red.txt). After the fix3regressions plus6direct UI helper cases
passed9/9 in0.88s (.tmp/phantom-draft/green.txt). TypeScript/Vite build passed26.04s,
then25.60s after the additional proven same-query search fix. No broad suite.

Actual headed44 browser reproduced the old white card before delivery (red.png).
After final installed15b6f9b,6journeys PASS: real target200canonical messages with
no completed draft cards in dark/light, fixture-only proposed/UNKNOWN previews
with correct colors/warning, ordinary reload still no phantom, and unchanged
search input retains existing results. Real preference GET was mocked ONLY in
this owned browser to auto_read=false, preventing read ACKs without changing the
owner preference. Preview drafts existed only in intercepted GET responses; no
DB rows, sends, approvals or receipt replay. API history/receipt readback remains
unchanged. Proof: .tmp/phantom-draft/browser-proof.json, dark-real.png and explicitly
fixture-only dark-draft-preview.png. Runtime manifest verified; existing native
budgets and active ingress dependents restored. Foreign connector/messenger WIP
is preserved. Owned browser/profile and private login copy cleaned on completion.

The first live acceptance exposed an owned search regression: identical input
cleared results/set loading without rerunning its query effect. Browser RED
captured empty/loading>16s despite HTTP200 search responses (failure.png). Input
now ignores identical values; the same real fill is GREEN in final acceptance.
One preview script await syntax error was captured/inspected before correcting
that test-only script; all final journeys then passed, including final real reload.
