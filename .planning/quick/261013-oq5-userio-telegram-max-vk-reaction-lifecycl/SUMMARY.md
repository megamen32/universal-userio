---
status: complete
source_commit: 23a4cda
consumer_scope: UserIO providers, not Secretary behavior
---

# UserIO reactions: delivered and live-verified

Added personal VK reactions to generic Messenger using the existing connector,
source-bound approved actions and native conversation_message_id. Native artwork
and current catalog verified ❤️/🔥/😂/👍; empty removes this sender only.
Reply/edit/media/typing generic VK are explicitly unsupported; existing web
send/repeated-edit/photo/document functionality remains independently accepted.
Explicit preflight refusal is now reported accurately instead of as UNKNOWN.

68 regression checks passed42.33s, then4 changed focused checks10.07s. Native
bounded jobs/helper0, swap/OOM0, same-generation cleanup confirmed; scopes absent.
First source RED2fail and later cached test-fixture binding RED retained; fixed
lazy lookup and exact source-drift checks. Ordinary combined run under180s.

Published/installed23a4cda, manifest6412769b762dc589cdd33ab1a3a7d415ee71c964c327a5b07b8eba33c28f5e5b,
verified actual core3225943 and phone ingress3225973 active/restarts0 before canary.
No runtime changes occurred during final VK/TG captures.

Live generic operations:
- Telegram881 / QA-1009000002001 / source24: 👍 then🔥 thennone, independently
  read by Telegram Helper with sender881. Add228a/change0c9b/remove accepted.
- MAX<owner-max-id> /900000105 /ownedfile117429938010356944: native counters👍1→🔥1→none,
  actionsb307/8a05/1f63 accepted.
- VK200000000106 /bezrabotniq46887791 /source8, nativecmid1:
  ownreaction4/👍→2/🔥→0, nativeauthor200000000106, actionsf0b2/6cb0/7afb accepted.

All original reaction baselines restored; no new messages/models/read ACK.
Telegram initial change/removal proposals0996/9117 were refused before mutation:
actual edited_at1791902998 differed from stored0. Exactsource resolve exposed
that drift; fresh immutable proposals succeeded. Old refused proposals were not
replayed. The source protection remains intact. Do not attribute this field drift
to a specific Telegram update mechanism without additional protocol evidence.

Private full receipts: .tmp/reactions-20261013/{live-tg-max,live-tg-fresh,live-vk}.json.
Proof distinguishes source tests, installed runtime, exact provider acceptance and
independent Telegram recipient view. No new credentials/auth/browser/phone used.

Secretary natural/automatic use was delivered as a separate work packet to its
owning Root97 active turn01a135b1. Their native behavior acceptance remains their
work; UserIO source/runtime/QA are released and do not wait for it. Old transport
retirement remains excluded. VK read ACK/renewal and recipient typing remain open.
