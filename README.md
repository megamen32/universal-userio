# Universal UserIO

Universal UserIO is the business/control plane above Universal Inbox and
NoticePlace. It owns conversations, user identities, AI reply drafts and
approval. It does **not** poll providers, hold browser sessions, or select
delivery URLs.

```text
providers -> Universal Inbox -> UserIO -> NoticePlace -> provider adapters
                           read       send only after approval
```

## MVP contract

1. `universal.inbox.message.v1` becomes one durable conversation message.
2. An AI provider produces a draft, never a delivery.
3. A human approves or rejects the draft.
4. Only approval emits `userio.reply.v1` to a scoped NoticePlace route.

The business control plane adds identity mapping (`channel external ID → person`)
and a per-person/channel reply rule. Modes are `suggest` (draft only),
`approve` (draft awaiting human confirmation), and `auto_send` (a configured
business rule permits immediate delivery through its scoped Outbox route).
An AI provider may return several independent drafts for the operator to pick
from; approval is tied to one selected draft.

Provider integrations declare `read` and `reply` capabilities. A VK or
WhatsApp Web browser worker owns its browser session and translates UI actions
to/from these contracts; UserIO sees the same conversation API as Telegram or
Matrix.

## Boundaries

- Universal Inbox owns source cursors, deduplication and canonical ingress.
- UserIO owns business identity, conversation state, drafts and approval.
- NoticePlace owns durable delivery, destination credentials, retries and
  provider receipts.

`route_id` is a UserIO control-plane reference. The deployment maps it to a
scoped NoticePlace consumer token; neither the AI nor a client submits a
provider credential or URL.

## Minimal API

`POST /v1/messages` accepts a canonical `universal.inbox.message.v1` envelope
and a configured `route_id`. It returns a proposed draft; it never sends a
reply.

`POST /v1/drafts/{draft_id}/approve` is the single send authority. The
`route_id` resolves to a server-side `NoticePlaceRoute`, so a caller cannot
supply an arbitrary destination, token, or provider URL.

`GET /v1/conversations/{conversation_id}` returns durable history and draft
state. All endpoints require a UserIO bearer token.

`POST /v1/identities` and `POST /v1/reply-rules` administer the control plane;
`GET /v1/inbox` returns unread cross-channel messages, and
`POST /v1/inbox/seen` marks one canonical source message as seen.

`GET /` serves a small human dashboard. It contains no message data itself;
the browser supplies the UserIO API token only when calling the protected API.
An authenticated internal reverse proxy is recommended for production.

## AI boundary

`OpenAICompatibleDraftGenerator` is the initial AI capability adapter. UserIO
passes it the recent canonical conversation history and receives text drafts or
variants. It does not pass Outbox credentials, provider credentials, or browser
session data to the model. The endpoint, model and token are deployment-owned
configuration for UserIO alone.

## Accounts and browser workers

`POST`/`GET /v1/accounts` maintain provider accounts as business capabilities:
`read`, `reply`, enabled state, and an opaque `credential_ref`. A VK or
WhatsApp Web worker receives that reference through deployment-owned capability
binding and returns its own receipt. UserIO and the AI never receive browser
cookies, an automation handle, or raw credential material.

## Run

Copy `.env.example` into deployment-owned secret configuration, set the UserIO
API token, AI token/model, and the token variables referenced by the route
registry. Then run `universal-userio`. The service binds to `127.0.0.1:18093`
by default; publish it only through an authenticated internal ingress.

## Universal Inbox connection

Configure Universal Inbox with `UNIVERSAL_USERIO_INGRESS_URL`, a UserIO API
token, and a `source → route_id` map. Inbox forwards each canonical durable
message to `POST /v1/messages`; UserIO acknowledges the message before Inbox
advances its source cursor. The map is business routing metadata only.

UserIO's `USERIO_ROUTES_JSON` is the reverse safe boundary: each `route_id`
resolves to one NoticePlace endpoint and the name of a deployment-owned scoped
token variable. Thus the model, HTTP caller, and Inbox cannot choose an
arbitrary recipient or send provider credentials.
