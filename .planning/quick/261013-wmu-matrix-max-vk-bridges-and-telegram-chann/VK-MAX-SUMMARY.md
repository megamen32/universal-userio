---
status: delivery-verified-owner-decision-pending
---
# MAX/VK bridge acceptance — 2026-10-14

The authorized transport work is verified on the live owner Matrix API and
the actual MAX/VK self dialogs. Source: published main 65929e5; core VK SDK
overlay from 472c490. Ordinary Matrix/Telegram room settings were not changed.

Final frozen candidate: 12/12 native scenarios PASS in79.481s:
MAX and VK, text/image/file, Matrix->provider and provider->Matrix.
Native receipts and original provider bytes were checked. Matrix media has
real mxc URLs and correct m.image/m.file type; downloaded Matrix bytes equal
native provider bytes. Human outgoing media events remain visible after
native echoes. Native file messages do not create a second outgoing send.
Six original Matrix events replayed through the real AS transaction API:
same receipts, one durable draft each, pending0/failed0.

Relevant checks:29 bridge unit PASS;24 focused MAX service/media checks PASS;
4 VK native-upload refusal checks PASS. Ordinary verification remains below180s.
Tests are fast unit/focused integration; no broad suite or nightly/browser
was started. Evidence is real API/service state, not a health-only substitute.

Fixed:
- approved + native receipt is accepted, including late read-side reconciliation.
- send wait90s; accepted pending files recovered before consumed upload retry.
- all imported media carries io.userio.bridge; legacy unmarked media is ignored.
- repeated native own media echoes cannot redact or replace a human photo.
- native generic PNG/JPEG/GIF/WebP MIME becomes an actual Matrix image.
- empty/[file]/[photo] placeholders are hidden once actual media exists.
- unavailable old media has durable bounded backoff; fresh MAX file indexing
  retries reads in seconds, without repeating native send.
- a definite VK CDN HTTP405 upload refusal obtains one fresh slot; uncertain
  network writes are never silently retried.

Deployment: only the owned bridge module and VK SDK module changed; the
existing UserIO provider credentials/sessions/state were retained. Before SDK
deployment its runtime bytes matched the reviewed source base. Shared WIP in
server.mjs/messenger.py/call code was preserved. No new checkout/worktree.

Known limits: one media item per native message, max2MiB. MAX images use
the native FILE transport on egress; Matrix still renders original image
bytes as m.image. Reactions/calls/native delete synchronization are outside
this bridge release.

Historical consequences requiring the owner's decision:
The earlier missing media marker generated7 extra VK attachments in one
existing non-self dialog. Each duplicate and its source original were checked
against VK messages.getById: the owner, peer and native IDs match; originals
are present. Exact private targets are in reviewed-vk-duplicate-deletion-plan.json.
No native message was deleted. Removing these copies from both participants'
VK history needs separate authorization; next action is approve that exact
seven-message deletion or explicitly retain the history.
Two unsubmitted duplicate drafts were rejected only after proving they were
generated media echoes and had no native upload reference/receipt.
Five incorrect echo error notices were removed. Future echo suppression and
replay were verified; historical automatic sends remain audit evidence.

Private runtime evidence:
server-100 .tmp/matrix-cleanup/vk-max-finish-20261014/ contains rollback modules,
SQLite snapshots, final canary/replay, echo repair and reviewed deletion plan.
No credentials/private message bodies or attachment URLs are published here.

