---
status: complete
quick_id: 261015-hvf
completed: 2026-10-15
---
# Indexed exact and typo search — delivered

@semerindima now returns the actual Дмитрий Семерин conversation first. Full name,
first/last name and formatted/unformatted phone resolve the same conversation.
The previous recent50000-message Python scan missed older dialogue metadata and
never retained provider usernames/phones. A maintained normalized FTS5 trigram
index now covers all57796 nondeleted messages and1104 conversation documents.
Profiles stream read-only through existing connected Telegram clients,100/page,
startup before history and hourly. Edits, deletes, renames and refreshed profiles
maintain the index; interrupted first migration rolls back and safely retries.

Exact contact results precede text mentions. Exact and typo groups independently
collapse and page50 results; fuzzy completion does not delay exact display.
Generation checks suppress stale response replacement. Loading/debounce shows
Ищу… and no empty result; empty appears only after completion, errors explain
failure separately. Typo scoring is bounded to5000 indexed candidates, short
(<6characters) typo queries cover contact names; exact search covers all text.

## Acceptance

Production core ff167fa and connector38be57f installed from published Git blobs
using rollback receipts and manifest verification. All intentional restarts
precede the final8-journey headed browser44 canary. Real target381703703 found
by username, full name, surname, formatted phone and swapped-letter username.
Native collapse hides rows; exact Показать ещё appended100 unique results;
normal page reload repeats username lookup. Real fetch delayed1800ms proves
loading/no false-empty, followed by genuine empty result. No sends or read ACKs.
Browser evidence: .tmp/search/browser-proof.json, final-found.png, loading-state.png.
The full canary passed twice, including after the final core install/restart.

Focused checks:7search units PASS5.57s;51direct store/service/phone/edit/account
checks PASS32.34s; modified contact-ingress HTTP test PASS1.36s;4Node checks PASS.
UI TypeScript/Vite build PASS26.126s. Initial production-copy index13.151s,
peak59784KiB RSS; live exact requests17–199ms and typo897ms under existing caps.
Runtime verification verified=true; no service restart/OOM increase; existing
384/512MiB core and224/256MiB ingress caps retained.

## Preserved boundaries

Foreign messenger.py/new messenger_telegram_mirror.py and the original8-line
server.mjs reconcile patch remain untouched/uncommitted; source publication uses
only owned paths. Own disjoint server import/startup/hourly hooks were staged
from exact Git blobs; foreign patch content confirmed identical afterward.
The pre-existing service64/64thread exhaustion and502 were captured in the owned
browser, then cleared by the authorized deployment/restart. Steady new core8tasks.
AX snapshots timed out in Agent Browser; same-session screenshots were captured
and inspected before switching to semantic CSS actions plus DOM verification.
One unsupported test selector was corrected after screenshot/inspection; all
final journeys passed. Other GSD phases and pending foreign work stay independent.
