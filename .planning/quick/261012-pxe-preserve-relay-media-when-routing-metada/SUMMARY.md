---
status: complete
date: 2026-10-12
---

# Relay media retained through actual UserIO consumer

Routing lacked an attachment index and SQLite replaced native media at slot0. import_projection now places routing after the highest native media index. No store/service schema changes.

Published source3477df9, installed manifestd9da882e/current core1231984 verified. Regression RED (first image missing), then21 tests PASS7.70s, bounded helper0/64.7MiB/swapOOM0/full same-generation cleanup.

Real existing QA source telegram881/peer-1009000002001/message10: before media=[], after generic resolve returns image5361575419839556313; generic media returns image/png90835bytes/SHA d19588260b4e9521b5d3e880447bf57297910c5a971ac270412a455b70033a9e, equal to provider original; stored history retains it. No new message/model intent or read acknowledgement. Protected evidence .tmp/relay-media-20261012/{before-live,after-live,verify}.json.

Actual native Secretary model/media QA remains owned independently by Exmanager; this proves only the repaired UserIO resolve/media/history path. VK ordinary-user onboarding remains active.
