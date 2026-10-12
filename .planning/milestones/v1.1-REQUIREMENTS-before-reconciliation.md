# Requirements: MAX v1.0

- [x] AUTH-01: Existing Android authorization reused with user authority; account identity proven.
- [x] AUTH-02: Session reused without browser/phone; no token in logs/Git.
- [x] READ-01: UserIO history preserves provider IDs, chat, sender, time, direction and attachments.
- [x] READ-02: Live messages, own replies and reconnect catch-up deduplicated.
- [x] SEND-01: Ordinary draft plus explicit approval sends once to exact chat, with actual provider receipt and history readback.
- [x] OPS-01: Bounded native service, reconnect/stop, rollback and real consumer proof.

## Traceability
AUTH-01/AUTH-02 → Phase 1; READ-01/READ-02 → Phase 2; SEND-01/OPS-01 → Phase 3.

## Release criteria
Authenticated account, real history parity, new event, approved test message in owner Saved Messages, reconnect readback. Preserve other providers and never replay an uncertain send. Ordinary checks have maximum 180 seconds; slow reliability soak is nightly.
