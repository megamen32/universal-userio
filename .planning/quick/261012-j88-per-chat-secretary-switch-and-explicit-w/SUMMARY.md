---
type: quick
status: complete
completed: 2026-10-12
---

# Automatic analysis is a per-chat preference

Delivered normal web checkbox «Разбирать», visible conversation kind and
effective analysis indicator in both sidebar and search results. Existing
owner-scoped workspace allow/ignore rules are reused. Turning off keeps history;
turning on admits future arrivals and does not replay older ineligible events.
Personal read-only credentials cannot change these preferences.

The owner corrected the earlier folder-derived Telegram selection to named
work groups. Other accounts, direct-chat defaults, mail and bot defaults remain.
Private before/after rule evidence is retained outside tracked documentation.

## Verification

- Focused integration: old HTTP surface RED3; current HTTP/token cases GREEN33,
  33.34s; production web build GREEN8.15s. Combined bounded run71.45s/native0,
  peak305799168B/swap0/OOM0 and exact cleanup.
- Live web exposed quadratic SQL search: correlated unread counts for every
  scanned message exhausted the existing64 HTTP threads. Aggregate once per
  conversation fixes the cause; budgets remain unchanged. VM-instruction-bound
  regression RED1→GREEN6/4.90s/native0/cleanup. Real searches find the intended
  chat in1.39/1.44s; new core has3threads, not64 blocked requests.
- Search policy metadata regression: GREEN3/native0/cleanup. Existing web build
  reused; no new inference, sends or phone actions.
- Actual headed web4fe: owner login, temporary normal stealth suppression,
  normal search/open, checkboxON→reload/reopenON→OFF→reload/reopenOFF and group
  label PASS. Close conversation BEFORE restoring original auto_read=true;
  exactignore/OFF baseline restored. Owned target closed; own remote copies removed.
- Actual headed web640: normal account filter/search displays OFF group badge
  for excluded discussion and ON group badge for work chat. No chat opening,
  preference writes, mark-read, model or send action. Original reading=true
  unchanged; only owned target closed and own copies removed.

Published source:7fb3f05→4feef2c→640501d. Installed640501d,
manifest4ec3306e324b5f91e108aca6ac334a2333f8dcdcc7bce8b50e66a38e125b7227.
Runtime core3147418; host/controller limits unchanged.

Evidence: `.tmp/chat-analysis-control-20261012/`: work-policy-baseline.json,
live-readback.json, search-live-and-reading-restored.json, badge-live.json,
web-on-reload-off.json, web-baseline-restore-close.json, web-badges-final.json,
checkbox-off.png, badge-off.png and badge-on.png. Independent resource closure
is in Admin `.tmp/fleet-capacity-20261012/userio-badge64-300-window/release-proof.json`.

Earlier script failure and blocked-search browser window remain failed records;
they are not merged into successful acceptance. No old provider action repeated.
