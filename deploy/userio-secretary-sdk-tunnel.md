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

## Primary88 Matrix private control export

The same existing SDK SSH transport may export the primary88 gateway Unix
control socket to server100. Only on `roomhacker-server-88`, set the existing
unit's environment `USERIO_PRIMARY_CONTROL_REVERSE_SOCKET=/run/agentcall/primary88/control.sock`.
The published helper adds exactly one SSH `-R` to its existing process while
preserving the SDK `-L` and protected references; no second caller, daemon or
public port is created. Other hosts reject this setting.

The runtime owner must first confirm the actual88 primary control socket and
an idle resource join. On100 provision only `/run/agentcall/primary88` for the
existing SSH service user, mode0700, inside the backend's existing `/run/agentcall`
mount. Keep the forwarded socket private and validate its owner/mode after bind.
The tunnel process on88 also needs the existing `notification-center` group to
connect to its local `root:notification-center` mode0660 AgentCall socket. Add
that group only as a supplementary group on this unit; do not widen the socket
mode or add the login user to the group globally.
Do not weaken `/run/agentcall` or replace its local100 socket. A stale forwarded
socket is not proof of a live route: reconcile the existing SSH session, then
remove only its owned stale socket at an idle handoff before one tunnel restart.
Do not enable broad StreamLocalBindUnlink or overwrite foreign sockets.

Backend private config `/etc/userio-containers/matrix-call-control.json` (root or
backend service owner, mode0600, already-mounted directory):
```json
{"socket_path":"/run/agentcall/primary88/control.sock","execution_host":"roomhacker-server-88"}
```
Once configured, all new Matrix calls use it and invalid/unavailable remote IPC
never falls back to100. Explicit Notice Place policy calls require this config.
Their route file defaults to `/etc/userio-containers/noticeplace-matrix-routes.json`,
so no backend environment/HostConfig change is necessary. Source support alone
neither provisions these files nor authorizes any call.

The typed request transfers the admitted record, account credential/identity
snapshot, RTC endpoint and per-user AI settings over SSH;88 does not need a
replica of UserIO SQLite or another account registry. The fixed worker uses88's
existing installed voice config/adapter and writes media to88's existing archive.
Events return to100, which owns the durable call receipt and normalized history.
Remote receipts expose `recording_host`; backend100 does not infer audio bytes
from an unrelated local archive. Serving those remote recordings through the
backend audio UI is a separate archive-delivery concern, not answered-call ACK proof.

Rollback: while idle, remove only this owned route configuration and reverse
export from the existing transport, then restore the prior backend release via
its existing receipt procedure. Explicit policy calls fail closed without the
primary route; do not replay accepted/UNKNOWN receipts or flip to owner defaults.
