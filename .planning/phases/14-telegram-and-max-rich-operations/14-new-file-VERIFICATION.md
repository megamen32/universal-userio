---
phase: 14-telegram-and-max-rich-operations
status: native_consumer_pending
verified: 2026-10-12
---

# New-document UserIO extension

Owned source `1b44cb43da5b965b0622882db517438eabe8e33e` published and installed;
manifest `df86a86424807b55c20f3520ece154031d45051de14ec4b47a97c0d3635b837b`,
core1971342. Same existing sender accounts and credentials; private read-only
access remains unchanged. No account/session cloning.

## Verified

- 46 focused checks passed in15.58s, native helper0, full same-generation
  cleanup, peak87,781,376 bytes, swap0/OOM0. Existing reference actions retained.
- Independent review found two defects before live delivery: staging metadata
  identity was not checked; MAX could return to proposed after accepted upload
  but failed send reconnect. Both fixed with direct regressions.
- Normal live MAX MCP upload→file proposal→one exact approved action delivered
  a new document to SavedMessages0. New provider ID117429027634638960;
  action_db7d05ef5a60766822251e3418ff64d66281eb8d2cbe7b8ee95c34f22c00dcb1.
  Native download returned203 bytes with SHA256
  954eba9118b68d7286561171f0d84cdd412eb5d9a5abc8aba417a0aa807fed39,
  equal to the original new bytes. No model/readACK/other-account action.
- Accepted staging bytes were scrubbed; durable action and receipt retained.

Protected evidence is in `.tmp/messenger-upload-20261012/`:
`source-test-green-output.json`, `verify.json`, `max-live-receipt.json`,
`staging-cleanup-proof.json`. Setup refusal (wrong local interpreter) and one
error-string mismatch are retained separately; they are not counted as passes.

## Remaining native acceptance

Exmanager owner01a11197 owns SDKStorage extraction and relay881 new-file
execution/capability advertisement. Installed UserIO contract and actual MAX
byte receipt were handed over once. The existing native Telegram text/vision/
private/reminder acceptances remain accepted and are not replayed.

This UserIO live delivery does not replace the final Secretary native exported-
document Telegram/MAX journey. MAX incoming actor journey and full legacy
transport retirement remain phase15 work. No whole milestone completion is
claimed and no foreign business task is paused.
