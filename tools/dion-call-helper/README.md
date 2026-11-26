# DION access-only call helper

Build and test from this directory with `go build .` and `go test -race ./...`.
The module pins independent `kulikov0/whitelist-bypass` source, not WhiteTransport.

Python starts one child per call lifecycle with private stdin/stdout pipes.
Send one JSON object per line. Never put credentials in argv, logs or files.
Each command requires `request_id`, `account_ref` (`dion:<org_id>:<user_id>`),
`org_id`, `user_id`, and `action`. Account identity is supplied by the verified
Python broker; parsing a JWT expiry does not verify identity or its signature.

- `create`: requires `access_token` (JWT with `exp` more than 60 seconds away).
  Creates the room and starts upstream `dion.Call` with `RoleCreator`.
- `join`: requires fresh `access_token` and `room_id` containing the room slug
  (or upstream-supported DION event URL). Starts `dion.Call` with `RoleJoiner`.
  Both actions return `connected`, `room_id` and `room_slug` only after Start
  succeeds and `OnConnected` supplies a non-nil DataTunnel (Pion/ICE/VP8 core).
  The response `room_id` is the provider event ID, not necessarily the input slug.
- `leave`: requires the same account and the returned event ID as `room_id`.
  No token is needed to close the active Call and its tunnel. Returns `closed`;
  this is local teardown, not an independently observed remote departure.

EOF also closes the current connection. Unknown fields, including refresh
tokens/passwords, are rejected without echoing their values. The ephemeral
upstream cookie jar is never persisted. An HTTP transport allowlist blocks
login, refresh and registration before network I/O, intercepts HTTP 401 before
upstream auto-refresh, disables redirects and enforces a request timeout.

`auth_expired` means Python must obtain a fresh access token from DionAuthBroker
before retrying setup. If the response includes a room ID/slug, creation already
succeeded: retry `join` with that slug, never `create`. `unknown` means reconcile the original operation; do not
blindly replay room creation. Provider errors are not printed or serialized.
Request IDs correlate responses; they are not a persistent idempotency store.

The helper retains Call and DataTunnel until leave/EOF; the obfuscation secret
is derived from the returned event slug for both roles. There is no UserIO audio
bridge or public tunnel data IPC: upstream provides a VP8 data tunnel, not speech.
The selected upstream Call retains its participant-kicking behavior; only use
authorized isolated tunnel rooms, not ordinary shared meetings. Tests exercise
actual upstream HTTP methods with an in-memory transport and an injected Call
core, including delayed/missing OnConnected and teardown. They do not prove live
ICE connectivity. No real participant is called.
