# Requirements: UserIO v1.3

**Defined:** 2026-10-12
**Core Value:** One real messenger interface for the native Airlock Secretary.

## v1.3 Requirements

### Effective capabilities
- [ ] **CAP-01**: UserIO exposes supported and allowed operations for an exact connected account, intersecting provider support, account permissions and credential permissions.
- [ ] **CAP-02**: HTTP and both MCP surfaces return the same capability contract, including explicit unsupported/denied reasons; existing read-only credentials stay read-only.
- [ ] **CAP-03**: SDK selects channel operations from capabilities rather than privileged Telegram assumptions and preserves the actual current Secretary sender identity.

### Source, history and approval
- [ ] **SRC-01**: Native ingress preserves exact account, conversation, provider message, author, owner direction, reply and thread provenance without impersonating group authors.
- [ ] **SRC-02**: Bounded history/message/media reads have stable paging and honest partial flags on Telegram and MAX.
- [ ] **REPLY-01**: Normal draft/approval binds the exact source, body, action payload, account and provider target; duplicate/uncertain writes do not replay.
- [ ] **REPLY-02**: Provider acceptance includes a durable account/source-bound receipt and updates the same canonical inbox projection.

### Rich operations
- [ ] **ACT-01**: Same-ID own-message edits work repeatedly on Telegram and MAX and preserve source/receipt provenance.
- [ ] **ACT-02**: Reactions use the same interface and actual provider-supported behavior or explicit unsupported results.
- [ ] **ACT-03**: Sticker/file sends use ordinary immutable approval/outbox with provider-supported references, not local paths or raw client bypass.
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
| CAP-01 | 12 | Pending |
| CAP-02 | 12 | Pending |
| CAP-03 | 12 | Pending |
| SRC-01 | 13 | Pending |
| SRC-02 | 13 | Pending |
| REPLY-01 | 13 | Pending |
| REPLY-02 | 13 | Pending |
| ACT-01 | 14 | Pending |
| ACT-02 | 14 | Pending |
| ACT-03 | 14 | Pending |
| ACT-04 | 14 | Pending |
| LIVE-01 | 15 | Pending |
| LIVE-02 | 15 | Pending |
| LIVE-03 | 15 | Pending |

Coverage: 14 requirements, 14 mapped, 0 unmapped. Previous v1.2 requirements
remain in milestones/v1.2-REQUIREMENTS.md; no prior acceptance is reset/replayed.
