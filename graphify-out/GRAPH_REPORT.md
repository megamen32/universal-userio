# Graph Report - universal-userio  (2026-09-14)

## Corpus Check
- 185 files · ~423,973 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 5888 nodes · 19991 edges · 234 communities (209 shown, 25 thin omitted)
- Extraction: 80% EXTRACTED · 20% INFERRED · 0% AMBIGUOUS · INFERRED: 4014 edges (avg confidence: 0.57)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `397e38f3`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- adapters.py
- index-CxLUMw7i.js
- index-DuKNzKr_.js
- index-7AoOQ2cN.js
- index-dPl1AlHM.js
- index-DWodf9JG.js
- SQLiteUserIOStore
- index-BZa--jkH.js
- index-BDHIfE0-.js
- index-D7SnVdOK.js
- wd
- index-FQlC4kKN.js
- i
- index-DSPT1BVB.js
- UserIOMcpSurface
- j
- EmailChannel
- http_api.py
- gmail_ingress.py
- i
- TelegramIdentityPort
- i
- n
- i
- gu
- ReplyDraft
- jc
- InboxMessage
- i
- Yi
- so
- i
- Xi
- n
- hu
- i
- l
- ChatPort
- n
- mc
- s
- TelegramAPI
- n
- i
- UserIOService
- LiveTelegramOutbox
- FakeClient
- jc
- Yi
- wd
- l
- gu
- xc
- n
- xc
- l
- hu
- l
- bridge.js
- server.mjs
- xc
- hu
- xc
- runtime.py
- WhatsAppBridgeClient
- hu
- l
- AdapterNotSupported
- hu
- xc
- gu
- App.tsx
- wd
- l
- UnifiedChannels
- l
- xc
- Omnichannel
- n
- l
- xc
- index-ChlMeMcN.js
- gu
- hu
- ko
- l
- gu
- gu
- i
- gu
- bridge_helpers.js
- telegram_env_config
- compilerOptions
- vi
- hu
- manifest.json
- up
- il
- Ji
- md
- il
- il
- agentcall_ingress.py
- t
- up
- i
- css
- cn
- dependencies
- compilerOptions
- il
- get
- ko
- ChatGPTWebChannelAdapter
- package.json
- md
- il
- devDependencies
- background.js
- keywords
- vault.js
- Спека: универсальные канальные адаптеры userio
- get
- Ji
- agent.js
- test_oauth.py
- ko
- sl
- Universal UserIO
- AndroidSmsGatewayClient
- fd
- pu
- lc
- startSocket
- theme-provider.tsx
- Rc
- ChatSummary
- draft-browser-notifications.ts
- debounceAgentDeliver
- ko
- vault.py
- service.py
- startQr
- get
- il
- transcription.mjs
- cdp_inject_vk.cjs
- W
- popup.js
- cdp_probe.cjs
- package.json
- va
- pu
- matrix_ingress.py
- c
- test_search.py
- server.ts
- FakeDB
- group-routing.mjs
- envelope
- chat-view.js
- CDP
- vk_extension_gateway.mjs
- scripts
- byok-runs-ui.js
- allowlist.js
- collect.py
- UX fixes round 1 — Messenger
- record_and_send
- cdp_diag.cjs
- cdp_path.cjs
- package.json
- tsconfig.json
- db.js
- cdp_listen.cjs
- cdp_vk.cjs
- byok-ui.js
- byok-runs-ui.d.ts
- inferMediaType
- eslint-plugin-react-hooks
- build.sh
- prettier
- prettier-plugin-tailwindcss
- __init__.py
- tw-animate-css
- @types/react-dom
- typescript
- universal-userio
- VK Inbox — full feature + BrowserOS install
- VK Inbox sidecar — install
- UserIOIngressClient
- Zd
- Sink
- ChatGPT CDP starter — установка (macOS)
- pu
- Sink
- Epic: real attachment download per channel
- UI fix: топ-5 фиксов из ревью Марата
- VK Inbox → универсальный агент сбора данных (universal collect)
- Universal UserIO Agent extension (v0.3)
- Android SMS adapter
- WhatsApp bridge (pinned copy)
- React + TypeScript + Vite + shadcn/ui
- ChatGPT CDP adapter
- Dashboard login
- Telegram QR connector (`userio-telegram-qr.service`)
- browser-approve-400.md
- gmail-himalaya-reply.md
- universal-userio-mvp.md
- INSTALL.md
- adapter-sdk.md
- SKILL.md
- up
- ri
- et
- ChannelAdapter
- mc
- get
- pu
- agent_channel.py
- pu
- va
- UserIO contact and file operations

## God Nodes (most connected - your core abstractions)
1. `SQLiteUserIOStore` - 242 edges
2. `UserIOService` - 168 edges
3. `s()` - 159 edges
4. `InboxMessage` - 134 edges
5. `i()` - 120 edges
6. `so()` - 109 edges
7. `i()` - 84 edges
8. `i()` - 84 edges
9. `i()` - 82 edges
10. `i()` - 82 edges

## Surprising Connections (you probably didn't know these)
- `test_ambiguous_or_unknown_explicit_connector_account_fails_closed()` --calls--> `SQLiteUserIOStore`  [INFERRED]
  tests/test_multi_user.py → src/universal_userio/store.py
- `test_chat_message_requires_chat_and_id()` --calls--> `ChatMessage`  [INFERRED]
  tests/test_channels_core.py → src/userio_adapter_sdk/__init__.py
- `test_downloaded_media_aliases()` --calls--> `DownloadedMedia`  [INFERRED]
  tests/test_channels_core.py → src/userio_adapter_sdk/__init__.py
- `test_chat_port_runtime_check_on_full_implementation()` --indirect_call--> `ChatPort`  [INFERRED]
  tests/test_channels_core.py → src/userio_adapter_sdk/__init__.py
- `App()` --references--> `event`  [EXTRACTED]
  web/universal-userio-web/src/App.tsx → deploy/telegram-qr-connector/agent-deliver.test.mjs

## Import Cycles
- 1-file cycle: `src/universal_userio/channels/email.py -> src/universal_userio/channels/email.py`

## Communities (234 total, 25 thin omitted)

### Community 0 - "adapters.py"
Cohesion: 0.05
Nodes (42): AndroidSmsChannelAdapter, ByokBridgeGenerator, _chatgpt_accounts(), _chatgpt_bearer(), _chatgpt_client(), _chatgpt_conversation(), _chatgpt_last_message(), _chatgpt_raw() (+34 more)

### Community 1 - "index-CxLUMw7i.js"
Cohesion: 0.06
Nodes (65): al(), Ba(), bl(), bn(), bo(), Cl(), da(), dd() (+57 more)

### Community 2 - "index-DuKNzKr_.js"
Cohesion: 0.05
Nodes (77): al(), Ba(), bl(), bn(), bo(), Cl(), da(), dd() (+69 more)

### Community 3 - "index-7AoOQ2cN.js"
Cohesion: 0.04
Nodes (80): ad(), ar(), bn(), bo(), br(), bs(), bu(), Cn() (+72 more)

### Community 4 - "index-dPl1AlHM.js"
Cohesion: 0.04
Nodes (84): ad(), ar(), bn(), bo(), br(), bs(), bu(), Cn() (+76 more)

### Community 5 - "index-DWodf9JG.js"
Cohesion: 0.06
Nodes (64): an(), ap(), ar(), bn(), br(), bt(), bu(), cn() (+56 more)

### Community 6 - "SQLiteUserIOStore"
Cohesion: 0.04
Nodes (13): Path, Record that the browser actually displayed these proposed drafts., List proposed drafts with just enough conversation context for operator notifica, Pin (or clear) which connected account owns and replies in this chat., Return the user's own AI endpoint/model/token, or None for server default., For each conversation_id return the newest non-placeholder body, or ''., Return a configured source account's reply capability, when it exists., Create (or return the existing) empty conversation for source+sender.          L (+5 more)

### Community 7 - "index-BZa--jkH.js"
Cohesion: 0.04
Nodes (75): ad(), ap(), ar(), bn(), bo(), br(), bs(), bu() (+67 more)

### Community 8 - "index-BDHIfE0-.js"
Cohesion: 0.06
Nodes (63): ad(), an(), ar(), bn(), br(), bu(), cn(), cr() (+55 more)

### Community 9 - "index-D7SnVdOK.js"
Cohesion: 0.05
Nodes (64): ad(), Ba(), bo(), br(), bs(), bt(), Dn(), dr() (+56 more)

### Community 10 - "wd"
Cohesion: 0.10
Nodes (29): ai(), ap(), as(), at(), bt(), Do(), dp(), Fu() (+21 more)

### Community 11 - "index-FQlC4kKN.js"
Cohesion: 0.07
Nodes (44): an(), ar(), br(), bu(), cn(), cr(), dn(), dr() (+36 more)

### Community 12 - "i"
Cohesion: 0.11
Nodes (50): aa(), ae(), b(), bd(), be(), c(), ce(), cp() (+42 more)

### Community 13 - "index-DSPT1BVB.js"
Cohesion: 0.05
Nodes (73): al(), an(), ar(), Ba(), bl(), bn(), br(), bu() (+65 more)

### Community 14 - "UserIOMcpSurface"
Cohesion: 0.07
Nodes (36): ConversationPolicy, Stable boundaries between canonical ingress, AI, and the durable Outbox., UserPrincipal, Universal UserIO business conversation control plane., main(), Any, Authenticated MCP surface for user-scoped UserIO operations., _schema() (+28 more)

### Community 15 - "j"
Cohesion: 0.06
Nodes (67): aa(), ac(), af(), Ai(), ao(), bc(), C(), cc() (+59 more)

### Community 16 - "EmailChannel"
Cohesion: 0.06
Nodes (32): email_env_config(), EmailChannel, EmailEnvConfig, _parse_email_message(), _peer(), Any, ChatRef, MessageRef (+24 more)

### Community 17 - "http_api.py"
Cohesion: 0.07
Nodes (21): Backward-compatible re-export of the stable adapter SDK.  New adapters should im, _append_gmail_account(), _append_himalaya_account(), _connect_gmail_account(), Small authenticated HTTP boundary for the UserIO business flow., Verify and install one Gmail IMAP account without exposing its secret., OAuthError, OAuthProvider (+13 more)

### Community 18 - "gmail_ingress.py"
Cohesion: 0.24
Nodes (9): GmailMessage, HimalayaReader, Any, Gmail/Himalaya provider reader for UserIO-compatible runtimes.  Polls allowliste, _source(), accounts_from_file(), main(), Path (+1 more)

### Community 19 - "i"
Cohesion: 0.11
Nodes (57): ae(), b(), Ba(), bd(), c(), ce(), cp(), cs() (+49 more)

### Community 20 - "TelegramIdentityPort"
Cohesion: 0.05
Nodes (41): BaseModel, Enum, InputPeerUser, can_message(), MembershipResult, MembershipStatus, _normalize_username(), Any (+33 more)

### Community 21 - "i"
Cohesion: 0.11
Nodes (57): ae(), b(), Ba(), bd(), c(), ce(), cp(), cs() (+49 more)

### Community 22 - "n"
Cohesion: 0.12
Nodes (46): ae(), at(), b(), bd(), be(), Bi(), c(), ce() (+38 more)

### Community 23 - "i"
Cohesion: 0.10
Nodes (45): ac(), ao(), ea(), Eo(), ff(), G(), gc(), ge() (+37 more)

### Community 24 - "gu"
Cohesion: 0.06
Nodes (63): ai(), ap(), as(), Au(), Ca(), cd(), ct(), Cu() (+55 more)

### Community 25 - "ReplyDraft"
Cohesion: 0.09
Nodes (14): ReplyDraft, format_draft_approval_notice(), _preview(), Any, System notifications for reply drafts that still need owner approval., Send a system alert to the owner's Telegram DM without creating another draft., TelegramDraftApprovalNotifier, ValueError (+6 more)

### Community 26 - "jc"
Cohesion: 0.12
Nodes (37): a(), aa(), ac(), ci(), d(), di(), _e(), ea() (+29 more)

### Community 27 - "InboxMessage"
Cohesion: 0.08
Nodes (36): inbox_message_from_envelope(), OpenAICompatibleDraftGenerator, Any, AI adapter boundary. It receives business conversation context, never provider s, InboxMessage, DeliveryUnavailableError, The configured source intentionally has no outbound delivery capability., test_duplicate_variants_are_not_repeated() (+28 more)

### Community 28 - "i"
Cohesion: 0.11
Nodes (53): a(), aa(), ac(), ai(), ao(), ci(), d(), di() (+45 more)

### Community 29 - "Yi"
Cohesion: 0.11
Nodes (49): eo(), a(), ac(), ao(), Bi(), ce(), ci(), _e() (+41 more)

### Community 30 - "so"
Cohesion: 0.09
Nodes (51): a(), ac(), at(), bc(), bt(), cc(), cf(), ci() (+43 more)

### Community 31 - "i"
Cohesion: 0.14
Nodes (35): a(), ai(), bc(), d(), di(), Eo(), ep(), gc() (+27 more)

### Community 32 - "Xi"
Cohesion: 0.14
Nodes (36): ac(), ao(), Bi(), ce(), ci(), co(), dc(), _e() (+28 more)

### Community 33 - "n"
Cohesion: 0.12
Nodes (51): a(), aa(), ae(), b(), bd(), be(), Bi(), c() (+43 more)

### Community 34 - "hu"
Cohesion: 0.07
Nodes (49): al(), at(), bl(), Cl(), ct(), da(), dd(), dl() (+41 more)

### Community 35 - "i"
Cohesion: 0.11
Nodes (50): aa(), ae(), b(), bd(), be(), c(), cp(), cs() (+42 more)

### Community 36 - "l"
Cohesion: 0.30
Nodes (12): df(), ef(), get(), gf(), If(), jf(), kf(), Lf() (+4 more)

### Community 37 - "ChatPort"
Cohesion: 0.12
Nodes (9): AbstractAsyncContextManager, ChatPort, DownloadedMedia, ChatRef, ContactRef, MessageRef, Protocol, Bytes downloaded for one message's media attachment. (+1 more)

### Community 38 - "n"
Cohesion: 0.13
Nodes (42): ae(), at(), b(), bd(), be(), Bi(), c(), ce() (+34 more)

### Community 39 - "mc"
Cohesion: 0.10
Nodes (46): ac(), ao(), Bi(), cc(), cf(), ci(), dc(), Do() (+38 more)

### Community 40 - "s"
Cohesion: 0.13
Nodes (48): a(), ae(), b(), bd(), be(), cd(), Ce(), cp() (+40 more)

### Community 41 - "TelegramAPI"
Cohesion: 0.10
Nodes (22): Any, ChatRef, ContactRef, List dialogs as provider-neutral chat summaries., Read recent messages from ``chat`` as immutable DTOs., Acknowledge all currently visible messages in a chat., Forward one message while keeping source and destination explicit., Download message media into an immutable byte DTO. (+14 more)

### Community 42 - "n"
Cohesion: 0.13
Nodes (49): a(), aa(), ae(), b(), bd(), be(), Bi(), c() (+41 more)

### Community 43 - "i"
Cohesion: 0.09
Nodes (55): ac(), af(), ao(), co(), dc(), ea(), Eo(), fc() (+47 more)

### Community 44 - "UserIOService"
Cohesion: 0.14
Nodes (35): BaseHTTPRequestHandler, handler(), Deliver through the user's real browser when the server POST is blocked., UserIOService, Generator, Outbox, When the latest message is an attachment placeholder, the chat list and     sear, An older conversation that just received a fresh message must rank above     a c (+27 more)

### Community 45 - "LiveTelegramOutbox"
Cohesion: 0.08
Nodes (23): live_telegram_outbox_from_env(), LiveTelegramOutbox, Live Telegram delivery for the sync UserIO service (spec Phase 2).  Enabled with, Deliver approved drafts through an in-process Telegram channel adapter., Build the live outbox when ``USERIO_LIVE_TELEGRAM`` enables it.      The Telegra, Any, Run async channel adapters from the sync UserIO service.  One background thread, Execute coroutine factories on a private background event loop. (+15 more)

### Community 46 - "FakeClient"
Cohesion: 0.09
Nodes (25): _cli(), create_independent_session_via_qr(), login_via_qr(), _new_client(), Any, Path, QrLoginResult, Telegram session provisioning via QR login — two proven originals.  1. Interacti (+17 more)

### Community 47 - "jc"
Cohesion: 0.09
Nodes (51): a(), ac(), at(), bc(), bt(), cc(), cf(), ci() (+43 more)

### Community 48 - "Yi"
Cohesion: 0.16
Nodes (28): ai(), ea(), Eo(), ff(), gc(), hc(), i(), ic() (+20 more)

### Community 49 - "wd"
Cohesion: 0.07
Nodes (46): an(), ar(), br(), cn(), cr(), dn(), dr(), Ed() (+38 more)

### Community 50 - "l"
Cohesion: 0.11
Nodes (30): df(), ef(), get(), gf(), Hf(), hl(), If(), jf() (+22 more)

### Community 51 - "gu"
Cohesion: 0.10
Nodes (39): Au(), cd(), Cu(), Dr(), Du(), Eu(), gu(), hu() (+31 more)

### Community 52 - "xc"
Cohesion: 0.07
Nodes (50): af(), as(), at(), Ba(), cc(), cf(), da(), dd() (+42 more)

### Community 53 - "n"
Cohesion: 0.16
Nodes (40): a(), ae(), al(), at(), b(), bd(), bt(), c() (+32 more)

### Community 54 - "xc"
Cohesion: 0.08
Nodes (34): ai(), ap(), as(), at(), bc(), bt(), cf(), dp() (+26 more)

### Community 55 - "l"
Cohesion: 0.09
Nodes (38): al(), bl(), Cl(), dl(), Dr(), El(), fl(), Hf() (+30 more)

### Community 56 - "hu"
Cohesion: 0.15
Nodes (26): ad(), Au(), Ca(), cd(), Cu(), De(), ei(), Eu() (+18 more)

### Community 57 - "l"
Cohesion: 0.09
Nodes (38): al(), bl(), Cl(), dl(), Dr(), El(), fl(), Hf() (+30 more)

### Community 58 - "bridge.js"
Cohesion: 0.06
Nodes (30): _ACCEPTED_HOST_VALUES, ALLOWED_USERS, app, args, CHUNK_DELAY_MS, enqueueSend(), buildLocationPayload(), buildPollPayload() (+22 more)

### Community 59 - "server.mjs"
Cohesion: 0.06
Nodes (31): agentDeliverCallbackSecret, agentDeliverChatId, agentDeliverChats, agentDeliverCwd, agentDeliverDebounce, agentDeliverDebounceMs, agentDeliverHermesSessionId, agentDeliverIgnoredChats (+23 more)

### Community 60 - "xc"
Cohesion: 0.10
Nodes (30): af(), bc(), cc(), cf(), co(), dc(), Du(), ea() (+22 more)

### Community 61 - "hu"
Cohesion: 0.11
Nodes (35): ad(), Au(), Ca(), cd(), Cu(), De(), Dt(), ei() (+27 more)

### Community 62 - "xc"
Cohesion: 0.08
Nodes (58): so(), a(), ac(), af(), ao(), bc(), Bi(), cc() (+50 more)

### Community 63 - "runtime.py"
Cohesion: 0.09
Nodes (25): DirectProviderOutbox, HimalayaGmailOutbox, Production fallback: only provider-owned transports may deliver human replies., Deliver approved Telegram drafts through the telegram-qr connector.      The con, Send one explicitly approved Gmail reply through the configured Himalaya SMTP ac, TelegramQrHttpOutbox, build_service(), main() (+17 more)

### Community 64 - "WhatsAppBridgeClient"
Cohesion: 0.07
Nodes (27): _extract_text(), normalize_chat_id(), Any, ChatRef, MessageRef, WhatsApp channel adapter over the Baileys HTTP bridge (spec Phase 4).  The bridg, Universal WhatsApp channel backed by the HTTP bridge., Drain the bridge queue into the local inbox buffer. (+19 more)

### Community 65 - "hu"
Cohesion: 0.10
Nodes (40): ad(), as(), Au(), Ca(), cd(), Cu(), De(), Dt() (+32 more)

### Community 66 - "l"
Cohesion: 0.11
Nodes (28): cs(), ds(), es(), Fo(), i(), is(), ja(), jo() (+20 more)

### Community 67 - "AdapterNotSupported"
Cohesion: 0.07
Nodes (31): Row, Sender, AndroidSmsChannel, _peer(), ChatRef, MessageRef, SMS channel adapter over the Android SMS Gateway (spec Phase 5).  Wraps the low-, Universal SMS channel backed by one Android SMS Gateway instance. (+23 more)

### Community 68 - "hu"
Cohesion: 0.08
Nodes (55): ai(), ap(), as(), Au(), Ca(), cd(), ct(), Cu() (+47 more)

### Community 69 - "xc"
Cohesion: 0.09
Nodes (35): af(), ai(), as(), bc(), cc(), cf(), Du(), ec() (+27 more)

### Community 70 - "gu"
Cohesion: 0.12
Nodes (33): ad(), Au(), Ca(), cd(), Cu(), De(), Dt(), ei() (+25 more)

### Community 71 - "App.tsx"
Cohesion: 0.12
Nodes (31): Account, accountHealth(), api(), App(), AVATAR_COLORS, avatarColor(), channelIcon(), Chat (+23 more)

### Community 72 - "wd"
Cohesion: 0.16
Nodes (21): WhatsAppChannelAdapter, Generator, NoRedirect, Outbox, HTTPRedirectHandler, MailChannelAdapter now routes download() through a real email channel.     The f, TelegramChannelAdapter POSTs {chat, chat_id, message_id} to the bridge     and t, WhatsApp bridge contract mirrors Telegram: POST /download with the     same JSON (+13 more)

### Community 73 - "l"
Cohesion: 0.10
Nodes (31): df(), ef(), get(), gf(), Hf(), hl(), If(), jf() (+23 more)

### Community 74 - "UnifiedChannels"
Cohesion: 0.11
Nodes (17): Resolve all current provider wrappers behind one four-method interface., UnifiedChannels, Gateway, Generator, _locked_service(), Outbox, test_android_sms_syncs_to_userio_and_approved_draft_uses_gateway(), test_sms_auto_send_still_works_without_the_manual_lock() (+9 more)

### Community 75 - "l"
Cohesion: 0.10
Nodes (31): df(), ef(), get(), gf(), Hf(), hl(), If(), jf() (+23 more)

### Community 76 - "xc"
Cohesion: 0.09
Nodes (36): af(), ai(), as(), at(), bc(), cc(), cf(), co() (+28 more)

### Community 77 - "Omnichannel"
Cohesion: 0.08
Nodes (30): Upload bytes to a chat and return the resulting message DTO., Send a text message and return its neutral DTO., Send a local sticker file (.webp static) to the chat., AdapterCapabilities, Channel, ChatInvalidPeerError, ChatOperationError, ChatPermissionError (+22 more)

### Community 78 - "n"
Cohesion: 0.07
Nodes (50): aa(), be(), bo(), cs(), ct(), ds(), es(), Fo() (+42 more)

### Community 79 - "l"
Cohesion: 0.11
Nodes (30): ae(), b(), c(), Hf(), hl(), l(), ll(), ml() (+22 more)

### Community 80 - "xc"
Cohesion: 0.10
Nodes (29): af(), bc(), cc(), cf(), ci(), co(), dc(), Du() (+21 more)

### Community 81 - "index-ChlMeMcN.js"
Cohesion: 0.06
Nodes (63): an(), ar(), Ba(), bn(), bo(), br(), bs(), bu() (+55 more)

### Community 82 - "gu"
Cohesion: 0.15
Nodes (28): ad(), Au(), Ca(), cd(), Cu(), De(), ei(), Eu() (+20 more)

### Community 83 - "hu"
Cohesion: 0.13
Nodes (21): ds(), es(), Fo(), fs(), hs(), is(), jo(), ko() (+13 more)

### Community 84 - "ko"
Cohesion: 0.10
Nodes (28): bo(), cs(), ds(), es(), Fo(), fs(), Ho(), hs() (+20 more)

### Community 85 - "l"
Cohesion: 0.13
Nodes (25): df(), ef(), get(), gf(), Hf(), hl(), If(), jf() (+17 more)

### Community 86 - "gu"
Cohesion: 0.15
Nodes (26): ad(), Au(), bd(), Ca(), cd(), Cu(), De(), ei() (+18 more)

### Community 87 - "gu"
Cohesion: 0.20
Nodes (21): Au(), Ca(), cd(), Cu(), ei(), Eu(), gu(), id() (+13 more)

### Community 88 - "i"
Cohesion: 0.11
Nodes (32): Co(), ea(), fd(), gi(), hs(), i(), jd(), Jt() (+24 more)

### Community 89 - "gu"
Cohesion: 0.14
Nodes (26): as(), Au(), Ca(), Cu(), Do(), Eu(), Fu(), gi() (+18 more)

### Community 90 - "bridge_helpers.js"
Cohesion: 0.15
Nodes (23): appendMediaFailureNote(), createBoundedMessageStore(), createReconnectScheduler(), createVersionResolver(), extractBridgeEvent(), formatContactsText(), formatContactText(), formatLocationText() (+15 more)

### Community 91 - "telegram_env_config"
Cohesion: 0.10
Nodes (15): _proxy_spec(), Convert a proxy URL (optionally with userinfo) into a Telethon proxy dict., Resolve Telegram settings from ``USERIO_TELEGRAM_*`` with ``TG_*`` fallbacks., Build an adapter from ``USERIO_TELEGRAM_*``/``TG_*`` environment settings., telegram_env_config(), TelegramClient, Unit tests for the Telegram channel adapter (no network access)., Minimal stand-in for a Telethon client instance. (+7 more)

### Community 92 - "compilerOptions"
Cohesion: 0.08
Nodes (24): DOM, src, vite/client, compilerOptions, allowImportingTsExtensions, erasableSyntaxOnly, jsx, lib (+16 more)

### Community 93 - "vi"
Cohesion: 0.29
Nodes (8): Du(), ec(), Ee(), Nc(), Ua(), vi(), wu(), Zs()

### Community 94 - "hu"
Cohesion: 0.20
Nodes (14): as(), bs(), dd(), Do(), Fu(), Iu(), ms(), mu() (+6 more)

### Community 95 - "manifest.json"
Cohesion: 0.08
Nodes (23): action, default_popup, default_title, background, service_worker, content_scripts, description, host_permissions (+15 more)

### Community 96 - "up"
Cohesion: 0.12
Nodes (24): bu(), en(), fd(), Ft(), Gt(), Ht(), ii(), Kt() (+16 more)

### Community 97 - "il"
Cohesion: 0.17
Nodes (19): al(), bl(), Cl(), dl(), El(), fl(), il(), kr() (+11 more)

### Community 98 - "Ji"
Cohesion: 0.23
Nodes (19): ao(), be(), Bi(), _e(), Ei(), G(), io(), Ji() (+11 more)

### Community 99 - "md"
Cohesion: 0.07
Nodes (41): an(), ar(), br(), bu(), cn(), cr(), d(), dn() (+33 more)

### Community 100 - "il"
Cohesion: 0.11
Nodes (26): al(), bl(), Cl(), dl(), El(), fl(), il(), ja() (+18 more)

### Community 101 - "il"
Cohesion: 0.13
Nodes (24): al(), bl(), Cl(), dl(), El(), fl(), hu(), il() (+16 more)

### Community 102 - "agentcall_ingress.py"
Cohesion: 0.18
Nodes (12): AgentCallEventProjector, AgentCallRpcSubscriber, main(), Any, Project safe AgentCall lifecycle events into UserIO's ``phone`` channel., Read the gateway's newline-delimited, redacted event subscription., Convert redacted AgentCall RPC events into canonical inbox envelopes., run_forever() (+4 more)

### Community 103 - "t"
Cohesion: 0.08
Nodes (38): ai(), as(), cs(), ct(), dd(), Ee(), es(), fa() (+30 more)

### Community 104 - "up"
Cohesion: 0.16
Nodes (14): ap(), bt(), bu(), dp(), ip(), kp(), lp(), op() (+6 more)

### Community 105 - "i"
Cohesion: 0.18
Nodes (19): ds(), es(), Fo(), i(), jo(), ko(), Lo(), Mo() (+11 more)

### Community 106 - "css"
Cohesion: 0.09
Nodes (21): aliases, components, hooks, lib, ui, utils, iconLibrary, menuAccent (+13 more)

### Community 107 - "cn"
Cohesion: 0.18
Nodes (15): Avatar(), AvatarBadge(), AvatarFallback(), AvatarGroup(), AvatarGroupCount(), AvatarImage(), Badge(), badgeVariants (+7 more)

### Community 108 - "dependencies"
Cohesion: 0.10
Nodes (21): class-variance-authority, clsx, @fontsource-variable/geist, lucide-react, radix-ui, react-dom, shadcn, tailwind-merge (+13 more)

### Community 109 - "compilerOptions"
Cohesion: 0.10
Nodes (20): node, vite.config.ts, compilerOptions, allowImportingTsExtensions, erasableSyntaxOnly, lib, module, moduleDetection (+12 more)

### Community 110 - "il"
Cohesion: 0.06
Nodes (66): al(), Ba(), bl(), bn(), cc(), ci(), Cl(), dl() (+58 more)

### Community 111 - "get"
Cohesion: 0.18
Nodes (21): af(), df(), ef(), get(), gf(), Ht(), If(), jf() (+13 more)

### Community 112 - "ko"
Cohesion: 0.18
Nodes (20): af(), co(), df(), ef(), get(), gf(), Ht(), If() (+12 more)

### Community 113 - "ChatGPTWebChannelAdapter"
Cohesion: 0.13
Nodes (15): ChatGPTWebChannelAdapter, Read ChatGPT chats headlessly for every registered account.      Account session, DummyService, FakeClient, FakeResponse, make_adapter(), Tests for the headless cookie-based ChatGPT web channel adapter., Scripted curl_cffi stand-in: maps URL fragments to responses. (+7 more)

### Community 114 - "package.json"
Cohesion: 0.10
Nodes (19): dependencies, express, pino, qrcode-terminal, @whiskeysockets/baileys, description, name, overrides (+11 more)

### Community 115 - "md"
Cohesion: 0.12
Nodes (25): cn(), da(), dd(), fa(), fd(), Ft(), Gt(), Ht() (+17 more)

### Community 116 - "il"
Cohesion: 0.14
Nodes (20): bo(), ds(), es(), Fo(), Ho(), jo(), ko(), Lo() (+12 more)

### Community 117 - "devDependencies"
Cohesion: 0.11
Nodes (19): eslint, @eslint/js, eslint-plugin-react-refresh, globals, @types/node, @types/react, typescript-eslint, vite (+11 more)

### Community 118 - "background.js"
Cohesion: 0.16
Nodes (11): composeAndApprove(), downloadAttachmentsInBackground(), flushQueue(), FORWARD_QUEUE, onCapture(), SEEN, sendViaVK(), installObserver() (+3 more)

### Community 119 - "keywords"
Cohesion: 0.11
Nodes (18): author, name, url, description, keywords, license, name, repository (+10 more)

### Community 120 - "vault.js"
Cohesion: 0.22
Nodes (14): backendName(), cookieUrl(), decode(), del(), deriveKey(), encode(), list(), load() (+6 more)

### Community 121 - "Спека: универсальные канальные адаптеры userio"
Cohesion: 0.11
Nodes (17): 1. Цель и результат, 2. Минимальный путь, 3. Что уже есть (не изобретать), 4.1 Ядро библиотеки, 4.2 Сервис userio на тех же адаптерах, 4.3 Конфигурация и секреты, 4. Архитектура (маршрут «библиотека + сервис»), 5.1 Telegram — `channels/telegram.py` (первый, Ф1) (+9 more)

### Community 122 - "get"
Cohesion: 0.18
Nodes (20): af(), co(), df(), ef(), get(), gf(), Ht(), If() (+12 more)

### Community 123 - "Ji"
Cohesion: 0.18
Nodes (23): ao(), be(), Bi(), _e(), Ei(), En(), G(), io() (+15 more)

### Community 124 - "agent.js"
Cohesion: 0.26
Nodes (15): cmdEval(), cmdGetAttachment(), cmdGptSend(), cmdGptSendLegacyUnused(), cmdGptSendUi(), cmdNavigate(), cmdSend(), cmdVkAttachmentBytes() (+7 more)

### Community 125 - "test_oauth.py"
Cohesion: 0.20
Nodes (11): HTMLParser, Generator, Inputs, _json(), NoRedirect, Outbox, _pkce(), HTTPRedirectHandler (+3 more)

### Community 126 - "ko"
Cohesion: 0.10
Nodes (28): bo(), cs(), ds(), es(), Fo(), fs(), Ho(), hs() (+20 more)

### Community 127 - "sl"
Cohesion: 0.18
Nodes (17): Ba(), bl(), Cl(), dl(), El(), fl(), Il(), Ja() (+9 more)

### Community 128 - "Universal UserIO"
Cohesion: 0.11
Nodes (17): Accounts and browser workers, Agent Plugin 1.0, AI boundary, Android SMS Gateway adapter, Boundaries, ChatGPT CDP adapter, MCP first, Minimal API (+9 more)

### Community 129 - "AndroidSmsGatewayClient"
Cohesion: 0.24
Nodes (8): AndroidSmsGatewayClient, Any, Low-level Android SMS Gateway client with no UserIO runtime dependencies., SmsInboundMessage, main(), poll_once(), UserIOIngressClient, Continuous Android SMS ingress owned by Universal UserIO.

### Community 130 - "fd"
Cohesion: 0.19
Nodes (17): cn(), F(), fd(), Ft(), Gt(), Ht(), ii(), Kt() (+9 more)

### Community 131 - "pu"
Cohesion: 0.13
Nodes (17): ap(), bs(), bt(), Do(), dp(), Fu(), ip(), Iu() (+9 more)

### Community 132 - "lc"
Cohesion: 0.28
Nodes (16): ao(), co(), dc(), ff(), G(), ge(), io(), k() (+8 more)

### Community 133 - "startSocket"
Cohesion: 0.14
Nodes (14): buildLidMap(), emitDebugEvent(), emitPairEvent(), enqueuePollUpdateEvent(), getMessageContent(), getWAVersion, logPollUpdateDiagnostic(), normalizePollUpdateOptions() (+6 more)

### Community 134 - "theme-provider.tsx"
Cohesion: 0.19
Nodes (14): react, react, disableTransitionsTemporarily(), getSystemTheme(), isEditableTarget(), isTheme(), ResolvedTheme, Theme (+6 more)

### Community 135 - "Rc"
Cohesion: 0.29
Nodes (13): bc(), be(), di(), _e(), ff(), ga(), k(), Nc() (+5 more)

### Community 136 - "ChatSummary"
Cohesion: 0.08
Nodes (15): ChatId, datetime, ChatSummary, mapping_value(), Any, A provider-independent chat listing entry., Alias useful to callers that use ``chat_id`` consistently., Return the first present key from a provider mapping. (+7 more)

### Community 137 - "draft-browser-notifications.ts"
Cohesion: 0.23
Nodes (12): acknowledgeBrowserNotification(), Capabilities, installPermissionPrompt(), label(), ownsNotifierLease(), PendingDraft, preview(), processPending() (+4 more)

### Community 138 - "debounceAgentDeliver"
Cohesion: 0.22
Nodes (12): buildAgentDeliverEvent(), routingMetadata(), event, agentDeliverEnabledFor(), debounceAgentDeliver(), hmacV2(), normalizeChatLabel(), normalizeTelegramChatId() (+4 more)

### Community 139 - "ko"
Cohesion: 0.22
Nodes (14): cs(), es(), Fo(), is(), jo(), ko(), Lo(), Mo() (+6 more)

### Community 140 - "vault.py"
Cohesion: 0.24
Nodes (13): delete_session(), describe(), list_sessions(), load_session(), Path, Encrypted session vault: browser extensions store and fetch session blobs.  A "s, Strip the blob out of a record for listing responses., Store one session blob under ``name`` and return its metadata. (+5 more)

### Community 141 - "service.py"
Cohesion: 0.35
Nodes (8): Generator, _get(), Outbox, _post(), _server(), test_collect_broken_tasks_file_returns_server_error(), test_collect_rejects_oversized_result(), test_collect_tasks_results_roundtrip()

### Community 142 - "startQr"
Cohesion: 0.24
Nodes (13): credentials(), errorLogger(), failSlot(), finishLogin(), newClient(), newSyncClient(), passwordCallback(), registerAccount() (+5 more)

### Community 143 - "get"
Cohesion: 0.31
Nodes (13): Bi(), df(), ef(), get(), gf(), If(), jf(), kf() (+5 more)

### Community 144 - "il"
Cohesion: 0.15
Nodes (7): ChannelStub, RichChannelStub, test_omnichannel_enforces_adapter_capability_before_calling_missing_method(), test_omnichannel_refuses_accidental_account_replacement(), test_omnichannel_requires_account_for_ambiguous_platform(), test_omnichannel_routes_by_platform_and_account_without_provider_objects(), test_registration_rejects_unknown_capabilities_and_exposes_declared_caps()

### Community 145 - "transcription.mjs"
Cohesion: 0.44
Nodes (9): bodyAndAttachments(), loadWhisperApiKey(), multipartBody(), parseTranscript(), requestWhisper(), telegramAudioDescriptor(), main(), transcribeTelegramAudio() (+1 more)

### Community 146 - "cdp_inject_vk.cjs"
Cohesion: 0.27
Nodes (6): CDP, fs, getJSON(), http, main(), WebSocket

### Community 147 - "W"
Cohesion: 0.18
Nodes (11): Go(), Go(), W(), Go(), Go(), Go(), Go(), Go() (+3 more)

### Community 148 - "popup.js"
Cohesion: 0.44
Nodes (9): callSW(), fmtAgo(), fmtTime(), refreshChats(), refreshCollect(), refreshStats(), refreshVault(), send() (+1 more)

### Community 149 - "cdp_probe.cjs"
Cohesion: 0.31
Nodes (5): CDP, getJSON(), main(), http, WebSocket

### Community 150 - "package.json"
Cohesion: 0.22
Nodes (8): @bezrabotnyi/byok, dependencies, @bezrabotnyi/byok, name, private, scripts, start, type

### Community 151 - "va"
Cohesion: 0.40
Nodes (6): aa(), da(), fa(), ga(), va(), yc()

### Community 152 - "pu"
Cohesion: 0.11
Nodes (21): ap(), at(), bs(), bt(), Do(), dp(), Fu(), ip() (+13 more)

### Community 153 - "matrix_ingress.py"
Cohesion: 0.24
Nodes (8): MatrixMessage, MatrixReader, Any, Matrix provider reader with no UserIO store/UI/MCP dependency., main(), poll_once(), UserIOIngressClient, Matrix read ingress owned by Universal UserIO.

### Community 154 - "c"
Cohesion: 0.21
Nodes (15): _cookie_chunks(), delete_session(), describe(), list_sessions(), load_session(), _path(), Path, Per-account ChatGPT session store: browser extensions export their ``__Secure-ne (+7 more)

### Community 155 - "test_search.py"
Cohesion: 0.27
Nodes (8): Any, Global message search for Universal UserIO.  Search is deliberately user-scoped, search_conversations(), _tokens(), Generator, Outbox, test_global_search_finds_old_message_across_sources(), test_search_source_filter()

### Community 156 - "server.ts"
Cohesion: 0.38
Nodes (6): apiFormatFor(), ChatRequest, fetch(), json(), ledger, port

### Community 158 - "group-routing.mjs"
Cohesion: 0.62
Nodes (5): entityName(), explicitSelfMention(), idString(), replyMessageId(), telegramGroupRoutingAttachment()

### Community 159 - "envelope"
Cohesion: 0.57
Nodes (7): backfillDialogs(), entityLabel(), envelope(), ingestLive(), postInbox(), setSync(), syncAccount()

### Community 160 - "chat-view.js"
Cohesion: 0.52
Nodes (6): activePeerId(), deriveFilename(), guessImageType(), installObserver(), localAttachments(), pushMessage()

### Community 161 - "CDP"
Cohesion: 0.43
Nodes (3): CDP, getJSON(), main()

### Community 162 - "vk_extension_gateway.mjs"
Cohesion: 0.38
Nodes (6): lookup(), PORT, put(), seedAttachment(), server, STATIC

### Community 163 - "scripts"
Cohesion: 0.29
Nodes (7): scripts, build, dev, format, lint, preview, typecheck

### Community 164 - "byok-runs-ui.js"
Cohesion: 0.48
Nodes (6): defineByokRunDetails(), defineByokRunsView(), esc(), renderRunDetails(), renderRunsTable(), section()

### Community 165 - "allowlist.js"
Cohesion: 0.67
Nodes (5): expandWhatsAppIdentifiers(), matchesAllowedUser(), normalizeWhatsAppIdentifier(), parseAllowedUsers(), readMappingFile()

### Community 166 - "collect.py"
Cohesion: 0.40
Nodes (3): active_tasks(), load_tasks(), Operator-published site collection tasks and browser-agent results.  Tasks live

### Community 167 - "UX fixes round 1 — Messenger"
Cohesion: 0.18
Nodes (10): Context, Discard list (intentionally not in this slice), Out of scope (next rounds), Slice A — per-platform icons, Slice B — clear draft when switching chats, Slice C — better initials for phone-like senders, Slice D — search empty state with context, UX fixes round 1 — Messenger (+2 more)

### Community 168 - "record_and_send"
Cohesion: 0.50
Nodes (3): main(), Path, record_and_send()

### Community 169 - "cdp_diag.cjs"
Cohesion: 0.50
Nodes (4): getJSON(), http, main(), WebSocket

### Community 170 - "cdp_path.cjs"
Cohesion: 0.50
Nodes (4): getJSON(), http, main(), WebSocket

### Community 171 - "package.json"
Cohesion: 0.40
Nodes (4): name, private, type, version

### Community 172 - "tsconfig.json"
Cohesion: 0.40
Nodes (4): compilerOptions, paths, files, references

### Community 177 - "byok-ui.js"
Cohesion: 0.67
Nodes (3): defineByokPresetPicker(), esc(), renderPresetCards()

### Community 199 - "VK Inbox — full feature + BrowserOS install"
Cohesion: 0.18
Nodes (10): BrowserOS install, VK Inbox — full feature + BrowserOS install, Архитектура (расширение MV3), Бизнес-camary (Definition of Done), Данные (IndexedDB), Запрос пользователя, Оценка, Поток (+2 more)

### Community 200 - "VK Inbox sidecar — install"
Cohesion: 0.18
Nodes (10): 1. Pick a browser, 2. Build the static zips (only if you changed the source), 3. Load the extension, 4. Configure the connector, 5. Verify the canary, 6. Updates, Troubleshooting, Uninstall (+2 more)

### Community 201 - "UserIOIngressClient"
Cohesion: 0.33
Nodes (4): poll_once(), Any, Request, UserIOIngressClient

### Community 202 - "Zd"
Cohesion: 0.27
Nodes (10): hl(), ll(), pf(), Qd(), ul(), vl(), xc(), xl() (+2 more)

### Community 203 - "Sink"
Cohesion: 0.24
Nodes (3): Reader, Sink, test_gmail_ingress_uses_userio_cursor_api()

### Community 204 - "ChatGPT CDP starter — установка (macOS)"
Cohesion: 0.25
Nodes (7): 1. Требования, 2. Установить сервер, 3. Проверить на mock-драйвере (без аккаунта), 4. Реальный драйвер (Chrome с ChatGPT), 5. Подключить к UserIO (сервер), ChatGPT CDP starter — установка (macOS), Ссылки

### Community 205 - "pu"
Cohesion: 0.36
Nodes (8): bs(), Do(), Fu(), Iu(), mu(), Nu(), pu(), ys()

### Community 206 - "Sink"
Cohesion: 0.32
Nodes (3): Reader, Sink, test_matrix_ingress_uses_userio_cursor_and_ingress()

### Community 207 - "Epic: real attachment download per channel"
Cohesion: 0.29
Nodes (6): Definition of Done, Epic: real attachment download per channel, Минимальный путь (3 строки), Не делаем сейчас (discard), Что наблюдать / проверить, Что реализуем в этом цикле

### Community 208 - "UI fix: топ-5 фиксов из ревью Марата"
Cohesion: 0.29
Nodes (6): UI fix: топ-5 фиксов из ревью Марата, Минимальный путь (3 строки), Не делаем сейчас (discard), Результат / Definition of Done, Что наблюдать, Что чиним (приоритет)

### Community 209 - "VK Inbox → универсальный агент сбора данных (universal collect)"
Cohesion: 0.29
Nodes (6): VK Inbox → универсальный агент сбора данных (universal collect), Запрос пользователя, Минимальный путь (3 строки), Не делаем сейчас (discard), Решения, Статус

### Community 210 - "Universal UserIO Agent extension (v0.3)"
Cohesion: 0.29
Nodes (6): Universal UserIO Agent extension (v0.3), Архитектура, Безопасность, Известные ограничения, Установка в BrowserOS (localhost:9223), Что умеет

### Community 211 - "Android SMS adapter"
Cohesion: 0.40
Nodes (4): Android SMS adapter, Минимальный путь, Не делаем сейчас, Результат

### Community 212 - "WhatsApp bridge (pinned copy)"
Cohesion: 0.50
Nodes (3): HTTP contract (loopback hosts only), Run (one bridge per WhatsApp account), WhatsApp bridge (pinned copy)

### Community 213 - "React + TypeScript + Vite + shadcn/ui"
Cohesion: 0.50
Nodes (3): Adding components, React + TypeScript + Vite + shadcn/ui, Using components

### Community 223 - "up"
Cohesion: 0.18
Nodes (15): ap(), dp(), ip(), it(), kp(), lp(), ni(), op() (+7 more)

### Community 224 - "ri"
Cohesion: 0.20
Nodes (13): ap(), bt(), dp(), ip(), kp(), lp(), op(), ri() (+5 more)

### Community 225 - "et"
Cohesion: 0.15
Nodes (14): Ba(), da(), Dt(), et(), fa(), Ha(), Nc(), np() (+6 more)

### Community 226 - "ChannelAdapter"
Cohesion: 0.18
Nodes (6): ChannelAdapter, DraftGenerator, OutboxClient, Any, Protocol, One user-bound channel, independent of provider transport details.

### Community 227 - "mc"
Cohesion: 0.22
Nodes (13): ds(), Eo(), hc(), ic(), kc(), mc(), oc(), So() (+5 more)

### Community 228 - "get"
Cohesion: 0.27
Nodes (13): df(), ef(), get(), gf(), If(), jf(), jl(), kf() (+5 more)

### Community 229 - "pu"
Cohesion: 0.19
Nodes (13): bs(), da(), dd(), Do(), fa(), Fu(), Iu(), mu() (+5 more)

### Community 230 - "agent_channel.py"
Cohesion: 0.33
Nodes (8): call(), enqueue(), _now(), poll(), push_result(), Browser-agent command channel: operator enqueues commands, the extension long-po, Enqueue a command and block until the extension posts its result.      Server-si, status()

### Community 231 - "pu"
Cohesion: 0.31
Nodes (9): bs(), Do(), Fu(), Iu(), mu(), Nu(), pu(), ui() (+1 more)

### Community 232 - "va"
Cohesion: 0.40
Nodes (6): aa(), da(), fa(), ga(), va(), yc()

## Knowledge Gaps
- **322 isolated node(s):** `name`, `private`, `type`, `start`, `@bezrabotnyi/byok` (+317 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **25 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `s()` connect `s` to `index-7AoOQ2cN.js`, `index-dPl1AlHM.js`, `index-BDHIfE0-.js`, `index-D7SnVdOK.js`, `ko`, `i`, `index-DSPT1BVB.js`, `j`, `i`, `i`, `n`, `i`, `gu`, `jc`, `i`, `Yi`, `so`, `i`, `Xi`, `n`, `hu`, `i`, `n`, `mc`, `n`, `i`, `jc`, `Yi`, `wd`, `gu`, `xc`, `n`, `xc`, `hu`, `xc`, `hu`, `xc`, `hu`, `hu`, `xc`, `gu`, `xc`, `n`, `l`, `xc`, `gu`, `gu`, `gu`, `i`, `gu`, `md`, `i`?**
  _High betweenness centrality (0.169) - this node is a cross-community bridge._
- **Why does `so()` connect `xc` to `index-CxLUMw7i.js`, `lc`, `index-BZa--jkH.js`, `Rc`, `j`, `i`, `i`, `i`, `jc`, `i`, `Yi`, `so`, `Xi`, `i`, `mc`, `i`, `jc`, `gu`, `xc`, `n`, `xc`, `l`, `xc`, `xc`, `n`, `xc`, `hu`, `ko`, `i`, `Ji`, `Ji`, `ko`?**
  _High betweenness centrality (0.062) - this node is a cross-community bridge._
- **Why does `me()` connect `j` to `lc`, `Rc`, `index-D7SnVdOK.js`, `n`, `i`, `jc`, `i`, `Yi`, `so`, `Xi`, `n`, `hu`, `n`, `mc`, `n`, `i`, `jc`, `n`, `xc`, `Ji`, `Ji`?**
  _High betweenness centrality (0.018) - this node is a cross-community bridge._
- **Are the 146 inferred relationships involving `SQLiteUserIOStore` (e.g. with `AndroidSmsChannelAdapter` and `ByokBridgeGenerator`) actually correct?**
  _`SQLiteUserIOStore` has 146 INFERRED edges - model-reasoned connections that need verification._
- **Are the 134 inferred relationships involving `UserIOService` (e.g. with `AndroidSmsChannelAdapter` and `ByokBridgeGenerator`) actually correct?**
  _`UserIOService` has 134 INFERRED edges - model-reasoned connections that need verification._
- **Are the 153 inferred relationships involving `s()` (e.g. with `ce()` and `d()`) actually correct?**
  _`s()` has 153 INFERRED edges - model-reasoned connections that need verification._
- **Are the 75 inferred relationships involving `InboxMessage` (e.g. with `AndroidSmsChannelAdapter` and `ByokBridgeGenerator`) actually correct?**
  _`InboxMessage` has 75 INFERRED edges - model-reasoned connections that need verification._