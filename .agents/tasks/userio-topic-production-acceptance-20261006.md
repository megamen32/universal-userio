# UserIO topic production acceptance — 2026-10-06

Status: requested mail/Matrix/UI and Telegram topic paths accepted live.
Additional terminal-review deep-analysis transition fixed in 5c5a365; final
source/runtime synchronization and canonical endpoint acceptance follows.

## Mail, Matrix, browser and AI-reply investigation

- Gmail and Matrix ingress stopped cleanly on 2026-10-05 when the core was
  restarted. Their old units were attached to multi-user.target only. Published
  `fa0f6e0` adds core WantedBy/PartOf and canonical bounded native units.
- Matrix /versions, /whoami and /sync pass; native cursor advances. No new
  real provider events after activation were available to ingest. Synthetic
  phase7 no-delivery acceptance is separate evidence, never proof of a real send.
- All three mail aliases authenticate and poll. Yandex's 500-envelope scan hit
  its 30s timeout; all current cursors/overlap anchors were checked in a 100-item
  snapshot with zero pending gaps before changing only the existing
  USERIO_GMAIL_SNAPSHOT_SIZE from 500 to 100. Repeated native cycles for all
  three aliases pass without errors. Provider identities/credentials unchanged.
- `1040eff` exposes a public canonical account source and latest-message time;
  it removes the false inference that old/no messages means account unavailable.
  The main Gmail account must use source gmail, not gmail:megamen932.
- `9966531` bounds list previews to 4096 characters while retaining full bodies
  and waits for real user capabilities before loading conversations.
- `f02410f` preserves grouped platform views but uses exact_source=1 for an
  individual account, and rejects stale list responses (including after read
  capability is disabled). Node and API regressions, typecheck/build pass.
- AI proposal controls were not removed. Their refresh race dates to August
  17/20. The first proven user complaint is October 5 07:49 UTC, before the
  October 5 09:01 triage commit and all October 6 fixes. October 4 data growth
  plausibly exposed the race; the exact first failure time is not proven.
- Native owner API has 12 proposed AI drafts in the existing Katya conversation
  conv_cedf4b78f9cfa282b3713505; account and owner reply/send capabilities are
  present. Final browser acceptance must verify the selected account's actual
  rows, then existing draft buttons and Предложить ответ without generating,
  approving, deleting or sending any replies.
- Browser evidence and safe probe are under the current thread artifact folder:
  /home/roomhacker/.codex/visualizations/2026/10/06/01a110c6-203c-7db3-8b86-fce6c3869d35.
  Early captured failures were inspected before retries; no unit-only UI claim.

## Delivered source

- `universal-userio` `73f4167`: triage uses one forced `submit_triage` tool call
  and still rejects unknown keys, wrong tools, invalid arguments and invalid
  field values.
- `hermes-unified-inbox` `4567622`: a non-retryable `review` creates a manual
  Notice Place request instead of completing silently.
- `noticeplace` `287933f`: HumanRequests use a project-specific Telegram topic
  from `telegram_topics_json`; other projects keep the default destination.
- Runtime route is staged only for project `userio`: Telegram chat
  `-1004322359393`, topic `5764` (`Разбор UserIO`).

## Evidence before deployment

- Secretary sender receipt `2297` matched Nikita receiver message `1981245`.
- UserIO persisted `telegram:540308572|8810909089:1981245` as event `39204`.
- Prompt-only MiniMax-M2.7 returned several incompatible top-level objects;
  the same redacted event through the forced tool contract returned a valid
  `silent`, importance `0.0`, confidence `1.0` result.
- Focused checks: UserIO `39 passed`; dispatcher `27 passed`; Notice Place
  HumanRequest/topic `19 passed`.

## Production delivery acceptance

- Notice route 287933f is live in reviewed d236108; project UserIO retains
  chat -1004322359393 / topic 5764 and unchanged thresholds/tokens.
- Normal test event 39507 completed silent, importance 0.1, and correctly did
  not create a HumanRequest. Terminal 39204/39332/39367 were not replayed.
- Clearly labeled priority technical test (no actual incident/call) matched
  the existing safety-direct rule: Secretary UID8810909089 sender2304 →
  Nikita UID540308572 receiver1981441 → event39515 eligible1/reconciled0 →
  completed notify/safety_override true (importance0.35) → durable
  HumanRequest userio-39515 → Telegram5849 at12:41:07UTC.
- Receiver Careviolan read actual message body and keyboard. Native Telegram
  thread link is https://t.me/c/4322359393/5764/5849, proving exact topic5764.
  Buttons were only inspected, never clicked; no external replies or calls.
- This proves priority transport, separately from AI-selected importance.
  The AI understood the registered Secretary identity and correctly recognized
  ordinary tests as low priority.
- UserIO runtime ddb92cd / core3820661 exact manifest
  5558d5954a757484fca4879d8c11c08e89bb0ba1ed65a716c5bd12c576b1be2c
  passed supported release verification and no-delivery readback canary.
- Final headed browser on that runtime: exact-source Gmail100 rows with only
  source gmail; existing Katya12 drafts,12 enabled Edit/Send controls and
  Предложить ответ. No generation/approval/send/delete action. Screenshots
  hide message text before capture and were inspected.
- Safe final structured evidence: current thread userio-delivery-final.json.

## Final identity-context repair

- Events 39332 and 39367 completed as silent (importance 0.2/0.3); no
  HumanRequest/HTTP notification was created. They are terminal and are not replayed.
- Receiver account telegram:540308572 and private peer 8810909089 were correct,
  but a stale contact label resembled the receiver. The AI context had no trusted
  account-qualified sender identity and included a self-request reason.
- The service now resolves distinct registered accounts within the same user
  for incoming direct Telegram messages only. The actual model request contains
  Secretary/Nikita IDs/names and preserves the raw contact label as supporting
  data. Groups, foreign users, unknown peers and outgoing messages remain
  unqualified. Identity does not force notify or lower policy thresholds.
- Scoped service/transport/claim tests: 41 passed. Reviewer found only a test
  assertion key mismatch, corrected before publication.
- Browser acceptance on deployed 3849f83: exact-source Gmail rows only; Katya
  selected with 12 existing AI drafts, enabled Edit/Send and Предложить ответ.
  No proposal generation, approval or external reply was performed. Privacy-safe
  screenshots/probe are in the current thread artifact folder.
- Next controlled priority message is explicitly a technical test, without
  fictitious incident or phone action. Its safety-direct receipt proves topic
  transport separately from ordinary AI importance decisions.

## Terminal review manual choice repair

- Live old userio-39389 was resolved to deep_analysis with authenticated actor;
  UserIO status review/attempts3/send_state none. A completed-only store guard
  rejected the canonical deep preparation endpoint, producing repeated HTTP400.
- Dispatcher 407f158 retains explicit notify/review mode, plus a narrow legacy
  dismissal recovery requiring the actual completed-triage-not-found error.
  Genuine legacy no-draft notify feedback remains enabled;30 tests passed.
- UserIO 5c5a365 lets exact authenticated deep preparation accept completed or
  terminal review, preserving current policy/user/event/request and no-send
  fences. Pending/running still reject. Regression shows no reclassification,
  no draft send and no replay; focused tests passed.
- Original event is harmless social text; previously authenticated human choice
  authorizes the existing read-only analysis route, not a new fabricated choice.
