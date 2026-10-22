# Deploy: first contact and honest delivery

Branch `feature/messenger-first-contact-fixes`, commit
`bd91d1ef1e99b37b12f453a80094b7627685e59a` (base `origin/main`
`f5990e4e...`).

Nothing else changes: no connector change, no image change, no new secret,
no database migration. The telegram-qr connector already answers `GET /state`
with the per-account `status`, and the image already ships Himalaya v2.0.0
with `message add --mailbox`.

## 0. Publish the commit on main

`scripts/container_release.py context` refuses to build anything that is not
on `origin/main`, so the branch has to be merged first:

```bash
git -C /home/roomhacker/agents-projects/universal-userio fetch origin
git -C /home/roomhacker/agents-projects/universal-userio checkout main
git -C /home/roomhacker/agents-projects/universal-userio merge --ff-only origin/feature/messenger-first-contact-fixes
git -C /home/roomhacker/agents-projects/universal-userio push origin main
```

The merge must leave `origin/main` exactly at
`bd91d1ef1e99b37b12f453a80094b7627685e59a`.

## 1. Build the image from that SHA

```bash
sh /home/roomhacker/deploy-userio-build.sh \
  bd91d1ef1e99b37b12f453a80094b7627685e59a first-contact-bd91d1e
```

The script clones the repository, verifies `HEAD == origin/main == <SHA>`,
builds the release context and writes the image
`universal-userio:first-contact-bd91d1e`. It prints `BUILD_OK` on success.

## 2. Swap the running backend

```bash
sh /home/roomhacker/deploy-userio-swap.sh first-contact-bd91d1e
```

The script refuses to swap while a voice call is active, renames the old
container to `userio-backend-rollback-<TS>` and tags the old image
`universal-userio:rollback-<TS>`.

## 3. Verify

```bash
# the new image is live
docker inspect userio-backend --format '{{.Config.Image}}'

# the first-contact capability is reported for the Telegram account
docker exec -i userio-backend python3 - <<'PY'
import json, urllib.request, os
core = json.load(open('/etc/userio-containers/processes.json'))
env = [p for p in core['processes'] if p['name'] == 'core'][0]['environment']
body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
  'params': {'name': 'userio.messenger.capabilities',
             'arguments': {'account_ref': 'telegram:540308572'}}}).encode()
req = urllib.request.Request('http://127.0.0.1:%s/mcp' % env.get('USERIO_PORT', '18093'),
  data=body, method='POST',
  headers={'Content-Type': 'application/json',
           'Authorization': 'Bearer ' + env['USERIO_API_TOKEN']})
with urllib.request.urlopen(req, timeout=30) as response:
    answer = json.loads(response.read())['result']['structuredContent']
    print(json.dumps(answer.get('first_contact', answer), ensure_ascii=False, indent=2))
PY
```

Expected: `supported: true`, `availability: available` while the connector
holds a live session for that account, and `supported: false` with
`reason: connector_unreachable` or `account_not_connected` while it does
not. The answer must never be `unsupported` for a healthy account.

## 4. Rollback

```bash
docker stop userio-backend
docker rm userio-backend
docker rename userio-backend-rollback-<TS> userio-backend
docker start userio-backend
```

The old image stays available as `universal-userio:rollback-<TS>`.

## Live canary, on owner accounts only

- Send one letter from the `gmail-airlock` account to an owner test address.
  The letter must appear in the account's Sent mailbox, and the answer must
  say `delivery_status: smtp_accepted`, `sent: false`, with the RFC
  Message-ID in `message_id`.
- Approve the same draft twice. The second answer must carry the same
  `receipt` and `idempotency_key`, must say `replay: true`, and the mailbox
  must still hold one letter.
- Resolve a Telegram username of an owner test contact and read the
  `first_contact` block of the same account.

Never use a partner or customer address for the canary.
