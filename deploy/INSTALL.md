# Deployment

1. Install this checkout at `/opt/universal-userio`.
2. Create `/var/lib/universal-userio` and root-owned `/etc/universal-userio.env`
   (`0600`) from `.env.example`. Create a separate mode-`0600`
   `.env.owner-seed` with `USERIO_SEED_USERNAME` and
   `USERIO_SEED_PASSWORD`, or point `USERIO_OWNER_SEED_FILE` at it.
3. Install `deploy/universal-userio.service`, run `systemctl daemon-reload`, then `systemctl enable --now universal-userio`.
4. Enable the native UserIO ingress units required for this host (`userio-gmail-ingress`, `userio-matrix-ingress`, `userio-sms-ingress`, or provider sidecars). Universal Inbox is retired and is not a runtime dependency.

The service is loopback-only by default. Publish the dashboard only through an
HTTPS reverse proxy; UserIO itself redirects anonymous browsers to `/login`
and scopes the dashboard session to the authenticated user. Do not copy
provider cookies, browser profiles, or raw NoticePlace credentials into UserIO.
If the proxy uses the dashboard trust header, configure
`USERIO_TRUSTED_PROXY_TOKEN` and make the proxy overwrite both
`X-UserIO-Authenticated` and `X-UserIO-Proxy-Token`; an unkeyed header is not
accepted.
The same HTTPS proxy may publish `/mcp` for ChatGPT; do not expose the
loopback listener directly. Give each connector its own user bearer token.

## Exact release identity

Production deployment owns `/opt/universal-userio/.userio-release.json`. It
must be a root-owned, mode-`0644`, regular file with exactly this public schema:

```json
{
  "schema_version": 1,
  "commit": "0000000000000000000000000000000000000000",
  "manifest_sha256": "0000000000000000000000000000000000000000000000000000000000000000"
}
```

`commit` is the verified 40-character commit published to the owned remote;
`manifest_sha256` is the digest produced by the deployment manifest. The
runtime never infers either value from `/opt/universal-userio/.git`, because
that checkout may contain classified local state and old Git metadata.

After authentication, `GET /v1/runtime` returns only `schema_version`,
`commit`, `manifest_sha256`, and `verified`. Development instances without a
valid release file remain available but return `verified: false`. For a local
manual probe, put the authorization header in a mode-`0600` curl config file
outside the repository so the bearer never enters shell history or process
arguments:

```bash
curl --config /run/user/$(id -u)/userio-curl.conf \
  http://127.0.0.1:18093/v1/runtime
```

`GET /v1/workspace/events?after=0&limit=50` is the authenticated,
read-capability-gated cursor feed for an opt-in workspace worker. It returns
`schema`, `events`, `cursor`, and `head` for the current principal only. Each
accepted incoming message has one durable integer sequence, source/message/
conversation identity, current account/route binding, sender, body and receive
time; duplicates and outgoing messages do not add events. `head` lets a newly
enabled worker start after historical messages without marking them seen.
When a worker processes an event it must still enforce an explicit provider,
account, route and `/work` trigger allowlist. This endpoint never sends or
approves a reply; disabling the user's read capability returns 403.

The supported deployment and secret-redacted verification commands are
provided by `scripts/runtime_release.py`; prefer them for production rollout.

## Rollback-first release procedure

Run these commands only from a clean canonical `main` whose `HEAD` equals
`origin/main`. Keep the manifest outside the repository so generating release
evidence does not dirty the source checkout:

```bash
python3 scripts/runtime_release.py manifest \
  --source . --remote-ref origin/main \
  --output /var/tmp/universal-userio-release-manifest.json

sudo -n python3 scripts/runtime_release.py prepare \
  --manifest /var/tmp/universal-userio-release-manifest.json \
  --runtime /opt/universal-userio \
  --unit-root /etc/systemd/system \
  --rollback-dir /var/backups/universal-userio
```

`prepare` prints a JSON receipt with `receipt_path`, `archive_sha256`, and an
exact `restore_argv`. Save that output. The archive contains only the explicit
deployment-owned source and unit paths plus prior release identity; it never
contains `/etc/universal-userio.env`, `/var/lib/universal-userio`, provider
state, or the complete dirty checkout.

Use the emitted rollback receipt for installation, then activate and verify:

```bash
sudo -n python3 scripts/runtime_release.py install \
  --source . \
  --manifest /var/tmp/universal-userio-release-manifest.json \
  --rollback-receipt /var/backups/universal-userio/RECEIPT.json

sudo -n python3 scripts/runtime_release.py activate

sudo -n python3 scripts/runtime_release.py verify \
  --source . \
  --manifest /var/tmp/universal-userio-release-manifest.json \
  --check-systemd

sudo -n python3 scripts/runtime_release.py canary \
  --env-file /etc/universal-userio.env
```

The canary uses a unique Matrix conversation sender and the unmapped route
`phase7-no-delivery`; it requires `accepted: true`, `draft: null`, and an exact
read-back from the live inbox. It reads the bearer in-process and never prints
it. If install, activation, verification, or canary fails, run the receipt's
`restore_argv`, activate both services again, and record the release as partial.
