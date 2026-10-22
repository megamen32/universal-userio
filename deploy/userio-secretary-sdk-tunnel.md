# Private native Secretary transport for UserIO Voice

This is a transport for the existing signed SDK routes, not a caller, agent,
model router or scheduler. It preserves the fixed app JWT, application caller
and HMAC. It binds only `127.0.0.1:18086` on the voice host. Public app-route
admission remains unchanged.

The systemd unit runs as `roomhacker` through existing SSH trust to server100.
Every supervised start resolves the exact native container
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

The native ExManager deployment owner must run this existing supervisor refresh
step after the fixed app has been replaced and its new token is ready, before
calling the deployment complete:

```bash
ssh -o BatchMode=yes 44 sudo -n systemctl restart userio-secretary-sdk-tunnel.service
```

Read back the protected `upstream.json` metadata and compare its container/image
identity to the current fixed app, then perform the signed read-only SDK status
probe. Refreshing the tunnel does not restart the voice gateway or replay a
call/turn. Coordinate an active call before refreshing its transport; an admitted
unknown SDK action remains unknown and must be reconciled by its original receipt.

Systemd reconnects and resolves again when SSH fails; keepalives detect broken
SSH. Startup resolution alone does **not** detect an otherwise healthy SSH
connection whose app IP/token changed. This deployment hook is therefore required.
There is no automatic new scheduler. Until the native deployment workflow has
integrated this hook, future app replacement requires that exact owner operation;
do not claim permanent automatic app-replacement resilience.

Rollback: stop/disable only this task-owned transport, restore saved voice files,
protected config and owner greeting bytes, restore the prior gateway unit/drop-in,
then restart the idle gateway. Preserve call ledgers and unknown receipts.

Native SDK v0.8.1-alpha.12 compares the configured app bearer as an opaque
value; it does not parse its JWT expiry on this direct endpoint. The transport
does not impose a new expiry availability gate. Refresh follows actual native
app replacement/token rotation, not an inferred JWT deadline.
