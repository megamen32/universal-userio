# Secretary transport through UserIO

Baseline source audit, 12 October 2026. v1.3 implementation is now active; current Exmanager
transport and business work remain active. Exmanager files were read only.

## Decision

UserIO can own messenger connections, inbox history, media access and provider
delivery. Exmanager keeps the Secretary application on the official Airlock SDK:
reasoning, tasks, reminders, L1/L2/L3 memory, human confirmations and group roles.
It should become a thin UserIO consumer rather than maintain another Telegram
session/transport. Current UserIO does not yet provide every required operation
through one generic contract, so removing the existing transport now would lose
working features.

## Actual reusable paths and gaps

| Need | Existing source | Remaining UserIO work |
|---|---|---|
| Normal Airlock connection | Exmanager `airlock-agent/userio.go` already uses SDK `RegisterMCP` (`MCPAuthToken`) | Retain this encrypted credential/resource route, not a new connector store |
| Accounts, chats and reading | `userio_accounts`, `userio_chats`, `userio_read`; Telegram aliases already use UserIO | Normalize bounded paging, partial/truncated flags, author and account identities across channels |
| Inbound event cursor | `userio_workspace_sync` and `userio_workspace_events`; durable UserIO workspace events | Thin native SDK ingress binding preserving actual sender, source message, chat/group/thread and account; a tool connection alone is not inbound delivery |
| Text proposal and approval | `userio_draft`, `userio_approve_send`, exact immutable confirmation snapshot and provider receipt | Preserve the authoritative native/user approval and durable idempotency; expose provider reply/thread anchors, not only a plain body |
| Group administration | Existing relay grants and SDK actor/group context | Carry provenance from trusted ingress; never reinterpret group authors as private owner commands. Business role decisions stay in Exmanager/Airlock |
| Rich history and AI media | Exmanager `airlock-agent/telegram_chat.go`, signed history/media path; UserIO native media/context reader | Shared source-bound history/media contract with MIME, download references and honest remaining cursor |
| Reactions | UserIO HTTP/legacy MCP `userio.messages.react`; `service.react_to_message` currently checks Telegram | Effective capability contract; align MCP catalogs/dispatchers and implement channel-specific support/fallback |
| Message editing | UserIO real MAX edit path; Exmanager `telegram-live.ts` has Telegram edit/streaming progress | Generic account-bound edit operation, own-message check, same provider ID, receipt and cached projection; preserve text entities |
| Files and stickers | UserIO can read/download media; generic draft path is text only. Exmanager transport has source-bound file sends | Supported attachment/sticker send through normal approval/outbox, provider-supported capability and explicit unsupported result |
| Typing and streaming | Exmanager transport has typing/progress/edit behavior | Capability-led progress on each channel, with plain final-message fallback where editing/typing is unavailable |

Relevant UserIO source: `contracts.py`, `adapters.py`, `service.py`,
`mcp_surface.py`, `mcp_catalog.py`, `mcp_dispatch.py`, `http_api.py` and
`store.py` under `src/universal_userio/`. Relevant Exmanager source:
`airlock-agent/userio.go`, `airlock-agent/telegram_chat.go`,
`src/airlock-telegram-relay.ts`, `src/relay-userio-account-bridge.ts` and
`src/channels/telegram-live.ts` in its owning checkout.

The inspected Exmanager module pins official `github.com/airlockrun/agentsdk`
v0.7.0. Its installed upstream README assigns routes, tools, native chat, scoped
storage and credential-managed LLM access to Airlock/SDK. Follow the
[official SDK guide](https://airlock.run/docs/agentsdk/) when wiring that consumer.

## Migration order

1. Define per-account effective capabilities as provider support intersected
   with account and credential permissions. Read-only owner credentials stay
   read-only; a `reply` flag is not proof of edit/reaction/sticker rights.
2. Complete the thinnest generic source/history/media and reply/receipt vertical
   needed by the existing Secretary. Keep provider sessions at their current
   supported owners; no session copying or duplicate authorizations.
3. Verify equivalent real Telegram and MAX consumer journeys through native
   Airlock. Preserve group authors, source quotations, cursor completeness,
   exact approval, idempotency and uncertain outcomes.
4. Retire only the dedicated transport paths proven replaceable, after the
   Exmanager owner integrates and accepts the thin SDK wiring. Keep rollback
   and retained business receipts. No replacement LLM loop or Airlock core fork.

Active GSD context: [UserIO v1.3](../.planning/milestones/v1.3-MESSENGER-PARITY-CONTEXT.md).

## Implementation contract

The account/credential intersection, common MCP catalog, paged stored/provider
history, actual media/source resolve, source-bound action journal, distinct
`messenger_actions` credential and current-client provider routes are implemented
in the v1.3 source slice. See [callable contract](messenger-contract.md).
Native Secretary sender remains900000103; private900000101 readonly access
is unchanged. Source/focused checks do not claim deployment, rich media parity
or native acceptance; Exmanager owns its thin SDK/relay installation and cutover.
