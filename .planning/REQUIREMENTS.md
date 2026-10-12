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
New provider authorization/session clones, blanket read-only token upgrades,
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
| ACT-04 | 14 | Partial — live acceptance pending |
| LIVE-01 | 15 | Partial — live acceptance pending |
| LIVE-02 | 15 | Partial — live acceptance pending |
| LIVE-03 | 15 | Partial — live acceptance pending |

Coverage: 14 requirements, 14 mapped, 0 unmapped. Previous v1.2 requirements
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
VK reactions/read acknowledgements/session renewal and older-history viewport
anchor are additional open follow-ups with their own owner and live evidence
criteria there; completed VK text/edits/photo/document acceptance stays closed.
