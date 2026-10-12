# Roadmap: Universal UserIO

## Milestones
- ✅ v1.0 MAX personal account — phases1–3 accepted.
- ✅ v1.1 Human web send, edits and phone conversations — phases4–7 accepted.
- ✅ v1.2 Reliable everyday communication — phases8–11 accepted.
- 🚧 **v1.3 Messenger capabilities for the Airlock Secretary** — phases12–15.

Prior roadmap: [v1.2](milestones/v1.2-ROADMAP.md). No historical action repeats.

## Phases
- [ ] **Phase 12: Effective channel capabilities** — one account/provider/credential contract and consistent HTTP/MCP discovery.
- [ ] **Phase 13: Source-bound history and approved replies** — provenance, paged media and durable immutable delivery.
- [ ] **Phase 14: Telegram and MAX rich operations** — edit/reaction/sticker/file and capability-led progress.
- [ ] **Phase 15: Native Secretary parity and transport retirement** — real consumer acceptance without interrupting business.

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
- [ ] 12-01: Capability contract and current sender route

### Phase 13: Source-bound history and approved replies
**Goal**: The same native consumer reads and replies with correct source/author/thread and durable approval.
**Depends on**: Phase 12
**Requirements**: [SRC-01, SRC-02, REPLY-01, REPLY-02]
**Success Criteria**:
  1. Ingress/history/media preserve exact identities and honest paging/partial flags.
  2. The approved action binds source, account, target and immutable content.
  3. A successful provider write retains its receipt; uncertain outcomes never replay.
**Plans**: 1 plan
- [ ] 13-01: Provenance, history/media, live events and immutable approval

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

### Phase 15: Native Secretary parity and transport retirement
**Goal**: The real Secretary uses UserIO and no dedicated Telegram path for accepted operations.
**Depends on**: Phase 14
**Requirements**: [LIVE-01, LIVE-02, LIVE-03]
**Success Criteria**:
  1. Deployed native Secretary completes Telegram/MAX source/media/read/reply/rich action acceptance.
  2. Current business work, roles, confirmations and memory remain intact.
  3. Exmanager owner retires only proven replaceable dedicated paths with rollback.
**Plans**: TBD

## Progress
| Phase | Status |
|---|---|
| 12 | In Progress |
| 13 | Pending |
| 14 | Pending |
| 15 | Pending |
