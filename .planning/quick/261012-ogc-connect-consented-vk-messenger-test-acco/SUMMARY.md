---
type: quick
status: complete
date: 2026-10-12
---

# Personal VK: real ordinary authorization and maintained reading

Delivered scope: authenticated per-user personal VK read connection, maintained bounded history/new-message import, actual provider identity, direct/group classification, no permanent browser/phone and no root requirement for ordinary onboarding. VK send/edit/reaction/read acknowledgement remain unsupported and visibly read-only.

Initial native test account200000000106 enrolled201; actual5 incoming messages matched full provider bodies. ONE unique native self6 automatically imported by the running poller, no manualimport/replay. Invalid emptyvk:undefined placeholder alone deleted by supported account API; history preserved.

Fixed actual silent-token authorization and classification gaps: native peer.type=user is direct even for large IDs; routing-media collision separately accepted under quick261012-pxe. The legacy oauth/authorize endpoint discards requestedUUID; the actual directid.vk.ru/auth entry preservesUUID36 and returns?payload=. Form/backend now support that actual callback, exact user validation, both query/fragment carriers without mixing, safe stage/code errors and fail before enrollment on denied message access.

Canonical source2c71175 installed/e952ef69.26 focused checks PASS5.99s/helper0/same-generation cleanup; JS syntax checked. Earlier legacyassertionFAIL and intermediate resource/parser/snapshot/refused-auth cases are retained as incomplete, not reused as acceptance.

Actual normal web acceptance:
- Own authenticated root→VK→native self6 exacttext/direct/read-only→reload/reopen, visible normal personalform, captured6B2.
- Fresh native Chrome login using the standard public authorization entry, same test account, exact nonce preserved. No root extraction in this new login.
- Own normal maskedwebform+expectedID→ONEsubmitF9ED→visible account connected. This consumed the fresh one-time result, without fallback/reexchange.
- A new serverprocess reads the CURRENT savedConnectorSecrets identity+native self6; storedUserIO body exact and one row, directkind. No phone/browser/manualimport required.

Protected acceptance evidence under .tmp/vk-personal-20261012: browser-verdict.json, browser-freshauth-F9ED6599.json, fresh-session-after-web-proof.json, automatic-import-proof.json, continuous-sync-first.json, saved-session-resume-proof.json, direct-verify.json.

All owned Mini tabs closed, target headers cleared, configs/PNGs removed; independent R38 nativeabsence/resource releases retained. S21leases released and foregroundcontrollers stopped. Transient authorization snapshots are redacted and temporary credential exports removed; actual connector remains bound to its user.

Source/documentation published to main; publicguide describes realflow and read-only limitations. This quick closes VK connection/read scope, not the whole v1.3 Secretary retirement or all VK write capabilities.
