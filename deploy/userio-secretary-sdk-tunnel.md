# Private native Secretary transport for UserIO Voice

This is a transport for the existing signed SDK routes, not a caller, agent,
model router or scheduler. It preserves the fixed app JWT, application caller
and HMAC. It binds only `127.0.0.1:18086` on the voice host. Public app-route
admission remains unchanged.

The systemd unit runs as `roomhacker` through existing SSH trust to server100.
At startup and every ten seconds the supervisor resolves the exact native container
`airlock-dev-agent-c8b0c2c6-dc99-463f-baed-ff049b455be8`, validates its current
private IP and app-token identity, and atomically refreshes only
`/etc/agentcall-secretary/voice.token`. That directory is owned by the service
user, mode0700; token, signing key, identity map and upstream receipt are0600.
The root voice gateway can read them. No token is put in argv, unit, logs or
source. The existing protected HMAC key and exact identity map are provisioned
separately; the transport does not grant identities or roles.

The protected `/etc/agentcall-secretary-voice.json` uses:

```json
{
  "EXMANAGER_VOICE_URL": "http://127.0.0.1:18086",
  "EXMANAGER_VOICE_TOKEN_FILE": "/etc/agentcall-secretary/voice.token",
  "EXMANAGER_VOICE_SIGNING_KEY_FILE": "/etc/agentcall-secretary/signing.key",
  "EXMANAGER_VOICE_IDENTITIES_FILE": "/etc/agentcall-secretary/identities.json"
}
```

After staging published files, start the transport, verify a signed read-only
status response through it, then activate the gateway only after an idle-call
guard and backups. Preserve the existing guest WAV, provider/MCP settings,
SSH trust and service environment. Gateway replacement is an explicit native
release action; do not recreate the unrelated server100 voice container.

## Native-app deployment hook and exact limitation

The same supervisor refreshes the fixed app credential reference within a bounded
10-second polling interval plus the SSH read timeout. A changed app IP reconnects
only after the local Asterisk idle guard passes. This is one transport supervisor,
not a general scheduler. A failed resolution preserves the existing transport and
prints a secret-free state change; it never replays a voice request.

For immediate supervised refresh during an already idle native deployment, the
ExManager deployment owner can also use:

```bash
ssh -o BatchMode=yes 44 sudo -n systemctl restart userio-secretary-sdk-tunnel.service
```

Read back the protected `upstream.json` metadata and compare its container/image
identity to the current fixed app, then perform the signed read-only SDK status
probe. Refreshing the tunnel does not restart the voice gateway or replay a
call/turn. Coordinate an active call before refreshing its transport; an admitted
unknown SDK action remains unknown and must be reconciled by its original receipt.

Systemd reconnects and resolves again when SSH fails; keepalives detect broken
SSH. The bounded identity recheck also sees a healthy SSH connection whose app
IP/token changed. The wrapper rereads the protected token file for each SDK
request. A running voice gateway is never restarted by this supervisor. Pending
IP replacement is recorded as pendingIdleReconnect until the call guard is idle;
an uncertain admitted SDK turn remains subject to ordinary receipt reconciliation.

Rollback: stop/disable only this task-owned transport, restore saved voice files,
protected config and owner greeting bytes, restore the prior gateway unit/drop-in,
then restart the idle gateway. Preserve call ledgers and unknown receipts.

Native SDK v0.8.1-alpha.12 compares the configured app bearer as an opaque
value; it does not parse its JWT expiry on this direct endpoint. The transport
does not impose a new expiry availability gate. Refresh follows actual native
app replacement/token rotation, not an inferred JWT deadline.
