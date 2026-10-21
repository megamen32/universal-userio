# Communications ownership: UserIO and Notice Place

Authoritative product-boundary contract, clarified by the owner on 2026-10-20.
This document defines responsibility, not a claim that every account or deployed
adapter supports every operation. Read installed API/MCP schemas and capabilities
before use. Existing consent, account scope and delivery safeguards still apply.

## Two products, not a collection of competing callers

**UserIO is the universal communications gateway.** It owns connected accounts,
chats, messages, attachments, history, incoming/outgoing provider operations,
calls, meetings and their media/events through one authenticated API/MCP surface.
Channel-specific adapters implement transport, not independent user-facing products.

**Notice Place is the notification and human-decision application.** It owns
agent questions, incident/escalation policy, recipient preferences, contact hours,
choice of an eligible channel, deduplication and the canonical answer to a request.
Its API/MCP is the agent-facing entry point for operator notification/question
calls. It delegates actual communications to the authorized UserIO transport.
Notice Place must not grow a second account store or telephony/messenger stack.

```text
Agent or application
  -> Notice Place API / MCP       question, notification, escalation, contact policy
  -> UserIO API / transport      authorized account, provider operation, call/media events
     |-- Telegram               messages and supported Telegram voice calls
     |-- Matrix / MatrixRTC     rooms, messages and supported Matrix voice calls
     |-- Telemost               Yandex chats and supported meeting participation/recording
     |-- Cellular / SIM         configured SIP + GSM2SIP/Android bridge
     `-- Other channel adapters email, SMS, MAX, VK, WhatsApp, ...
```

Ordinary reading and messaging clients continue to use UserIO API/MCP directly.
A low-level UserIO call capability does not authorize an agent to bypass Notice
Place's operator-contact policy. Neither layer may infer a new recipient, widen
account access or retry an uncertain external action under a new identifier.

## Components below the product boundary

| Name | Responsibility | Not its responsibility |
| --- | --- | --- |
| UserIO backend | Account authorization, capabilities, normalized history, durable provider-operation receipts, API/MCP | Deciding that an unresolved agent task permits calling a person |
| UserIO Voice | Internal execution of supported audio/RTC operations, speech and media processing | A second public notification product |
| AgentCall | Existing internal voice/AI bridge reused by UserIO Voice; an implementation name retained in code, sockets and runbooks | A third independent product or permission to call directly |
| Asterisk | SIP call switching and audio bridging where the configured cellular transport uses it | Telegram, Matrix and Telemost as a universal mandatory route; AI reasoning |
| GSM2SIP on Android | Bridge between a compatible phone/SIM and SIP for the cellular transport | A substitute for UserIO or Notice Place; a prerequisite for internet-only calls |
| Agent Herder | Route a confirmed decision back to the exact originating agent session | Originate telephone calls or interpret an accepted call as a human answer |

AgentCall, Asterisk and Android may participate in the SAME cellular call. An
Android adapter error therefore does not prove all calling is unavailable.
Likewise, a running socket, process or container does not prove SIP registration,
an answered call, audible speech or a completed decision round trip.

## Separate channel capabilities

- **Telegram:** registered UserIO account sessions are owned by the Telegram QR
  connector. QR, phone and imported sessions converge on its stored GramJS
  session; account operations use that account-scoped connector. The explicit
  standalone `USERIO_LIVE_TELEGRAM` fallback remains a legacy single-account
  embedding path, not a second client for a registered account. Do not run
  another live client with a copied auth key, because it can steal that
  session's updates.
  The existing account-bound voice implementation is
  [`telegram_calls.py`](../src/universal_userio/telegram_calls.py) and its
  worker/relay transport. Message reading is not voice-call authorization. The
  current call implementation has owner/account restrictions; do not promise
  arbitrary accounts or group calls.
- **Matrix:** [`matrix_calls.py`](../src/universal_userio/matrix_calls.py) implements
  separate MatrixRTC calls and durable receipts. Linking a read-only Matrix inbox
  does not automatically provision the RTC backend, joined room or call consent.
- **Telemost:** [meeting documentation](telemost.md) describes the implemented
  join/record/stop flow and its visible participant. Meeting recording is not the
  same capability as dialing an arbitrary person or publishing two-way AI audio.
- **Cellular:** [SIM setup](guide/calls.md) and [Android connection](PHONE-CONNECT.md)
  cover the configured phone/SIP path. Asterisk and GSM2SIP belong here, not above
  every internet channel. Device authorization and a real audio check are required.

Discover exact installed methods with `tools/list` or the applicable HTTP schema.
Do not document a guessed common `call` endpoint as if it already existed. Source
support, installed adapter, account permission, provider readiness and observed
end-to-end success are separate facts.

## One request, observable delivery

Notice Place preserves the human request identity and originating native session;
UserIO preserves the account, target, provider request and delivery/call receipt.
Correlate those identities without putting credentials or phone numbers in docs.

A request may be accepted or queued without being dialed. A provider call ID or
ringing state is not proof of an answer. An answered call is not proof of an
explicit decision. Only an authorized, correlated, confirmed answer resolves the
original question and permits the bound session to continue. These are semantic
stages; provider-specific status enums are not interchangeable.

On unknown delivery, reconcile the existing receipt; never blindly redial or send
via another channel. When a safe fallback is permitted, preserve the same question,
its unresolved state and approved recipient/channel policy. Quiet hours, mute,
expiry, cancellation, human Stop and one-time exceptions remain explicit.

## Operation, documentation and migration

- [Container deployment](container-deployment.md) owns UserIO runtime placement and
  safe update/rollback instructions. A historical host unit may be a lease adapter,
  not the owner of the current voice container. Check the actual process/container.
- Physical SIM/SIP topology and device runbooks belong to
  [ServersAdministartion](https://github.com/megamen32/ServersAdministartion/tree/main/infra/android-s21/gsm2sip).
  Do not restore obsolete services solely because their names look familiar.
- [Notice Place](https://github.com/megamen32/noticeplace) owns notification and
  question policy. Its legacy direct Android/Matrix adapters are migration evidence,
  not permission to maintain competing public calling paths. This contract alone
  does not switch or remove any installed adapter.
- Keep this contract canonical. Project READMEs, agent instructions and guides link
  here; localized guides explain it without replacing technical contracts. Preserve
  historical receipts and raw feedback rather than rewriting them as current state.
- Simplify ownership and entry points first. Moving/removing processes requires a
  separate scoped change, active-call checks, preserved credentials/pending requests,
  a verified replacement and rollback. Documentation cleanup must not interrupt calls.
