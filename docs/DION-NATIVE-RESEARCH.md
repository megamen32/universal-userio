# DION native implementation research

The independent upstream `kulikov0/whitelist-bypass` repository exposes the
headless call pieces under `relay/dion` and `relay/headlessapi`. The useful
provider seam is concrete: `dion.Session` owns authenticated HTTP, refresh,
`ConnectWSS`, event lookup/create, and `dion.Call` owns signaling plus Pion
media. UserIO must copy or depend on that upstream implementation directly,
not WhiteTransport.

Observed upstream endpoints include `/platform/v1/auth/refresh/web`,
`/platform/v1/whoami`, `/conference/v1/events/slug/{slug}`,
`/conference/v1/events`, and `/conference/v1/connect/wss`. These are conference
surfaces, not evidence of a private personal-chat API. No chat endpoint is
implemented from guesswork.

`tools/dion-call-helper` is the initial UserIO-owned process boundary. It
validates request identity and fixture-only create/join/leave actions, but does
not contact DION yet. This prevents accidental real calls while the upstream
dependency and UserIO call lifecycle are wired. The next implementation step
is to replace the accepted stub with the upstream `relay/dion` adapter and
retain the fixture gate in tests.
