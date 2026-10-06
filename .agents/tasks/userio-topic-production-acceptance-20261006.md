# UserIO topic production acceptance — 2026-10-06

Status: blocked on the coordinated Notice Place deployment window owned by
Codex session `01a1106f-30c3-79f0-9260-ca4810dc1f2a`.

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

## External dependency and smallest next action

Do not replay terminal event `39204`. After Agent Herder's API is ready, the
Notice owner must perform one combined Notice Place upgrade containing
`287933f`, verify both Notice services, then restart
`userio-hermes-dispatcher.service`. UserIO's forced-tool fix is already live;
the final browser release uses current clean main containing `f02410f` through
the supported release path. Send one fresh Secretary (`8810909089`) to
Nikita (`540308572`) canary and require all of:

1. exact receiver message ID and UserIO event ID;
2. successful structured triage receipt;
3. durable Notice Place HumanRequest receipt when the decision is `notify` or
   terminal `review`;
4. visible delivery in chat `-1004322359393`, topic `5764`.

If the fresh message correctly classifies as `silent`, use a separate clearly
important test message; do not weaken the importance threshold or fabricate a
notification from an ordinary message.
