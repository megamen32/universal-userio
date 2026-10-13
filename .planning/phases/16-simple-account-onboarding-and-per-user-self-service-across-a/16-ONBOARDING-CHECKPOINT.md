---
phase: 16
status: partial
requirements_accepted: [CONN-01]
requirements_open: [CONN-02, CONN-03, CONN-04, CONN-05]
---

# Account onboarding — 13 October checkpoint

## Real user surface accepted

Published047f304 unified account screen: a NEW ordinary user signed up, selected
all ten provider panels, returned to overview, reloaded Yandex setup, and used
mobile390px without horizontal overflow. Own Android fixture enrollment,
connection reload and exact disconnect passed. Telegram showed only this new
user's empty slot list. Existing operator slots were not exposed. The fixture
account and user were removed only after empty-history/account assertions.

Published6b4bd74 / manifest1b8e7731 was actually installed and verified. A NEW
ordinary user opened MAX in the same account screen, displayed a real provider
QR and same-phone link, cancelled it through the normal button and reloaded.
The QR stayed cleared and accounts remained empty. Screenshot inspected.
Exact own headed44 target4EAE51101E2E58B6BB898CC04D61BA65 and Chrome/Xvfb were
closed, native PIDs/cgroup absent, foreign signals0. Own fixture user removed.
No AuthByTrack, send, model, phone UI action or read acknowledgement occurred.

## Implemented and verified in source

- Telegram enrollment slots persist exact UserIO owner IDs; request cookies
  stay transient, personal users never inherit operator2FA or service identity.
- Current MAX Web binary MessagePack/LZ4 protocol fixes the reproducible old
  JSON QR failure. Real anonymous provider QR now returns normally.
- MAX QR challenges belong to the requesting user, expire via existing server
  maintenance and are consumed before AuthByTrack (unknown outcome not replayed).
- Verified MAX profile IDs bind protected600 connector credentials and registry
  entries to that user. Owned send/edit/history/media routes select this account;
  polling imports edits and outgoing messages without read ACK. Existing operator
  credentials/routes preserved. New user rights are not granted to other clients.
- HTTP foreign-owner/service/readonly rejection, expired-flow closure, credential
  replacement and staged-secret cleanup regressions passed. Ordinary combined
 48checks21.59s, later QR-link/UI delta8checks6.06s; no OOM/swap, own cleanup.
- Documentation builds/checks reproducibly27pages. Root-relative guide links
  corrected to public UserIO URLs so GitHub documentation can open the cabinet.
- Concurrent/lost cancel response is displayed as unconfirmed and refreshes the
  account list instead of falsely saying cancellation succeeded (fast unitPASS).

Protected evidence: `.tmp/account-onboarding-20261013/{web-proof,max-web-proof,
max-web-cleanup-proof,max-fixture-cleanup,max-personal-verify,
phone-unavailable-disposition}.json`; accepted tests and earlier REDs retained.

## Remaining outcome and owners

| Requirement | Owner | Missing closure proof / next step |
|---|---|---|
| CONN-02 Telegram personal enrollment | UserIO7bb | NEW normal user's successful authorization, restart/persistence, actual read and exact-account send. Empty-slot/isolation proof does not replace it. |
| CONN-03 MAX personal enrollment | UserIO7bb | NEW normal QR/app confirmation → actual profile/persisted account → maintained read/send/reload. S21 reverse22220/22221 absent; guarded ADB22221 refused; agent-device devices[]/DEVICE_NOT_FOUND. Lease acquired then released, phone actions0. Restore canonical phone connection or have the account owner confirm in the normal MAX page; no old session extraction/replay. |
| CONN-03 WhatsApp personal enrollment | UserIO7bb | Existing bridge is one account per process. Add a bounded per-user managed binding and NEW owner pairing/read/send; unified UI explicitly avoids the operator's shared QR. Not a completed personal connector. |
| CONN-04 Phone | UserIO7bb; infrastructure owner for SIP provisioning | SMS guide/config enrollment accepted, new handset application pairing still required; personal SIP/voice route provisioning + actual own call/audio acceptance remains. |
| CONN-05 Mail/VK/Matrix | UserIO7bb | NEW owner connection/reconnect/reload through each guide; earlier existing-account business proofs retained but not substituted for a new connection. |

Phase remains1/5 fully accepted (20%). No completed plan summary or whole-phase
completion is generated. Exmanager Phase15 proceeds independently; no holds,
transport retirement or changes to its source/core/credentials.
