# Stored messenger history: legacy Telegram source identifiers

Owner request: repair UserIO history failure reported by the ExManager owner.
Lead brief: ExManager `.agents/tasks/briefs/20261028-userio-history-repair.md`.
Scope: local stored-history serialization; no Telegram API changes, sends,
read acknowledgements, credential changes or data migration.

## Diagnosis and failure shield

`SQLiteUserIOStore.ingest` intentionally preserves an existing legacy `peer:id`
message key when an account-scoped replay hydrates the same conversation.
`Messenger._project` accepted only `account_ref|peer:id`; a mixed page therefore
failed entirely with `provider_source_id_unavailable` although its messages
remained stored. Lead independently confirmed both forms in the affected live
conversation without exporting message bodies.

The fix recognizes only the exact conversation peer prefix for Telegram legacy
keys. Existing current user/account/conversation authorization and positive
numeric provider-ID validation remain in force. Stored IDs, chronology,
pagination and audit/source identity are preserved. Do not rename legacy rows
or guess a provider ID from an arbitrary suffix.

Verified production path: `src/universal_userio/store.py:2103` legacy hydration;
`src/universal_userio/messenger.py:225` source projection;
`tests/test_messenger_legacy_history.py` discriminating regression.

## Verification

- RED before source change: `python -m pytest -q tests/test_messenger_legacy_history.py`
  returned 2 failed / 4 passed; both MCP surfaces failed the older page with the
  reported `provider_source_id_unavailable`.
- GREEN after the four-line source fix: all six new tests passed. Coverage
  includes mixed legacy/current pages, source identity retention, read-only
  access and refusal of wrong-peer/account, zero and extra-segment IDs.
- Related suite: `python -m pytest -q tests/test_messenger_legacy_history.py tests/test_telegram_mirror.py tests/test_messenger.py -k 'not reaction_removal_uses_same_approved_path_without_whitespace'`
  returned 37 passed / 1 deselected in 33.59 seconds.
- Existing unrelated `test_reaction_removal_uses_same_approved_path_without_whitespace`
  fails `reaction_removal_not_supported`; independently reproduced with the
  original HEAD messenger module loaded in memory. No unrelated source rollback
  or fixture edits were performed. This is not a history regression.
- Installed pytest emits the existing unknown-config warning for
  `asyncio_default_fixture_loop_scope`.
- GSD original `gsd-tools.cjs init quick` initialized bounded task context
  `261028-pkt`; the installed CLI does not support `init debug`.

## Delivery boundary

Only `src/universal_userio/messenger.py`, the new regression file and this receipt
belong to this fix. Existing voice/Matrix WIP, `tests/test_history_search.py`,
AGENTS, test access and other files remain untouched.

Lead owns image build, reviewed exact-file deployment and independent installed
MCP acceptance. Source tests do not establish installed recovery. A separate
provider refresh HTTP error is being diagnosed by Lead; this stored-ID repair
does not claim to resolve that second failure.

## Installed integration receipt

Source repair published as `975fcc5`; Lead separately reviewed transport source
`5f282c3` (connector server plus new transport-options module). The guarded
release script was published as `0fc4da2`, refined by `50453db`/`8bd382c`.
Only those three reviewed application files were overlaid on the accepted
backend image; unrelated main history and WIP did not enter the layer.

The first activation preflight exposed a separate legacy guard parser defect:
incoming Matrix call claims omit `max_seconds`, while their installed answer
request has a fixed 120-second bound. `f0373fc` repairs only that schema case;
all other absent/invalid/future/recent bounds still fail closed. Fast RED was
1 failed / 6 passed, followed by 31 GREEN release tests. Receipt bytes were not
changed. Asterisk, RTC-process and meeting checks remained enabled.

A second pre-mutation check caught Docker automatically merging the new image
manifest label. The revised check allows only that exact manifest metadata;
all original labels and every operational configuration value stay identical.
The existing created candidate and successful backup were reconciled and reused.
Neither preflight failure stopped or replaced the original backend.

Final installed backend `d240792ccf1a1d05f41c6ae9afa17c23c0a0ae8880ff93104a6934a30ca0da0d`
runs image `sha256:fa31b8129a83b1b1eadbf31655fe21beb17e3e8f5275e0c14ec1797c95726c17`,
overlay manifest `0221993a581191caef56e8ecdf2fcdb98a6f4024d62cf5ee96978dad4c2e147d`.
Health passed, installed Python/connector source hashes match exactly the
three-file manifest, Docker environment/HostConfig stayed identical, frontend
and voice container IDs stayed identical, and pending UNKNOWN receipt hashes
stayed identical. Only the Telegram child's configured proxy changed from
port 3128 to the independently verified 3127 and its transport became obfuscated.

Verified online SQLite backup:
`/var/backups/universal-userio/history-repair-1793201972/userio.sqlite3`;
both `quick_check` and `integrity_check` returned `ok`. All 30 baseline source
rows retained their IDs/content/direction/timestamps; baseline fingerprint
`d8e433d1baa2971e03be4a3200392394796748ea812ebce0d60f9bad79b7f1c0`.
The original backend remains stopped as `userio-backend-prev-history-1793202077`.
Protected full process configuration backup and sanitized build/install receipts
remain in `.agents/tasks/20261028-history-repair/private/`, never committed.

Independent MCP stored/fresh consumer acceptance belongs to Lead and remains
separate from this installed hash/configuration proof. This worker sent no
messages, calls, read acknowledgements or standalone Telegram-session requests.
