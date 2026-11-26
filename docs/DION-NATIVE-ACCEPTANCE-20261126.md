# DION native candidate: acceptance evidence and remaining gates

This is a candidate, not installed or live-accepted functionality.

## Reproduction and fixes

- Refresh durability: rotating the one-time refresh credential followed by an
  identity-service outage left the old credential persisted (RED). The broker
  now persists the new generation before identity verification, then validates
  that pending identity on reuse, including after a backend restart (GREEN).
- Concurrent refresh: the broker uses an account/owner-scoped file lock across
  processes. Rejection-driven retries carry the rejected access token so a late
  401 cannot consume a chain another request already rotated. The process race
  fixture observes exactly one refresh and generation 2 in both consumers.
- Ambiguous refresh: a durable pending marker prevents replay of a potentially
  consumed credential after a network failure or process crash. It is not
  mislabeled as provider-confirmed reauthentication. This conservative state
  requires a fresh credential handoff if no provider reconciliation is available.
- Call setup: explicit `auth_expired` previously failed without broker recovery
  (RED). A single access-only retry now joins the already-created room using its
  slug instead of creating another room (GREEN). Unknown outcomes are not retried.
- Chat retry: the HTTP 401 path omitted the rejected access token (RED); both
  handshake and request refresh callers now supply it (GREEN).

## Checks

The final focused auth/chat/call/crypto/protocol suite passed all 29 tests,
including the chat rejection regression. These are fixture/storage/child-process
checks, not live provider acceptance. Pytest emitted an existing unknown-config
warning for `asyncio_default_fixture_loop_scope`.

Additional checks prove a temporary broker outage is not mislabeled as
`reauth_required`, and native polling stores exactly one message across a SQLite
backend restart while reusing the persisted overlapping history cursor.
`go test -race ./...` passes in `tools/dion-call-helper` (cached).

## Current external-state check

A read-only query of the installed UserIO database found zero DION provider
accounts. The explicitly authorized M1 Chrome default profile is connected;
an owned DION tab reached `/home` with authenticated home/history controls.
That tab was closed afterwards. No credentials were extracted, no refresh was
performed, and no messages or calls were sent. Therefore missing browser login
is not an established blocker: the missing transition is the supported auth
handoff into UserIO, with exclusive refresh ownership.

The current extension source contains no DION handoff implementation (`rg dion
extensions/vk-inbox` has no matches). Its files, `account_connections.py`,
`http_api.py`, and the connect UI remain explicitly reserved to the owner by the
earlier task allocation. No other active DION OpenChamber session was found by
the scoped session lookup; this does not revoke the owner's file reservation.
The owner subsequently explicitly released that reservation in this session.
The candidate extension now implements account handoff and explicit reauth, with
a durable browser refresh block, backend generation acknowledgement, and no
extension credential persistence. The backend response is the existing flat
`201 {accepted, id, state, generation, ...}` account contract. Four new extension
tests pass; the existing connection/bridge tests also pass (9 total). The combined
DION/account-connection Python run passes 42 tests. HTTP/MCP regression tests
pass 62 cases and reproduce three unrelated failures against clean committed
HEAD as recorded in `bugs.md`.

The candidate `bbac5b04` was pushed to the task branch. Extension 0.9.39 was
installed through the M1 chrome-default native extension controller; live get
confirmed its version, enabled state and declarativeNetRequest permission.
Browser inspection of its popup failed with Chrome's cross-extension restriction;
the same-tab screenshot failed for the same reason. HCU AX then failed with
`state_unavailable`; its captured desktop screenshot was retrieved and inspected,
but showed another foreground application, not the owned popup. It is not popup
acceptance evidence. No handoff or token extraction has been performed.

A subsequent focused RED/GREEN regression prevents `refresh_pending` accounts
from returning refresh ownership to the browser. Five handoff tests now pass.
This correction is versioned 0.9.40 and is not yet installed on M1.

The next browser-side backend probe ended `OUTCOME_UNKNOWN` because Chrome
disconnected after dispatch (create-tab request `dion-backend-account-20261126`).
The original GPTAdmin task was reconciled to completion; its nested browser
receipt, not the outer task status, contains that failure. A subsequent live
`browser_status` returned no browsers. Do not repeat that create blindly: inspect
tabs/command receipt after reconnect. No auth import, provider refresh, messages
or calls were requested. The default-profile popup lease may have expired and
must be reclaimed before use. Remaining acceptance is blocked at browser
reconnect and supported UserIO connection setup, not at the earlier file scope.

Chrome was subsequently launched normally in the exact Default profile and all
three browser connections returned. A bounded tab list found no earlier owned
popup/backend tab, establishing no remaining created-tab effect. A fresh linked
backend tab reached the existing authenticated `/accounts` page. The initially
guessed `/connect-accounts` route returned 404; its same-tab screenshot was saved
and inspected before navigating to the source-confirmed `/accounts` route.
The installed account page has no DION controls yet, consistent with its older
backend revision. The next gate is isolated candidate packaging/live readback,
not missing UserIO login. No owner password or manual QA is required.

## Public protocol evidence

Downloaded only public web-client JavaScript to
`/tmp/opencode/dion-public-chat-source`; no browser session credentials were read.
The current manifest includes `7403.d0c0e99c.js` (wire DTO converters),
`3448.fd4c9a5a.js` (generated personal HTTP API), and `5232.09930998.js`
(history and batch-message callers). These confirm snake-case message fields,
history `before`/`after`/`limit`, batch `conversation_id` and `messages`,
`intermediate_id` receipts, and `rich_text`/`text` formatted content.

## Remaining acceptance gates

- Confirm the integrated browser bootstrap relinquishes refresh ownership only
  after acknowledged import; preserve the owner's reserved backend/UI/extension
  files and concurrent changes.
- Exercise an authorized connected account through UserIO list/read/sync with
  browser absent, then repeat after backend restart and verify cursor persistence.
- Exercise approved outbound delivery and uncertainty reconciliation only in an
  isolated authorized test conversation; no real-person messages were sent.
- Verify WS behavior and pagination at provider boundaries rather than treating
  fixture compatibility as complete provider support.
- Prove installed revision and real consumer readback after the supported release
  path. No deployment or live Pion/ICE acceptance has been performed.
- Do not place DION acceptance calls to humans. Standing voice-test authorization
  allows only isolated Matrix bot-to-bot tests; DION fixture checks do not override it.

The working tree contains substantial concurrent uncommitted integration work.
Do not blindly commit or overwrite it. Latest committed helper implementation
remains `17f85c25` at the time this evidence was recorded.
