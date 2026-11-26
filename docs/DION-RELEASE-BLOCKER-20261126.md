# DION release continuation

## Current boundary

The native backend candidate for published revision
`81ffbb1f234df8f77c91389e29f3a68cc197f367` built successfully:
`universal-userio-backend:sha-81ffbb1f`, image
`sha256:a16b593501bcd3f51e2dd1d3c97e6d368a68502277d1b1829ea6bba1023b341a`.

Canonical `scripts/container_release.py replace-backend` stopped before mutation
at `assert_idle()`: SSH to the configured server-88 Asterisk host denied public-key
authentication. The mandatory active-call check was not bypassed. The previous
backend container remains running, unchanged. No credential handoff was attempted
against the old backend, and no messages or calls were sent.

## Additional failure shield

Symptom: opening the extension's own popup in a tab returns
`extension_ui_required` on import.

Hypothesis: Chrome sets `sender.tab` for an extension page opened in a tab; the
old guard rejects this legitimate UI along with content scripts.

RED: `node --test extensions/vk-inbox/tests/dion-background.test.mjs`
failed on the own-popup tab case against the original guard.

Change: require both own runtime ID and exact own `popup.html` URL, regardless
of whether Chrome includes tab metadata. Foreign runtime IDs, web content scripts
and missing URLs remain rejected.

GREEN: `node --test extensions/vk-inbox/tests/dion-background.test.mjs
extensions/vk-inbox/tests/dion.test.mjs` passed all seven tests. Bundled ZIPs
were regenerated using the existing build script. This is source evidence,
not installed-browser acceptance.

## Resume

1. Restore authorized SSH access for the release controller to its configured
   server-88 host. Do not substitute or bypass the mandatory idle check.
2. Re-fetch published main and re-read the current backend identity. Rebuild if
   the published release revision changed. Use the canonical release command
   with revision/container CAS and a fresh protected receipt directory.
3. Verify installed revision and authenticated `GET /v1/dion/accounts`.
4. Publish/install the popup-guard follow-up through the existing release path.
5. Complete credential handoff, native read/sync and refresh/restart acceptance.
   Reconcile accepted or unknown effects before retries.
6. Live send and Pion connectivity remain unverified. Never test calls against
   people; the standing owner rule allows isolated Matrix test calls only.

The untracked older `DION-IMPLEMENTATION-HANDOFF.md` was preserved unchanged.
