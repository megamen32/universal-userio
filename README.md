# Universal UserIO

Universal UserIO is the business/control plane above Universal Inbox and
NoticePlace. It owns conversations, user identities, AI reply drafts and
approval. It does **not** poll providers, hold browser sessions, or select
delivery URLs.

## MCP first

Run `python -m universal_userio` to expose a stdio MCP server. Its primary
tools list unread messages, read a conversation, mark messages seen, create or
edit drafts, explicitly approve-and-send an exact draft, and delete only the
local UserIO copy. `userio.ai.propose` is the separate opt-in model tool.

No MCP tool claims remote provider edit/delete unless that account's adapter
declares and implements the capability; UserIO never silently deletes provider
data.

The deployed service exposes the same JSON-RPC surface at `POST /mcp`. It
returns JSON normally and SSE when the client sends
`Accept: text/event-stream`. Every request requires a user bearer token. The
legacy `USERIO_API_TOKEN` remains a service-account token mapped to the seeded
owner, so existing exmanager configuration keeps working.

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

`POST /auth/login` accepts `{"username":"…","password":"…"}` and issues a
user bearer token. An owner can create a user with `POST /v1/users` or
`userio.users.create`; both return the initial token once. Passwords are stored
only as salted PBKDF2-SHA256 hashes, while API tokens are stored only as
SHA-256 digests.

On startup, a gitignored `.env.owner-seed` containing
`USERIO_SEED_USERNAME` and `USERIO_SEED_PASSWORD` creates or updates the
service owner. Its values are never logged. Set `USERIO_OWNER_SEED_FILE` only
when the private file lives elsewhere.

`POST /v1/messages` accepts a canonical `universal.inbox.message.v1` envelope
and a configured `route_id`. It returns a proposed draft; it never sends a
reply.

`POST /v1/drafts/{draft_id}/approve` is the single send authority. The
`route_id` resolves to a server-side `NoticePlaceRoute`, so a caller cannot
supply an arbitrary destination, token, or provider URL.

`GET /v1/conversations/{conversation_id}` returns durable history and draft
state. All endpoints require a UserIO bearer token.

The unified MCP tools are `userio.channels.list`, `userio.channels.read`,
`userio.channels.download`, and `userio.channels.send_draft`. Mail, Telegram,
WhatsApp, and VK wrappers share that contract. `send_draft` only creates a
user-scoped proposed draft; `userio.draft.approve_send` with exact
`confirm: true` remains the sole delivery authority. Unsupported provider
features return `not supported by adapter`.

`POST /v1/identities` and `POST /v1/reply-rules` administer the control plane;
`GET /v1/inbox` returns unread cross-channel messages, and
`POST /v1/inbox/seen` marks one canonical source message as seen.

`GET /` serves a small human dashboard. It contains no message data itself;
the browser supplies the UserIO API token only when calling the protected API.
An authenticated internal reverse proxy is recommended for production.

## Подключение ChatGPT как MCP-коннектора

1. Опубликуйте loopback-сервис через свой reverse proxy по **HTTPS**, например
   `https://userio.example.com/mcp`. Сам UserIO по умолчанию по-прежнему
   слушает только `127.0.0.1:18093`; TLS и публичный DNS находятся на proxy.
2. Создайте отдельного пользователя от имени owner. Начальный токен
   возвращается только в этом ответе:

   ```bash
   curl -sS https://userio.example.com/v1/users \
     -H "Authorization: Bearer $USERIO_API_TOKEN" \
     -H "Content-Type: application/json" \
     --data '{"username":"chatgpt-user","password":"use-a-long-private-password"}'
   ```

   Новый токен также можно получить через
   `POST https://userio.example.com/auth/login` с теми же
   `username`/`password`. Не помещайте пароль или токен в git, логи и README.
   Перед ingestion/отправкой owner привязывает разрешённый server-side route к
   пользователю и каналу (чужой `route_id` обычный пользователь выбрать не
   может):

   ```bash
   curl -sS https://userio.example.com/v1/channel-routes \
     -H "Authorization: Bearer $USERIO_API_TOKEN" \
     -H "Content-Type: application/json" \
     --data '{"user":"chatgpt-user","source":"telegram","route_id":"telegram-reply"}'
   ```

3. В настройках custom connector ChatGPT укажите URL
   `https://userio.example.com/mcp` и аутентификацию API key/Bearer. Значение
   заголовка: `Authorization: Bearer <USER_TOKEN>`.
4. Endpoint поддерживает обычный JSON-RPC POST и streamable SSE на том же URL.
   Быстрая проверка SSE:

   ```bash
   curl -N https://userio.example.com/mcp \
     -H "Authorization: Bearer $USER_TOKEN" \
     -H "Content-Type: application/json" \
     -H "Accept: text/event-stream" \
     --data '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
   ```

Каждый токен жёстко привязан к одному пользователю: списки каналов, сообщения,
аккаунты, черновики, подтверждение отправки и локальное удаление изолированы по
`user_id`. Для ChatGPT не используйте legacy `USERIO_API_TOKEN`: он оставлен
только для совместимости сервисного аккаунта exmanager и видит данные owner.

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

### VK Web sidecar (browser extension)

`extensions/vk-inbox/` is a Chrome MV3 connector that runs **inside the user's
already-logged-in VK Web browser**. It is a true sidecar: it does not hold VK
credentials or cookies on the UserIO side, it only translates the live VK Web
DOM into the same `universal.inbox.message.v1` envelope the rest of UserIO
sees.

Responsibilities:

- Capture every visible chat and message in real time (`MutationObserver` on
  `[data-testid="me_convo_list"]`, `.FCThumb`, and the open conversation).
- Index them locally (IndexedDB) so the popup can search across all messages.
- Forward each capture to `POST /v1/messages` with the configured `route_id`.
- Send messages: types text into the active composer and clicks the VK send
  button, then logs the outbound message locally with `direction: "out"`,
  `status: "sent"`. No provider credential ever leaves the browser profile.

Install once per machine where the user wants VK Web capture (typically the
operator's own machine — not a shared server):

```
cd extensions/vk-inbox
./scripts/build.sh                      # rebuilds static zips
# Then in the target Chromium:
#   1. open chrome://extensions, enable Developer mode
#   2. Load unpacked -> select extensions/vk-inbox/
#   3. open the extension options, set endpoint + route_id
```

The service serves the packaged zips at `/vk-userio-extension.zip` and
`/vk-userio-extension-mv3.zip` for clients that prefer drag-and-drop
downloads over a local checkout. Both zips are produced from the same
`extensions/vk-inbox/` source tree by `extensions/vk-inbox/scripts/build.sh`.

See `extensions/vk-inbox/INSTALL.md` for the full install procedure and
`extensions/vk-inbox/README.md` for the connector's own notes (DOM selectors,
IndexedDB schema, security boundary).

## Run

Copy `.env.example` into deployment-owned secret configuration, set the UserIO
API token, AI token/model, and the token variables referenced by the route
registry. Then run `universal-userio`. The service binds to `127.0.0.1:18093`
by default; publish it only through an authenticated internal ingress.

`deploy/universal-userio.service` and `deploy/INSTALL.md` provide the
loopback-only systemd deployment contract.

## Universal Inbox connection

Configure Universal Inbox with `UNIVERSAL_USERIO_INGRESS_URL`, a UserIO API
token, and a `source → route_id` map. Inbox forwards each canonical durable
message to `POST /v1/messages`; UserIO acknowledges the message before Inbox
advances its source cursor. A shared trusted watcher should include its
`account_id`; Gmail sources in the form `gmail:<alias>` are also resolved
against the owning account automatically. The map is business routing metadata
only, and non-owner routes must first be assigned with `/v1/channel-routes`.

UserIO's `USERIO_ROUTES_JSON` is the reverse safe boundary: each `route_id`
resolves to one NoticePlace endpoint and the name of a deployment-owned scoped
token variable. Thus the model, HTTP caller, and Inbox cannot choose an
arbitrary recipient or send provider credentials.
