# UserIO topic production acceptance — 2026-10-06

Status: blocked on the coordinated Notice Place deployment window owned by
Codex session `01a1106f-30c3-79f0-9260-ca4810dc1f2a`.

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
`userio-hermes-dispatcher.service` and deploy `universal-userio` `73f4167`
through its supported release path. Send one fresh Secretary (`8810909089`) to
Nikita (`540308572`) canary and require all of:

1. exact receiver message ID and UserIO event ID;
2. successful structured triage receipt;
3. durable Notice Place HumanRequest receipt when the decision is `notify` or
   terminal `review`;
4. visible delivery in chat `-1004322359393`, topic `5764`.

If the fresh message correctly classifies as `silent`, use a separate clearly
important test message; do not weaken the importance threshold or fabricate a
notification from an ordinary message.
