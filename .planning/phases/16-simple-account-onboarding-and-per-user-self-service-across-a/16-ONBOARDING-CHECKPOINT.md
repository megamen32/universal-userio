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

## Final installed readback / next WhatsApp slice

Published/installeda532efa, manifest87cd077c: actual HTTPS `/accounts?service=max`
bytes exactly match reviewed source; authenticated `/v1/runtime` verifies this
release. This read-only check did not repeat provider/QR/model/send intents.
Prior6b4 real headed QR consumer and cancellation-loss unit remain separately
identified; no successful MAX account confirmation is inferred.

The existing WhatsApp wrapper source is deployed-only
`/opt/userio-whatsapp-qr/server.mjs` (SHA3a67b725...) and has shared slots without
a UserIO principal/owner mapping; its registration posts to the operator's
account and marks it read-only. The pinned `deploy/whatsapp-bridge` source is
not this wrapper. Existing active wrapper/session data was read, not changed.
The UserIO-owned next slice must bring the reviewed wrapper into canonical
source, bind/filter NEW slots through the same ordinary principal used by
Telegram, preserve legacy assignments and verify a bounded own-user pairing
and exact-account delivery. The unified new-user screen does not send people
to that shared operator QR. This remains implementation work, not an accepted
self-service connection or a hold on Exmanager.

## Hosted SIM choice and reviewed WhatsApp source — 13 October

The user chose hosted SIP accounts on our PBX, not requiring users to own a
separate SIP server. Rooted compatible GSM2SIP phones get unique per-owner peers;
unrooted phones use SMS APK. New SIP accounts do not reuse gateway-s21.
The own provisioning service manages one additive Asterisk include, unique
secrets and authenticated create/list/settings/delete. Waiting/registration
status remains distinct from routed-call/audio acceptance. Until a verified
per-user voice selector exists, new SIP accounts advertise no call capability
and cannot enter the existing S21 inbound context. Full per-user call routing
and sound acceptance remain OPEN, owned by UserIO with the voice producer seam.

The recovered WhatsApp QR connector is now reviewed canonical source, pinned
to Baileys6.7.22. Personal slots filter by current ordinary principal, preserve
legacy slots, register exact personal accounts and use exact user/account sender
selection; group replies target the group. Restart resumes only durable bindings.
QR cancellation, stale socket callbacks and failed registration cannot turn
into an operator connection. The connector has a finite512MiB/1GiB RAM, CPU1,
swap0, tasks128 service budget. New-user native pairing/read/send is still OPEN
until a test phone is available.

Focused regressions:44Python PASS13.12s plus actual wrapper HTTP/mock-provider
registration/source/send/isolation and Node source syntax. Later final owned
frame retains separate bounded receipt. Tests are not live phone acceptance.
Next: install reviewed Node/core/provisioner, then normal new-user headed44
WhatsApp QR/cancel/reload + hosted SIP settings/reload/disconnect/error/mobile.
Full MAX login explicitly BLOCKED until a test phone is available; QR-only
acceptance from the prior cycle remains distinct. Exmanager source/runtime is
unaffected and is not a prerequisite for this UI/provisioning case.

## Live self-service acceptance — current deployed271787c

Two separate owned headed44 cycles were captured, inspected and cleaned up.
In installed11c312d, a NEW ordinary user saw no operator WhatsApp slots, received
a real provider QR, cancelled it, reloaded with QR cleared and no account, and
saw a403 wrong-slot error. Mobile390px had no horizontal overflow. The same
user received a unique hosted SIP peer from the actual Asterisk provisioner,
reopened masked settings after reload and disconnected it. Both UserIO account
and native Asterisk peer were absent afterwards. No actual phone pairing,
SMS/call/send/model/read-ACK occurred.

That cycle deliberately remains PARTIAL: live Chrome ignored the unescaped
hyphen in its new HTML pattern-v grammar. Source271787c fixes this, plus the
tiny mobile SIP action buttons. The NEW ordinary-user corrective cycle verified
normal browser invalid-ID rejection and actual API400 without provisioning,
then created one new own peer, checked44px mobile controls and masked settings,
reloaded and disconnected. An initial consumer Ctrl+A lacked native keycode;
its pre-submit failure is retained separately. Corrected keyboard selection
continued the SAME owned page, without signup/QR/provider replay. Final actual
capture passed; stale error text in the raw inherited proof is historical,
not a new failure. The selected proof records that distinction.

Both fixtures were removed only after empty-account/history/draft assertions.
Both owned Chrome/Xvfb/PID/cgroup trees and remote PNG copies are gone; foreign
actions0. Deployed product manifest2fbe644c / commit271787c is verified.
SIP unit sourcea3bbe5b fixed the discovered ProtectHome interpreter failure;
current process is stable at~10MiB/1task. Its13 restarts are retained historical
pre-fix failures; WhatsApp currently~91MiB/11tasks under its finite budget.

Accepted available-without-phone scope: inline WhatsApp QR/error/cancel/reload,
hosted per-owner SIP create/settings/status/reload/disconnect, actual peer
removal, masked secrets and mobile controls. This does not close CONN-03/04.

### Exact remaining boundaries

- Full new-user MAX login is explicitly **BLOCKED until a test phone is
  available**. Prior QR-only acceptance is retained, not a full login PASS.
- Full new-user WhatsApp pairing, maintained read/send and reconnect need
  a consenting test phone. OwnerUserIO7bb. Proof: successful linked device →
  exact owned registry/account → incoming + own outgoing/native readback/reload.
- Hosted SIM registration/call/audio remains OPEN. OwnerUserIO7bb integrates
  the account; voice producer owner01a12c50 owns immutable accountRef+SIP peer
  selector. Existing bridgeprepare uses global target/account metadata and
  gateway-s21 correlation; no per-user sender is inferred. The active-only
  handoff to that owner and owning voice root returned skipped_inactive; no
  hidden session wake, foreign edits or whole-project hold. Next exact step:
  owner source handoff or supported per-call selector, then registry-bound
  UserIO call request/ingress and a real registered rooted phone with distinct
  two-way audio. New peers remain call_ready=false until that proof; own SIP
  inbound context rejects calls rather than using S21.

Protected evidence: `.tmp/account-onboarding-20261013/{wa-sip-web-partial,
wa-sip-web-cleanup,wa-sip-fixture-cleanup,sip-validation-web-proof-raw,
sip-validation-web-proof,sip-validation-web-cleanup,
sip-validation-fixture-cleanup,sip-native-cleanup-readback,
validation-verify,connector-install}.json`. Final correction did not repeat
the accepted WhatsApp QR intent; source/runtime stayed frozen per capture.

Independent synthetic-reader/content review found obsolete administrator-first
WhatsApp instructions and a mobile same-screen QR ambiguity. Both were fixed:
self-service instructions first, deployment clearly separate, and explicit
computer/second-screen QR guidance in both UI and docs. Hosted calls guide also
points to personal SIP issuance and keeps unverified voice disabled. Independent
delta reviewPASS; this is simulated content review, not human research or phone
pairing. Final content-only browser capture follows this documentation/UI delta
without another QR, SIP create, send, model or accepted provider replay.

## New real MAX pending-status defect

S21 became reachable, was leased and physically verified asR5CR702SRFP.
AgentDevice reached the normal authenticated MAX app after an ordinary swipe-only
lockscreen (no password required). The native same-phone auth link merely opens
the Devices instruction; it does not confirm login in this installed app.
No new WEB session/auth exchange/account/send occurred.

The new normal browser case failed on its FIRST unconfirmed QR poll. An isolated
normal UserIO provider probe confirmed actual status shape contains expiresAt
and ttl, and omits false/default loginAvailable. Our strict parser incorrectly
raisedValueError and cancelled a valid pending login. It now accepts this exact
positive pending shape asfalse, still rejects malformed fields and never grants
AuthByTrack before true confirmation.19focusedregressions PASS5.11s. Old failed
QR/private-link copies are consumed/removed, never replayed. Successful ordinary
confirmation remains a distinct live boundary, not inferred from this repair.
