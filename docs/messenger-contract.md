# Messenger contract for the Airlock Secretary

The Secretary uses one authenticated UserIO MCP connection for messenger
operations. UserIO owns accounts, real source projections, approvals and durable
receipts. The official Airlock SDK owns reasoning, group roles, memory, business
tasks and the human confirmation. No provider login or Telegram session is copied.

Implementation milestone v1.3 is active. **This source contract is not a claim
that the live Secretary migration has completed.** Native acceptance and scoped
transport retirement are phase15.

## Normal MCP tools

The HTTP and stdio surfaces share `mcp_catalog.MESSENGER_SPECS`.

| Tool | Inputs | Result |
|---|---|---|
| `userio.messenger.events` | `account_ref`, optional `after` nonnegative integer, `limit`1–100 | Account-scoped incoming live events with canonical message, seq and analysis eligibility. Omit after to initialize at head without dispatching history; persist next_cursor. No read ACK or model call |
| `userio.messenger.capabilities` | `account_ref` | Actual configured provider/account/credential intersection: each operation has `supported`, `allowed`, `reason` |
| `userio.messenger.resolve` | `account_ref`, `peer_id`, `provider_message_id` | Actual source fetched on the current provider client and imported normally; canonical `conversation_id`, source and author. No fake conversation or read ACK |
| `userio.messenger.history` | `account_ref`, `conversation_id`, `limit`1–100, optional `cursor`, `refresh`false | Chronological page; stored mode is partial/unverified, provider mode returns its actual cursor and freshness. Cursors belong to that mode/account/conversation. MAX provider returns at most98 rows (`page_limit`) to reserve its inclusive boundary and continuation lookahead within native100 |
| `userio.messenger.media` | `account_ref`, `conversation_id`, `message_id`, optional `attachment_index`0–7 | Exact bounded attachment from the owned message; file results include original base64 bytes, filename, MIME, size and SHA-256. No read ACK |
| `userio.messenger.upload` | Exact account/conversation/source, filename/MIME/base64 bytes/SHA/request_id | Private bounded new-file staging, no provider send. See [new-file contract](contracts/messenger-upload-v1.md) |
| `userio.messenger.propose` | `account_ref`, `conversation_id`, `source_message_id`, `action`, `payload`, `request_id`16–128 characters | Immutable action with `id`, `hash`, `created_at`, `status`, `envelope`, `receipt`. No provider mutation |
| `userio.messenger.telegram_admin.propose` | Telegram `account_ref`, one of `group_create`, `group_invite`, `group_remove`, `channel_create`, `channel_delete`, exact payload, `request_id` | Read-only preflight and immutable action proposal. Does not change Telegram until the existing `approve` tool confirms this exact hash |
| `userio.messenger.approve` | `action_id`, `expected_hash`, `confirm:true` | Execute exactly the human-approved action. Durable accepted receipt or explicit failure/unknown |
| `userio.messenger.get` | `action_id` | Read the durable outcome; never generate a second intent to resolve `sending` |

Payloads: reply`{text}`, edit`{text,expected_text}`; edit source is the exact
outgoing target. Reaction`{emoji}`; sticker`{media_message_id,attachment_index}`;
file additionally allows `caption`; typing`{active}`. Media references must be
actual messages in this owned conversation. Each provider advertises only
implemented operations; unsupported file upload is not inferred from media reading.

Reaction`{emoji:""}` removes the current account's reaction through the same
immutable approval path; whitespace is invalid. MAX uses its native cancel
command, not an empty emoji added to the message. Fresh MAX resolve includes
`reaction_info` and `reaction_readback.status`; auxiliary read failure is
explicitly unavailable and does not hide the message or imply zero reactions.

Telegram administration is an additional `telegram_admin` right on an existing
`scoped_messenger` account grant. Group/channel creation requires account-wide
chat scope; invites, removals and channel deletion also require the exact target
conversation to be inside that token's chat grant. A basic group is distinct
from a supergroup (represented by Telegram as a megagroup channel) and a
broadcast channel. `group_create` takes
exact numeric Telegram user IDs; invite/remove require one existing UserIO
conversation. Removing a member from a supergroup is a ban and requires
`ban_on_supergroup:true`; UserIO never automatically unbans. Basic-group removal
sets `revoke_history:false`. New broadcast channels are private by default.
`channel_delete` requires `DELETE CHANNEL <peer_id>` in the proposal and the
selected account must still be the channel creator at execution time. Leaving a
channel is a separate Telegram operation and is not substituted for deletion.

Creation and participant changes are durable-once. If Telegram's outcome or the
HTTP response is uncertain, retrying the same exact approval only asks the
connector for its saved result; a new intent is never generated. Group creation
can be partial; any known `missing_invitees` remain visible in the receipt.
MAX SavedMessages uses its actual peer`0`, not a guessed personal contact.

Native sticker references use the received opaque attachment identity. In MAX,
the received global stickerId/setId is sent as `attaches[].STICKER`; the older
top-level sticker shortcut expects a different set-local ID. Keep native IDs
as decimal strings at the public boundary and preserve the actual sticker type.

Typing discovery includes `stop_mode`: `provider_command` for explicit native
stop, or `expires_automatically` for MAX. MAX active=false submits no read ACK
and relies on the short native indicator expiry; it does not claim immediate
cancellation. Native/recipient observation is distinct from provider ACK.
Telegram history omits only identity-unavailable records after scope/origin
validation and reports partial; direct resolve of such a record still refuses
to invent an author. Its provider cursor is retained.

Provider IDs are opaque **strings**, including MAX IDs above the JavaScript
safe-integer range. Do not substitute the SDK `stable_ref_id` hash. Telegram
canonical message ID is `account_ref + '|' + peer_id + ':' + provider_id`.
MAX canonical message ID is `max-` + actual chat ID + `-` + actual provider ID.

## Source binding

Messages expose `source`, `author`, `text`, `at`, `edited_at`, `media`.
Source includes exact account/conversation/peer/provider/local message IDs,
channel, direction, kind, reply-to and thread IDs. Source.invocation is
`{mention:bool,reply:bool}` from verified provider routing, never a text guess.
Unknown/native-unsupported invocation flags are explicitly false. Author ID is the actual
participant; name/first/last/username are display fields and never authority.

Approval binds the following exact provider fields:

```json
{
  "account_ref": "telegram:900000103",
  "peer_id": "-1009000002001",
  "provider_message_id": "123",
  "author_id": "456",
  "direction": "incoming",
  "text_sha256": "SHA256 of the exact raw UTF-8 source text",
  "thread_id": "",
  "reply_to_provider_message_id": "",
  "edited_at": 0
}
```

All IDs are strings. `edited_at` is integer Unix seconds, zero when absent.
`source_hash = SHA256(canonical(source_binding))`. Canonical JSON sorts object
keys, uses UTF-8 without ASCII escaping, no whitespace, no NaN; no floating
point values occur in the signed envelope. Display names, local IDs and local
arrival time do not participate in the provider binding.

The action envelope contains `schema:userio.messenger.v1`, `user_id`,
`account_ref`, `conversation_id`, `peer_id`, `channel`, `source_message_id`,
`source_provider_message_id`, `source_hash`, `source_binding`, `thread_id`,
`action`, `payload`, `media_hash`, `request_id`, `action_id`.
The exact whole-envelope digest is the approval `expected_hash`.

For sticker/file actions, `media_hash` is the digest of exactly
`{source_binding, attachment_id, attachment_index}` for the actual media source
in the same peer. `source_binding` uses the provider fields above,
`attachment_id` is the native photo/document/sticker ID as a string,
`attachment_index` is an integer. It contains no access hash, provider URL,
local display name or local timestamp. The provider re-reads this actual media
source and verifies its attachment identity before claiming the mutation.

New-file actions use the separately bounded `file_upload` capability and opaque
`upload_id`; they do not impersonate an existing media source. Their signed file
metadata/bytes SHA replaces the media-reference branch; `media_hash` is empty.
See [staging and provider packet](contracts/messenger-upload-v1.md).

## Telegram account sessions

All Telegram accounts use the shared UserIO GramJS connector and the same
account-owned session lifecycle. QR login, phone login, and `tg-convert`
session import save a GramJS session there; UserIO operations use that session
by the account's `telegram-qr:account-N` reference. Separate messenger HTTP
relay providers and the old exact-chat relay wrapper are no longer selected by
the runtime. Accounts with a missing or revoked session must be connected again
from the Telegram accounts page before live operations are available.

The old relay protocol was retired from runtime configuration; it is retained
in Git history only.

## First contact with a contact outside the history

`userio.contacts.resolve` and `userio.contacts.start` open a conversation with
a contact that has never written to the account. For Telegram this needs the
connector's own live session, so it can be impossible while the account itself
still reads and sends. The account's `capabilities` therefore carry a
`first_contact` block: `supported`, `allowed`, a machine-readable `reason` and
`availability` (`available`, `unavailable`, `unknown`). A failed resolve
answers with the same reason in `code`. A consumer must read that block before
it promises a first contact, and must not infer availability from
`provider_status`. The full contract is in
[first contact and first delivery](contracts/messenger-first-contact-v1.md).

## Draft status after approval

A draft that was sent stays on record, so the conversation keeps its history.
Its `status` is `approved`, which means the transport took the message and
never means "approved but not sent": `proposed` waits for an approval,
`sending` is claimed with an unknown outcome, `approved` was handed to the
transport, `rejected` was refused. Every draft projection carries `sent`, and
the pending list contains only drafts that still wait. Approving the same
draft again replays its stored receipt and sends nothing a second time; the
answer says `replay: true`.

## Separate native sender credential

The owner can issue a distinct app-specific Bearer using the existing normal
endpoint, authenticated with its existing protected owner credential:

```http
POST /v1/personal-tokens
Content-Type: application/json
```
```json
{"name":"Secretary messenger sender","scope":"messenger_actions","account_refs":["telegram:900000103","max"]}
```

The token is returned once and must go straight into the normal encrypted
Airlock resource, never chat/output. Native slug`userio_messenger` is separate
from read-only`userio`. The new scope permits only generic messenger MCP tools
and these explicit accounts; legacy APIs/resources, token management and other
accounts are denied. No private readonly token is upgraded/replaced/revoked.
MAX actions are limited by the native SDK to the explicitly authorized QA
conversation and exact confirmation during acceptance. Existing business
request135, other chats and historical sends are not canaries.

## Real acceptance

Current provider acceptance: Telegram/MAX reaction add/change/remove, native
sticker identity and new-file recipient bytes passed the shared approved-action
path. Telegram SDK document delivery also has independent native acceptance.
The [live matrix](../.planning/phases/14-telegram-and-max-rich-operations/14-rich-provider-VERIFICATION.md)
separates these results from typing command ACKs, unobserved recipient typing
and unfinished native Secretary MAX migration. Neither an advertised capability
nor an accepted command substitutes for the missing recipient observation.

After reviewed source, focused checks, publication, install and all intentional
restarts, run the same native Secretary journey through Telegram QA
`-1009000002001` and the authorized MAX test conversation: real source/media,
exact approved reply, durable receipt, same-ID repeated edit, then supported
rich actions. Capture provider/recipient and native SDK results before retirement.
Only the Exmanager owner changes its transport and SDK files. Retain native
memory, roles, business tasks, current identities, old receipts and rollback.


## Normal live ingress and generic consumer cursor

The authenticated service publisher uses `POST /v1/messages` on the existing
UserIO ingress (loopback18093 or public HTTPS). The existing service credential
resolves the registered account's ingress owner; personal sender credentials
cannot impersonate ingress or manage tokens.

```json
{
  "route_id": "telegram",
  "account_id": "telegram:900000103",
  "message": {
    "schema": "universal.inbox.message.v1",
    "source": "telegram",
    "message_id": "telegram:900000103|-1009000002001:123",
    "sender": "456",
    "peer_id": "-1009000002001",
    "conversation_kind": "group",
    "direction": "incoming",
    "body": "Exact untrimmed provider text",
    "received_at": 1791800000,
    "edited_at": 0,
    "provider_reply_to": "",
    "provider_thread_id": "",
    "reconciliation": false,
    "attachments": []
  }
}
```

Append actual media references in their native order with kind, attachment_id,
content_type, filename and size. Optional last `telegram_routing` attachment
contains author_id/name/first_name/last_name/username as JSON provider_ref, plus
boolean is_exact_account_mention and is_reply_to_account when actually verified
on the same connected account. These project as source.invocation.
All IDs remain decimal strings. Never insert provider tokens, access hashes or
private URLs. Do not insert ASR transcript into this raw-source ingress body;
read actual image/voice content separately via messenger.media. That read sends
its exact attachment_index to the provider. History and recovery use
reconciliation=true and do not create new business dispatch.

`messenger.events` is the account transport feed. Personal Secretary analysis
eligibility is carried as metadata; native Airlock authorization/roles still
apply before the SDK dispatches a source. It does not change personal analysis
preferences or automate approvals. Initial polling without after returns an
empty batch plus the current account head; subsequent explicit cursors deliver
only live incoming non-deleted, non-reconciled sources. Account filtering is
before LIMIT. The SDK persists the returned cursor and its native source dedup.

MAX uses the same source/action tools. Its existing client reads actual message
and bounded timestamp history pages without read ACK; tied native timestamps
are conservatively partial. File/photo sends reuse actual native references
from this conversation, without upload URLs/local paths. Media reads use actual
provider CDN bytes: bounded first-frame images, UTF-8 text files or the existing
Whisper service for audio. Unsupported formats return a readable status instead
of invented content. These source checks alone do not certify live delivery.

## Gmail/Himalaya: общий почтовый маршрут

Для уже подключённых аккаунтов Himalaya общий messenger предоставляет `capabilities`,
`resolve`, `history`, `events` и подтверждённый `reply`. Capability `history_mode=stored`
означает действующее зеркало Inbox, обновляемое обычным Gmail ingress; resolve не
читает письмо повторно с IMAP и возвращает `fresh=false, mode=stored, provider_read_acknowledged=false`.
`refresh=true` не поддерживается. Edit/react/sticker/file/typing здесь не объявлены.

`account_ref` — зарегистрированный UserIO ID, например `gmail-careviolan` или
`gmail-megamen932`; `peer_id` — настоящий адрес собеседника; `provider_message_id` —
нечисловой RFC Message-ID без угловых скобок (старый сохранённый UID также можно
читать, но для точного threaded reply требуется RFC Message-ID). `source.channel=gmail`
при этом внутренний источник `gmail`/`gmail:careviolan` и ID старого диалога сохраняются.
Входящий автор — адрес From, исходящий — адрес собственного настроенного аккаунта.

Новый mail ingress передаёт канонический ID аккаунта, настоящий peer, In-Reply-To
и thread reference. `events` без `after` инициализирует текущий head без выдачи истории;
старые alias-bound события также учитываются в head, поэтому уточнение account_ref
не превращает старые письма в новые обращения. Recovery/outgoing не dispatch.

Обычный `propose(reply,{text},request_id)` → `approve(id,hash,true)` фиксирует источник,
адресата и точную тему. Существующий SMTP/Himalaya отправляет UTF-8 MIME с новым
детерминированным Message-ID и In-Reply-To/References исходного письма. Receipt
`delivery_status=smtp_accepted` означает принятие SMTP; доставку адресату проверяет
реальный consumer. UNKNOWN сохраняет `sending`, не даёт повторить отправку.

Отправленное письмо теперь копируется в папку Sent аккаунта (IMAP APPEND того же MIME),
поэтому в самом ящике есть доказательство отправки, а не только в receipt. Receipt стал
`himalaya:<account>:<draft_id>:<message_id>:<sent_copy>`: `message_id` — RFC Message-ID,
принятый провайдером, `sent_copy` — `saved` либо причина, по которой копия не
сохранилась. Ответ об отправке различает «передано транспорту» и «доставлено»:
`delivery_status=smtp_accepted` даёт `sent=false` и `delivery_unconfirmed=true`,
`delivered` — только вместе с подтверждением провайдера. Подробности — в отдельном
контракте [first contact and first delivery](contracts/messenger-first-contact-v1.md).

Новый отдельный personal token с `scope=messenger_actions` может перечислять собственные
reply-capable Gmail IDs в `account_refs`. Старые readonly токены остаются readonly;
управление токенами/чужие аккаунты не входят в scope. Значения передаются только через
защищённый ресурс приложения.

Для нового человеческого QA-вопроса между уже существующими почтовыми диалогами
используется обычный UserIO `POST /v1/conversations/{id}/send` с `{body,request_id}`
и полным собственным owner credential. Это новая отправка с новым Message-ID,
а не пересылка старой операции. Дальнейший ответ Секретаря идёт через generic approval
по точному новому входящему письму. SMTP напрямую для обхода UserIO не нужен.


## Personal VK reactions

Personal `vk:<actual-user-id>` accounts use the existing user-owned connector
through the same `capabilities → resolve → propose(react) → approve → get` path.
A new `messenger_actions` credential may explicitly name that account; existing
readonly/other-account credentials are unchanged. No new VK login or client
session is created. Generic reply/edit/media/typing remain unsupported until
implemented; the normal VK web send/edit/upload paths continue independently.

`reaction_options` lists ❤️, 🔥, 😂, 👍. `payload={"emoji":""}` removes only
this sender's reaction. VKCOM native catalog IDs 1–4 were verified against the
current provider artwork. Before adding/changing, the provider checks the actual
catalog still permits the ID. Other emoji are refused before submission.
Reactions use the freshly read native `conversation_message_id` with the exact
peer; a global `message_id` is never substituted for cmid. Source/author/body/edit
binding and credential identity are revalidated before dispatch. `resolve`
returns native `reaction_info.own_reaction_id` plus counters without marking read.

An explicit refusal before mutation returns `provider_not_submitted` and
`action_status=proposed`. Accepted actions are deduplicated. Uncertain transport
or malformed mutation receipts remain `sending`; do not invent a new request ID.

Primary protocol: [VKCOM messages schema](https://github.com/VKCOM/vk-api-schema/blob/master/messages/methods.json)
(`messages.sendReaction`, `messages.deleteReaction`, `messages.getReactionsAssets`).
Telegram uses [messages.sendReaction](https://core.telegram.org/method/messages.sendReaction),
including an empty reaction vector for removal. Secretary policy/automatic use
belongs to its SDK consumer; provider support alone is not native consumer proof.


Live13October reaction acceptance: fresh Telegram881 and MAX498 lifecycles,
and personal VK200000000106 nativecmid1 👍→🔥→removed. All original baselines
restored. Before each distinct mutation, resolve the exact current source;
an edited_at change makes an older approval snapshot stale even when visible
text is unchanged. Never replay an UNKNOWN action or replace its request ID.
The initial known-unsubmitted stale Telegram proposals remain preserved;
fresh source-bound proposals passed independently. Native Secretary policy
acceptance remains with the Exmanager owner; no mutual source/runtime hold.
