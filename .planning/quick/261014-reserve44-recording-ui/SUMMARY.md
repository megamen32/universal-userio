---
status: complete
verdict: PASS
scope: selected-existing-reserve-host-recording
---

# Reserve-host recording accepted in the live web interface

The selected automatically imported phone recording shows two independent players labelled **Собеседник** and **Телефон**, with both transcript texts. Its 45.57575-second audio played through a real mouse event; both timelines advanced from about 0.57 to 1.88 seconds without decode errors. Reload and normal account/conversation reopening retained both tracks and the exact transcript contents; playback after reload also succeeded.

Both **Скачать WAV** controls produced actual browser downloads, each a valid 729256-byte WAV. Their separate SHA-256 values exactly match the imported original archive tracks. This is a browser-download proof, rather than an HTTP probe replacing the UI action. The decisive screenshot was inspected.

The installed UserIO service stayed active with the same PID and restart count. No source, account, preference, message, model or new-call action was executed. A target-specific origin/request guard withheld all non-read requests, including the UI's automatic reading/settings POSTs; this acceptance does not claim the default reading/settings flow has no writes.

The existing bounded headed browser finished with helper exit 0, peak 348762112 bytes, swap 0 and no OOM. Its unit, cgroup, Chrome/Xvfb processes, private authorization config and downloaded copies are absent. The browser authorization interceptor was cleared. Foreign browsers/processes were untouched; the remote screenshot copy was removed, and protected local evidence remains in `.tmp/reserve44-ui-20261014/`.

Evidence: `web-proof.json`, `archive-byte-match.json`, `native-result.json`, `cleanup-proof.json`, and the inspected `call-audio.png` in that ignored private directory. The telephony owner received the actual scoped acceptance. No other recording, new call, other messenger transport, or full Secretary milestone is asserted by this slice.

Unrelated dirty/untracked Telegram transport work is preserved and excluded from this publication. Only this acceptance plan and summary are owned here.
