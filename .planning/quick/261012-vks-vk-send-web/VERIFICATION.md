# Verification — PASS

Claim: normal UserIO VK web text sends, reaches the connected account's actual
self peer and remains visible after reload. Accepted external write count:ONE.

Tested final source 7d04cc698a1bef20fab00ffb1335ef02887d780a; exact installed manifest dce2f91b407ea7aadb4a5142e5580b80bec15f59b613eb9ce3834fffefe290fb,
core 2829089; verified=true.

Protected evidence under .tmp/vk-send-20261012:
- headed-web-trace.json: actual normal input/ONEclick, provider7 and reload.
- native-vk-send-proof.json: separate native getById exact body/peer/author/outgoing.
- headed-web-final-readonly-trace.json + final-web-verdict.json + vk-web-final.png:
  actual final reload/one history DOM bubble/delivery caption/no duplicate card.
- status-verify.json: exact installed source/services.
- final-own-browser-cleanup.json: PID/CDP/profile/auth/copies/control absence.
- max-release-output.json:47PASS9.96s/native helper0/cleanup.
- max-reconnect-live.json + max-read-after-idle.json: exact new Darya sticker
  read/resolve90258B SHA3d020f00451bf787853cfec1612306fefdc476a4b5d832b76aa535244761775a.

The first headless gateway was rejected as the final surface; stale page
selection/close errors are retained in m1-* diagnostics. Its selected page3
close could not be independently confirmed. No shared gateway/browser restart
or URL-based foreign close was attempted. Standard live acceptance instead used
an isolated, bounded foreground headed browser on the same authorized M1 route.

False preliminary auth/account loading assertions and global-body duplicate
count failures are preserved. They preceded a send or were read-only; no
UNKNOWN provider action was repeated. Test categories: fast unit(stale socket,
expected5/max20); focused integration(VK manual HTTP send/receipt/tenant/dedup,
expected15/max60); no slow nightly coverage required for this bounded slice.

Exmanager native MAX journey and transport retirement remain independently
owned by Root97 and are not claimed complete by this UserIO delivery.
