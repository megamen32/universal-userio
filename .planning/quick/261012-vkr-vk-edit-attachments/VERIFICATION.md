# Acceptance — PASS

Final tested published sourcefb6e584; actual installed manifest/core identity:
.tmp/vk-rich-20261012/final-verify.json. All intentional backend restarts completed
before final read-only web capture. Native recipient46887791, connected sender
200000000106; external accepted writes:3sends and2edits, no oldprovider replay.

Protected real evidence .tmp/vk-rich-20261012:
- first-web-trace.json/first-web-intent.json: normal web send8→sameID edit→edit,
  photo9 accepted, initial document upload failure before message-send retained.
- second-doc-web-trace.json: distinct document10 accepted and retained on reload;
  real CDN redirect defect captured, not fake download acceptance.
- final-web-trace.json + live-proof.json: final actual reload/reopen8/9/10,
  normal document download3682B/exact SHA and photo preview360px.
- vk-rich-final.png/vk-attachment.png: real visible browser surface.
- own-cleanup.json: own PID/profile/auth/remote artifact cleanup.
- first-unsubmitted-qa-cleanup.json: exact first unsubmitted draft cleanup;
  no native delete or retry and no private upload acceptance claim.
- download-output.json/tests.txt:67PASS24.41s/helper0/same-generation cleanup.
- native-chat-label-reconciliation.json: verified native profile labels restored
  only for existing owned account chats; no guessed identity/history changes.

Per-category purpose: fast unit protects native edit/upload fields and read
redirect separation(expected10/max30s); focused integration checks HTTP file
staging, tenant/source binding, unknown/no replay, two edits, native metadata and
bytes(expected20/max60s). Ordinary combined hard180s including setup. No new
nightly coverage needed.2MiB file limit retained; community/VKID-only/read-only
accounts are not upgraded. VK reactions/read acknowledgements remain unsupported.

Independent simulated reader PASS for repeat edit discoverability, clickable
photo/doc and sent state after reload. Direct normal browser/native proofs
support acceptance; source and test GREEN alone were not used as completion.
