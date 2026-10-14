---
status: resolved
trigger: Automatic calls during quiet hours and premature hangup
created: 2026-10-15
updated: 2026-10-15
---
## Symptoms
00:40Moscow calls from NoticePlace incident chain; Matrix/Telegram disconnect.
## Evidence
Live mesh settings00:00–12:00, calls enabled. Global quiet check only physical
phone branch. Latest calls concern Proxy5 traffic saturation and comment generator;
all severity critical, no emergency override. Builtin consumers have no quiet rules.
Telegram answered calls fail AttributeError PreparedCall.peer_account_ref;
canonical field account_ref. Matrix latest receipts await60s then timeout.
## Current Focus
Fix global quiet check before reservation; adapter contract; inspect native
MatrixRTC answer/signalling; establish UserIO canonical voice source without
altering previous owner WIP.
Temporary native automatic_calls_enabled=false during repair; restore only after
all-channel quiet gate installed and verified. Preserve receipts, no redial.

## Resolution and exact acceptance boundary
User corrected time to00:40 Moscow; retain existing00:00–12:00, no new timer.
NoticePlace2027ecd published: gate every *.call before reservation, critical
suppressed and emergency exception retained.19focused checks12.13s passed;
installed immutable one-file overlay on100,44,88;21uncertain calls preserved.
Temporary automatic_calls_enabled hold restored true after all three installs.
Exmanagerb57ae06 fixes real PreparedCall.account_ref;8checks0.407s passed;
immutable adapter installed behind existing compatibility path.
UserIO62b0e20 published/installed: accepted voice source migrated from published
Admin74fb73dc,c1d246f66d,7e6d46ce12, new runtime pathsrc/universal_userio/voice;
64waiting Telegram updates no longer overflow32event queue before answer;
Matrix ignores unrelated participant disconnects during ringing.
55focused checks2.14s passed (1optional skip); migrated engine56checks9.31s passed.
First migrated engine fixture run53/56 passed: production operator config leak
into fixture isolated and rerun green; no provider call/identity change used.

Owner authorized NEW Matrix canary1: locked-iPhone incoming UI disappeared;
server retained membership/ringing60s and only then closed unanswered. This
failure remains preserved. No old admitted operation was replayed.
Owner authorized NEW canary2 after unlocking. It rang for~10s, answered at
01:02:53Moscow, held bidirectional speech24.969s and ended on explicit farewell.
Exact callb16e121e-ef22-5012-9719-ab4f874b386d/voice49a1eecd-45a0-4e7a-8efb-b71ff61cf3c2;
48k caller/assistant1,198,510samples each, final transcriptionCOMPLETE.
Real recipient said “Всё работает”, then “Давай, до свидания”; chatconfirmed
“все работает ладно может ты прав это было на заблокированам”.
Artifacts:.tmp/quiet-calls-20261015/, native archive contains both live/final transcripts.

Accepted: quiet gate installed everywhere; source/runtime owned byUserIO;
actual unlocked-iPhone Matrix dialogue and teardown. Telegram fix installed,
new native Telegram dialogue not exercised this task. Locked-iPhone incoming
UI issue is not claimed fixed: iPhone paired throughMac but Developer Mode
blocks native app log access; S21 contains noElementX, lease released. No new
security setting, iPhone software or personal-Mac GUI was introduced.
Smallest remaining step for locked-screen diagnosis: ElementXversion+app log
of that exact incoming event, then one explicitly requested locked-screen test.
External client issue5335 is a related hypothesis only (owner said UI vanished
before answering), not a proven diagnosis. User's successful unlocked acceptance
is preserved without inflating it into locked-screen or Telegram acceptance.

Admin inventory note prepared atdocs/inventory/services/userio-voice.md;
publication blocked by existing9ahead/13behind main plus foreign bridge/session
WIP. Do not merge or push foreign commits merely to publish this note. Product
ownership doc is published inUserIOdocs/operations/voice-ownership.md. Existing
source WIP and unreviewedPR5 remain unchanged; canonical runtime does not use it.

## Owner's timing extension after Matrix acceptance
Direct request: “make timeout for waiting call to start on telegram longer please”.
Published/installedUserIO3ec9ba1: answer120s/connect60s, independent talk budget;
manager/gateway+340s, wrapper535s. NoticePlace058f7f1 HTTPfloor550s installed100/44/88.
Delayed-recipient75s+media35s virtual scenario passed; selected50PASS/1optionalskip
2.182s, NoticePlace20PASS9.914s. First timing fixture had an immediate-return
pump loop; only its owned bounded unit was stopped, corrected and rerun green.
No real call was made for this timing change. No new account/token/model or RAM/
CPU/parallelism increase. Rejection/provider termination still ends the wait early.
Core/gateway activated with no live model claims or nativeRTC/telephone calls;
retained14Oct10:18UTCringing JSON is historical, not a live call, preserved.
Relevant ingress services active; runtime_release verify passed, quiet gate remains
00:00–12:00 and critical_allowed=false. New Telegram live dialogue remains unclaimed.
