---
phase: 15-native-secretary-parity-and-transport-retirement
status: partial
verified: 2026-10-13
requirements-completed: []
---

# Current native Secretary read acceptance

The actual deployed Secretary MCP at `/api/agent/exmanager/mcp` read through
its existing encrypted UserIO resources. This is native **tool/read** evidence,
not a new model-generated MAX reply, full parity or transport retirement.
The protected owner session was refreshed by the cached official `air deploy
list` command in1.088s. No build/source upload/upgrade was started by UserIO;
no provider send, approval, model request or read ACK was issued by these probes.

Private artifacts: `.tmp/native-v13-final-20261013/` under UserIO. Credential
values, signed resource URLs and private message bodies are not published here.
The existing private `userio_accounts` resource still reads13 accounts, including
owner Telegram900000101 and MAX. This proves current functionality, not a claim
that no other authorized owner has ever changed its encrypted resource.

## Results

| Native operation | Actual result | Evidence |
|---|---|---|
| Current sender capabilities | TG881 and MAX both return `credential_scope=messenger_actions`, correct sender and effective operations | native-read.json |
| Resolve actual source | TG QA provider24 and MAX Darja117429138456803118 preserve exact account/peer/provider/author and media1 | native-read.json |
| Telegram document content | Same already delivered24 now returns actual150bytes/text/SHA2569575a93d…, matching the independently downloaded recipient bytes. The former caption substitution is fixed by Exmanager relay554/31a4, without resend24 | native-read.json and native-media-match.json |
| MAX actual image attachment | Native MCP emits text metadata plus the standard SDK `resource_link`. Downloading that exact signed native S3 link, without forwarding the owner Bearer, returns90258bytes/SHA2563d020f00451bf787853cfec1612306fefdc476a4b5d832b76aa535244761775a, matching UserIO provider bytes | native-resource-download.json |
| Bounded Telegram history | Two current pages of two real sources each, usable cursors, real authors, partial=true and no provider read ACK | native-history.json |
| Bounded MAX history | Fixed installed6288120: three limit2 pages reach known older117424822620729543, six distinct sources/authors, usable cursors, partial=true and no ACK. Original two-page failure retained | native-history-fixed.json; native-history.json |
| Existing private read resource | Current native userio_accounts succeeds,13 accounts, owner TG540 and MAX present; no resource mutation | native-private-accounts.json |

Initial diagnostic assertions wrongly expected inline MCP image blocks and then
MCP resources/read. That surface returns standard resource links instead. The
failed assertions remain in native-media-match.json; the exact signed S3 byte
comparison is the actual final attachment proof. They are not a product image
loss or a new model failure.

## Actual remaining work

- [UserIO MAX pagination](../../debug/max-history-inclusive-boundary.md) is
  resolved: new52focusedPASS25.79s/helper0/same-generation cleanup; scoped
  source6288120 published/installed/manifest verified; actual native Secretary
  third-page reread passed. Original live RED and prepayload75 remain separate.
- Native MAX **reasoning/proposal/confirmation/reply** and rich progress still
  need the Exmanager owner's new final consumer after its published SDK source
  is installed. Existing rejected draft and old unknown sticker are not replayed.
- Root97's sourcec077 compiled, but its normal connector candidate was rejected
  because the retained `AGENT_REPOS_PATH` contains literal `${HOME}`. R38 owns
  the supported same-root/inode-preserving placement and backup-budget repair.
  The failed buildd3d917d3 is terminal; its existence is not a running wait.
  A new actual R38 turn01a1320b was admitted, confirmed independently by Root97.
  Its supported accounting repair8edf9a4d has now passed a real UserIO ordinary
  consumer. Exmanager installation/business acceptance remains separate.
- Dedicated Telegram retirement remains **unproven at runtime**. Source wiring,
  native reads and a compiled candidate do not satisfy the final retirement gate.
  Preserve business135, memory, current identities, private readonly resource,
  provider client and rollback; only the Exmanager owner performs that cutover.

Retained Telegram native Vision/stream proof remains in Exmanager outcome
20261012-userio-native-vision-live.json: source18→SDK jobba0c0e5d succeeded→reply19
and three same-ID edits; actual PNG10 was read. This supports the Telegram
vertical, but final affected acceptance must follow the last migration/restart.
