# Human web send and MAX edit

Owner: existing UserIO session01a11747-7bb9-7bc0-8c0a-ce64db49105d.

User acceptance: writing in the web composer and pressing Send/Enter sends
immediately. Only AI suggestions need draft confirmation. A sent own MAX text
can be edited in place and survives reload with the same provider message ID.

Reproduced user failure: screenshot "draft is not approvable", plus actual
BrokenPipe on the locally-connected idle MAX socket and an unreleased sending
claim. Native complete history verified the original failed intent absent;
its text was preserved and its claim safely released, provider sends zero.

Candidate: daa2d758. POST /v1/conversations/{id}/send consumes human web intent
with durable request ID and normal claim/receipt. No AI approval policy change.
Fresh MAX authentication happens before outgoing write; only a proven preflight
failure releases the claim. Uncertain provider acceptance never auto-retries.
PATCH /v1/conversations/{id}/messages/{id} validates own stored/native plain text,
uses native opcode67, reads authoritative message71 and updates the same mirror
origin. Media editing stays explicitly unavailable instead of dropping media.

Checks (no provider writes):
- fast unit: fresh idle socket/preflight, native edit fields/conflict/SDK ref;
  expected1s/maximum10s;
- focused integration: HTTP human session/send, durable idempotency and pending
  state, same-ID mirror edit/no arrival, concurrent approval; expected3s/max30s;
- compatibility: existing MAX and generic delivery tests, expected15s/max45s;
- frontend production build/typecheck expected15s/max90s. Final build RSS277MiB,
  swap0; preserved failed2GiB address-space attempt, successful8GiB virtual bound.
- slow nightly: no new scenario required for this finite text journey.
Ordinary source/check cycle remains below180seconds.

Real acceptance, after install/restart freeze: one leased visible browser,
authenticated real msg.bezrabotnyi.com, MAX Saved Messages only; normal Send,
edit that exact message, normal Enter, native provider readback/id counts,
UI reload/screenshot. Maximum180seconds for useful journey, sole browser lease
maximum600seconds including capture/cleanup. Only owned tab closes afterward.
No original contact send, old accepted canary replay, new account authorization,
shared daemon restart or foreign browser/session change.

Source and runtime delivered; browser consumer still pending at plan write.
