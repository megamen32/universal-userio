# MV3 Z.AI live acceptance evidence — 2026-11-09

Browser host: mac-mini-2012.lan, real Chrome profiles Default and Carevio.
Both Universal UserIO Agent service workers reached 0.9.23 and then 0.9.24
in their own distinct extension storage and agent_instance_id.
Authenticated Z.AI via same-origin /api/v1/auths/ on both profiles.
Default account enumerated 43 conversations, Carevio 0. Account scopes were
independently verified using SHA-256 and never logged as raw user identities.
Default native Z.AI chat read returned 17 messages with exact conversation ID,
a latest user and ready=false on a latest unanswered turn.

Both profiles proved the full read-only UserIO command channel roundtrip:
queued zai_scope, pinned browser UUID, same principal, exact command receipt,
valid SHA-256 scope. Neither command used Vibe nor direct CDP.

Isolated disposable Z.AI judge canary:
- First attempt rejected before Send with judge_tab_load_timeout because Chrome
  reported a newly loaded Z.AI tab as loading despite later showing the UI.
  No model click occurred. Regression was fixed in 0.9.24 to probe DOM
  readiness without trusting tab.status alone.
- A second isolated one-shot judge command was accepted by the extension
  but returned zai_judge_outcome_unknown. A fresh Z.AI conversation route
  was observed afterward. The outcome is NOT a confirmed native delivery,
  and no retry, failover or production activation is authorized for that
  request without read-only reconciliation.
- Do not claim a complete live GLM answer, native user/assistant receipt,
  or enabled production autopilot until a separate accepted canary proves it.

Safety invariants: every judge command carries one-use request_id and exact
account scope; a browser extension can never use another profile's credential;
the extension only creates its own background judge tab and quarantines unknown
receipts; no live user conversations were intentionally modified.

Source feature branch: feat/zai-userio-mv3-20261109.
Herder feature branch: feat/zai-userio-autopilot-20261109.
Both branches are separate from protected main. UI-side Z.AI transport through
Vibe was refused in configuration; CDP remains an explicit compatibility route.
