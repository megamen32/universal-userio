# Roadmap: Universal UserIO

## Milestones
- ✅ v1.0 MAX personal account — phases1–3 accepted.
- ✅ v1.1 Human web send, edits and phone conversations — phases4–7 accepted.
- ✅ v1.2 Reliable everyday communication — phases8–11 accepted.
- 🚧 **v1.3 Messenger capabilities for the Airlock Secretary** — phases12–16 (including own-user onboarding).

Prior roadmap: [v1.2](milestones/v1.2-ROADMAP.md). No historical action repeats.

## Phases
- [x] **Phase 12: Effective channel capabilities** — installed HTTP/MCP discovery and real restricted-credential/native account proof accepted.
- [x] **Phase 13: Source-bound history and approved replies** — real Telegram source/media/reply receipts and bounded MAX reads accepted.
- [ ] **Phase 14: Telegram and MAX rich operations** — edit/reaction/sticker/file and capability-led progress.
- [ ] **Phase 15: Native Secretary parity and transport retirement** — real consumer acceptance without interrupting business.
- [ ] **Phase 16: Simple account onboarding** — one account screen, personal connectors and hosted SIM setup.

## Phase Details

### Phase 12: Effective channel capabilities
**Goal**: Secretary discovers usable operations for the exact current account and credentials.
**Depends on**: accepted v1.2
**Requirements**: [CAP-01, CAP-02, CAP-03]
**Success Criteria**:
  1. Telegram/MAX advertise actual supported and allowed operations with explicit reasons.
  2. HTTP and both MCP surfaces agree; read-only credentials deny mutations.
  3. Current Secretary Telegram identity has a verified supported sender route; it is not silently replaced by a private owner account.
**Plans**: 1 plan
- [x] 12-01: Capability contract and current sender route

**Status:** Complete — 3/3 requirements, 100%. [Accepted evidence](phases/12-effective-channel-capabilities/12-01-SUMMARY.md).

### Phase 13: Source-bound history and approved replies
**Goal**: The same native consumer reads and replies with correct source/author/thread and durable approval.
**Depends on**: Phase 12
**Requirements**: [SRC-01, SRC-02, REPLY-01, REPLY-02]
**Success Criteria**:
  1. Ingress/history/media preserve exact identities and honest paging/partial flags.
  2. The approved action binds source, account, target and immutable content.
  3. A successful provider write retains its receipt; uncertain outcomes never replay.
**Plans**: 1 plan
- [x] 13-01: Provenance, history/media, live events and immutable approval

**Status:** Complete — 4/4 requirements, 100%. [Accepted evidence](phases/13-source-bound-history-and-approved-replies/13-01-SUMMARY.md). This closes the shared contract, not the final native MAX journey in phase15.

### Phase 14: Telegram and MAX rich operations
**Goal**: Supported rich actions share one normal approval/outbox interface.
**Depends on**: Phase 13
**Requirements**: [ACT-01, ACT-02, ACT-03, ACT-04]
**Success Criteria**:
  1. Repeated own-message edits retain the same provider ID in both channels.
  2. Reactions and stickers/files work through declared capabilities and exact approval.
  3. Progress/typing follows actual support; unavailable operations have readable fallback.
**Plans**: 1 plan
- [ ] 14-01: Native-reference rich actions and media

**Status:** Partial — ACT-01–03 accepted, 3/4 requirements, 75%. Actual Telegram/MAX reaction add/change/remove, sticker identity and recipient file bytes are verified. Typing commands are accepted; recipient visibility/fallback acceptance is still open. [Current verification](phases/14-telegram-and-max-rich-operations/14-rich-provider-VERIFICATION.md), [remaining matrix](phases/14-telegram-and-max-rich-operations/14-UAT.md). Native Secretary MAX remains phase15.

### Phase 15: Native Secretary parity and transport retirement
**Goal**: The real Secretary uses UserIO and no dedicated Telegram path for accepted operations.
**Depends on**: Phase 14
**Requirements**: [LIVE-01, LIVE-02, LIVE-03]
**Success Criteria**:
  1. Deployed native Secretary completes Telegram/MAX source/media/read/reply/rich action acceptance.
  2. Current business work, roles, confirmations and memory remain intact.
  3. Exmanager owner retires only proven replaceable dedicated paths with rollback.
**Plans**: 1 plan
- [ ] 15-01: Real native parity, business preservation and scoped retirement

**Status:** Pending final native acceptance — 0/3 fully closed requirements, 0%. Telegram text/vision/streaming and native MAX grounded final reply/two same-ID edits are accepted partial verticals. MAX same-account authorization is restored in installed c6cdf44; deployed Secretary source/history/media acceptance is recorded in Exmanager3e58f39. Full external inbound/rich parity remains with Exmanager; retirement is explicitly a later separate step. [Latest verification](phases/15-native-secretary-parity-and-transport-retirement/15-native-max-final-VERIFICATION.md).

## Progress
| Phase | Status | Closed requirements | Requirement progress |
|---|---|---|---|
| 12 | Complete | CAP-01–03: 3/3 | 100% |
| 13 | Complete | SRC-01–02, REPLY-01–02: 4/4 | 100% |
| 14 | Recipient typing deferred until second test account | ACT-01–03: 3/4 | 75% |
| 15 | Native MAX reply/edit and restored live auth/media accepted; new inbound/full parity/retirement open | 0/3 | 0% |
| 16 | Unified UI accepted; personal QR and SIP issuance/control live-proven, full phone pairing/call route pending | CONN-01:1/5 | 20% |

Current milestone: **2/5 phases/plans closed (40%); 11/19 requirements accepted (58%)**. These are different counters. The original GSD all-directory bar also counts retained historical phases and is not a product-readiness percentage. No full milestone completion is claimed.

## Already accepted user outcomes — retained, not reopened

These belong to accepted v1.0–v1.2 or completed quick tasks; they are not new CAP/SRC requirements. Reconciliation does not move them into phases12–14 to inflate progress.

| Closed outcome | Evidence / exact boundary |
|---|---|
| Continuous SMS synchronization, one native phone account | [Phase08](phases/08-everyday-connections/08-01-SUMMARY.md), [retained continuous-SMS/native readback](STATE.md): new outgoing/incoming SMS automatically imported, cursor advanced and later polls did not duplicate them |
| Calls grouped by person, full numbers, start/end and older history | [Phase08](phases/08-everyday-connections/08-01-SUMMARY.md), [retained phone capture](STATE.md); unknown historic numbers remain archived by the owner's explicit choice; scroll-anchor proof remains separately open |
| Telegram/MAX group author names | [Phase08](phases/08-everyday-connections/08-01-SUMMARY.md): actual group history/author web capture |
| Named work chats and per-chat analysis checkbox / search badges | [Quick261012-j88](quick/261012-j88-per-chat-secretary-switch-and-explicit-w/SUMMARY.md): ON/reload/reopen → OFF/reload/reopen, exact reading preference restored |
| Correct reply roles, draft creation time, exact source quote and navigation | [Phase09](phases/09-secretary-context/09-01-SUMMARY.md): current sticker reaction/source/time/reload/reopen accepted |
| Meaningful work-chat and Gmail/Yandex notifications | [Phase10](phases/10-timely-notifications/10-01-SUMMARY.md): real mail/incoming → analysis → delivered source/summary/action; later positive Yandex delivery supersedes the earlier SMTP refusal |
| Personal Bearer/revoke/read-only access and ChatGPT OAuth | [Phase08](phases/08-everyday-connections/08-01-SUMMARY.md), [read-only native acceptance](quick/261012-hj3-named-personal-bearer-with-server-enforc/SUMMARY.md); existing OAuth account consumer accepted, no new M1 browser authority implied |
| Landing, authenticated cabinet, visible cabinet/GitHub links, docs | [Phase08](phases/08-everyday-connections/08-01-SUMMARY.md), [docs acceptance](quick/261012-6tp-update-userio-documentation-and-short-pu/261012-6tp-SUMMARY.md) |
| MAX authorization recovery | [Resolved auth debug](debug/max-post-edit-auth-refusal.md): ordinary SAME900000102 login, protected persistence, actual deployed UserIO MCP and independent Secretary source/history/media read accepted; no old action replay |
| Personal VK authorization/read/send, repeated edits and photo/document delivery | [VK rich acceptance](quick/261012-vkr-vk-edit-attachments/VERIFICATION.md): same message changed twice, photo and byte-identical document retained after reload |

## Remaining work — owner and closure proof

| Open item | Owner | Required evidence to close |
|---|---|---|
| **Native Secretary MAX journey and scoped transport retirement** (LIVE-01–03, phase15) | Exmanager lead `01a11197-d2d9-7812-97c8-c2da3f454461`; UserIO lead fixes only a reproduced UserIO contract defect | Current restored MAX live read/media already accepted; the exact missing input is ONE NEW genuine image/message from the interlocutor after cursor50967. That new authorized MAX source must be handled by the deployed Secretary through its existing Airlock/UserIO resource; exact author/source/media, approved reply/provider receipt and native readback; original business/memory/readonly checkpoints retained. Then owner-recorded removal of only proven replaceable Telegram glue, rollback and affected fresh native acceptance. UserIO-only MAX sends do not substitute for this proof. Exmanager-owned document-content reader is now live-accepted for actual bytes/text/SHA; source24 was not resent. Installed6288120 MAX history also passes actual native third-page read. |
| **Recipient typing/progress acceptance — explicitly deferred until second test account** (remaining ACT-04, phase14) | UserIO lead `01a11747-7bb9-7bc0-8c0a-ce64db49105d` owns contract/provider checks; Exmanager lead owns actual Secretary SDK journeys | Complete remaining typing/progress rows in [14-UAT](phases/14-telegram-and-max-rich-operations/14-UAT.md): owned authenticated recipient observation or a truthful unsupported fallback for each Telegram/MAX account. Provider ACK alone is not visible typing. Reactions/sticker/file delivery passed the new real provider cycle; native Secretary rich parity remains phase15, not an additional UserIO send replay. |
| **VK read acknowledgements, automatic session renewal** | UserIO lead `01a11747-7bb9-7bc0-8c0a-ce64db49105d` | Separate source/runtime changes and permitted live checks: a newly unread authorized message becomes read after normal viewing while stealth suppresses it; controlled credential expiry/renewal resumes maintained sync without manual re-entry or duplicate messages. Current text/edit/file acceptance does not imply these capabilities. |
| **Older call-history scroll anchor** | UserIO lead `01a11747-7bb9-7bc0-8c0a-ce64db49105d` | Normal managed browser on server44/88: record the visible message ID and viewport offset, load older history, show that the same message stays at the same offset within measured tolerance; retained screenshot/DOM readback. API pagination/counts alone are insufficient; no calls needed. |

The earlier reconciliation performed no provider action; the subsequent owned rich cycle installed reviewed source and captured new explicit QA actions in [provider verification](phases/14-telegram-and-max-rich-operations/14-rich-provider-VERIFICATION.md). Earlier failures, cancelled delete-six scope and the explicit unknown-number archive are preserved, not reintroduced as open product work. Prior provider quota429 is an operational observation; accepted notification delivery is not a guarantee of uninterrupted external model availability.

13October: personal VK reaction add/change/remove is live-accepted through
generic UserIO MCP with original baseline restored, alongside fresh Telegram/MAX
provider acceptance. [Reaction proof](quick/261013-oq5-userio-telegram-max-vk-reaction-lifecycl/SUMMARY.md).
Native Secretary automatic reaction behavior remains Exmanager97-owned; UserIO
work proceeds independently. No all-capabilities/milestone completion is inferred.

### Phase 16: Simple account onboarding and per-user self-service across all providers

**Goal:** Каждый пользователь подключает свои аккаунты из одного простого раздела с короткими понятными шагами.
**Requirements**: CONN-01–05
**Depends on:** Existing UserIO contracts/providers; independent of Exmanager Phase15.
**Plans:** 1 plan, in progress
**Status:** 1/5 requirements fully accepted (20%): unified UI; remaining per-provider self-service/live connection acceptance open.

Plans:
- [ ] [16-01](phases/16-simple-account-onboarding-and-per-user-self-service-across-a/16-01-PLAN.md) — unified UI and personal connectors

**Phase16 current status:** unified UI real PASS; personal MAX source/runtime and live QR/cancel/reload accepted, successful new account confirmation/read/send pending the native camera confirmation. WhatsApp personal QR/cancel/reload and hosted per-user SIP settings/reload/disconnect/error/mobile are live-accepted in271787c. Full WhatsApp pairing/read/send, SIM voice selector/phone/audio and new-user provider acceptance remain open. S21 is now available, root and current native MAX LOGIN were verified; it is not password-blocked. New QR account confirmation remains unverified because this MAX scanner accepts camera input only; a same-phone link opens instructions. The pending-status parser defect was fixed and live two-poll/cancel/reload/mobile acceptance passed in4b9169e. See [checkpoint](phases/16-simple-account-onboarding-and-per-user-self-service-across-a/16-ONBOARDING-CHECKPOINT.md).
OwnerUserIO7bb, independent of Exmanager15. Closure criteria in
[16-01-PLAN](phases/16-simple-account-onboarding-and-per-user-self-service-across-a/16-01-PLAN.md).
