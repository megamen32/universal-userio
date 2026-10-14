# Requirements: UserIO v1.3

**Defined:** 2026-10-12
**Core Value:** One real messenger interface for the native Airlock Secretary.

## v1.3 Requirements

### Effective capabilities
- [x] **CAP-01**: UserIO exposes supported and allowed operations for an exact connected account, intersecting provider support, account permissions and credential permissions.
- [x] **CAP-02**: HTTP and both MCP surfaces return the same capability contract, including explicit unsupported/denied reasons; existing read-only credentials stay read-only.
- [x] **CAP-03**: SDK selects channel operations from capabilities rather than privileged Telegram assumptions and preserves the actual current Secretary sender identity.

### Source, history and approval
- [x] **SRC-01**: Native ingress preserves exact account, conversation, provider message, author, owner direction, reply and thread provenance without impersonating group authors.
- [x] **SRC-02**: Bounded history/message/media reads have stable paging and honest partial flags on Telegram and MAX.
- [x] **REPLY-01**: Normal draft/approval binds the exact source, body, action payload, account and provider target; duplicate/uncertain writes do not replay.
- [x] **REPLY-02**: Provider acceptance includes a durable account/source-bound receipt and updates the same canonical inbox projection.

### Rich operations
- [x] **ACT-01**: Same-ID own-message edits work repeatedly on Telegram and MAX and preserve source/receipt provenance.
- [x] **ACT-02**: Reactions use the same interface and actual provider-supported behavior or explicit unsupported results.
- [x] **ACT-03**: Sticker/file sends use ordinary immutable approval/outbox with provider-supported references, not local paths or raw client bypass.
- [ ] **ACT-04**: Typing/progress and readable fallback follow actual channel capabilities.

### Native consumer and retirement
- [ ] **LIVE-01**: The actual deployed Secretary performs equivalent Telegram and MAX journeys through UserIO with correct speaker/source/media context.
- [ ] **LIVE-02**: Native integration preserves current business work, SDK confirmations, tasks and L1/L2/L3 memory; tests use owned QA destinations and retained receipts.
- [ ] **LIVE-03**: Dedicated Secretary Telegram transport is retired only after real generic consumer acceptance; shared provider clients and rollback are preserved.

## Out of Scope
For phases12–15 only: new provider authorization/session clones, blanket read-only token upgrades,
Airlock core forks or another reasoning loop, historical send/task replay,
deletion of old customer content, and replacing business/task storage with UserIO.

## Traceability
| Requirement | Phase | Status |
|---|---|---|
| CAP-01 | 12 | Complete |
| CAP-02 | 12 | Complete |
| CAP-03 | 12 | Complete |
| SRC-01 | 13 | Complete |
| SRC-02 | 13 | Complete |
| REPLY-01 | 13 | Complete |
| REPLY-02 | 13 | Complete |
| ACT-01 | 14 | Complete |
| ACT-02 | 14 | Complete — actual add/change/remove and native readback on both providers |
| ACT-03 | 14 | Complete — actual stickers and byte-identical files on both providers; native Secretary remains phase15 |
| ACT-04 | 14 | Partial — recipient typing explicitly deferred until second test account |
| LIVE-01 | 15 | Partial — native MAX model reply and repeated edits delivered; authorization restored with deployed Secretary live read/media; new external inbound/full rich parity pending |
| LIVE-02 | 15 | Partial — live acceptance pending |
| LIVE-03 | 15 | Deferred in this session by explicit owner instruction; later Exmanager retirement acceptance |

Latest [native MAX final verification](phases/15-native-secretary-parity-and-transport-retirement/15-native-max-final-VERIFICATION.md)
retains the real model/normal confirmation/send/two-edit proof separately from
later `login.token` refusal. That refusal is now resolved by ordinary same-account
login and protected persistence: installed c6cdf44, real UserIO MCP read and
independent deployed Secretary source/history/media acceptance. Old receipts
are preserved; this does not certify a new inbound/model/reply journey.

Coverage including phase16:19requirements,19mapped,0unmapped. Original phases12–15 remain14requirements. Previous v1.2 requirements
remain in milestones/v1.2-REQUIREMENTS.md; no prior acceptance is reset/replayed.

## Evidence reconciliation — 2026-10-12

Accepted10/14requirements (71% rounded), not a whole-product readiness estimate.
CAP-01–03: [phase12 summary](phases/12-effective-channel-capabilities/12-01-SUMMARY.md).
SRC-01–02/REPLY-01–02: [phase13 summary](phases/13-source-bound-history-and-approved-replies/13-01-SUMMARY.md).
ACT-01–03: [phase14 verification](phases/14-telegram-and-max-rich-operations/14-rich-provider-VERIFICATION.md).
Remaining ACT-04 and LIVE-01–03 have installed/partial evidence but no whole
requirement pass; owners and exact closing proofs are in [ROADMAP](ROADMAP.md)
and [14-UAT](phases/14-telegram-and-max-rich-operations/14-UAT.md).

Accepted everyday SMS/phone/group authors/chat switches/draft source/time/mail/
Bearer/OAuth/landing/docs retain their original v1.0–v1.2 or quick-task ownership.
They are explicitly listed as closed in ROADMAP, without inventing new CAP IDs.
VK read acknowledgements/session renewal and older-history viewport
anchor are additional open follow-ups with their own owner and live evidence
criteria there; completed VK text/edits/photo/document acceptance stays closed.


## Phase16 — Own-user simple account onboarding

- [x] CONN-01: one consistent account screen and short guides; real new-user
  desktop/mobile/back/reload, normal own fixture SMS config issuance and cleanup.
- [ ] CONN-02: ordinary Telegram QR/phone connection belongs to its user and
  keeps real input/reply working. Source/ownership/live empty-state accepted;
  new actual enrollment and message acceptance pending. OwnerUserIO7bb.
- [ ] CONN-03: personal MAX and WhatsApp enrollments, persistence and maintained
  input/reply work for arbitrary users. Global/shared operator path not substitute.
- [ ] CONN-04: root/unrooted SMS and individual call setup have their own clear
  connection route and live phone/SIP ownership proof. SMS source/UI accepted,
  hosted SIP issuance/settings/reload/disconnect/mobile now live accepted; actual per-user voice selector and new physical registration/call/audio pending.
- [ ] CONN-05: new own-user mail/VK/Matrix connection/reconnect agrees with guides
  and actual provider source/identity/readback; old operator acceptance preserved.

All remaining phase16 work belongs to UserIO7bb, independent of Exmanager15.

Phase16 traceability: CONN-01 Complete; CONN-02 Partial/source; CONN-03
Partial/personal QR and cancellation source+live; CONN-04 Partial/hosted
SIP+SMS configuration issuance source+live; CONN-05 Pending new-user acceptance.
All5 mapped to phase16. Overall current milestone11/19requirements accepted
(58%), with2/5fullyclosedphases/plans (40%); phase16 remains1/5 (20%).
See latest [checkpoint](phases/16-simple-account-onboarding-and-per-user-self-service-across-a/16-ONBOARDING-CHECKPOINT.md) for exact phone and voice-binding closure criteria.

Latest phase16 delta: MAX pending/status/cancel/reload/mobile accepted in installed
4b9169e. S21 root/unlock and existing native MAX LOGIN separately accepted, no
new enrollment. Camera-confirmed NEW MAX and physical WhatsApp pairing/read/send
remain open; phone absence/password is no longer a blocker. Hosted SIP issuance
and cleanup are accepted; call routing/audio are still unverified and disabled.
No requirement checkbox or percentage inflated by these partial sub-results.

## Phase17 — Telemost recording and Yandex chats

- [x] TM-01: quiet automatic meeting join, camera/mic off, real incoming audio.
- [x] TM-02: private persistent recording/transcript; real play/download/reload.
- [x] TM-03: same-owner personal/group Yandex chats, correct authors/full text,
  maintained sync/history and one authorized normal web send/receipt/reload.
- [x] TM-04: narrow recorder authorization and actual native Secretary-started
  journey. Separate encrypted meeting_recording resource; NEW Telegram link →
  quiet join → real voice/Whisper → native stop → play/reload/download accepted.
  Exmanager PR3 merged and installed; existing readonly resource unchanged.

All4 map to phase17;4accepted100%. Current total15/23accepted65%rounded.
Separate follow-ups: arbitrary-user Yandex enrollment; generic messenger rich
actions and Secretary Yandex adapter; atomic shared API admission/drain.
