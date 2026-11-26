# DION access-only call helper

Build and test from this directory with `go build .` and `go test -race ./...`.
The module pins independent `kulikov0/whitelist-bypass` source, not WhiteTransport.

Python starts one child per call lifecycle with private stdin/stdout pipes.
Send one JSON object per line. Never put credentials in argv, logs or files.
Each command requires `request_id`, `account_ref` (`dion:<org_id>:<user_id>`),
`org_id`, `user_id`, and `action`. Account identity is supplied by the verified
Python broker; parsing a JWT expiry does not verify identity or its signature.

- `create`: requires `access_token` (JWT with `exp` more than 60 seconds away).
  Returns `created`, `room_id` and `room_slug`. This creates a room only; it
  does not join. Persist the receipt before sending a separate `join` command.
- `join`: requires fresh `access_token` and `room_id` containing the room slug
  (or upstream-supported DION event URL). Returns `signaling_joined` only after
  the provider `server:you_joined` notification. The response `room_id` is the
  provider event ID, not necessarily the input slug.
- `leave`: requires the same account and the returned event ID as `room_id`.
  No token is needed to close the local signaling connection. Returns `closed`;
  this is local teardown, not an independently observed remote departure.

EOF also closes the current connection. Unknown fields, including refresh
tokens/passwords, are rejected without echoing their values. The ephemeral
upstream cookie jar is never persisted. An HTTP transport allowlist blocks
login, refresh and registration before network I/O, intercepts HTTP 401 before
upstream auto-refresh, disables redirects and enforces a request timeout.

`auth_expired` means Python must obtain a fresh access token from DionAuthBroker
before retrying setup. `unknown` means reconcile the original operation; do not
blindly replay room creation. Provider errors are not printed or serialized.
Request IDs correlate responses; they are not a persistent idempotency store.

This helper currently joins **signaling only**: no SDP/audio/video bridge is
implemented. It deliberately avoids upstream `Call` and its participant-kicking
VP8 tunnel behavior. Tests exercise actual upstream HTTP methods through an
in-memory transport and a fake signaling peer. No real participant is called.
