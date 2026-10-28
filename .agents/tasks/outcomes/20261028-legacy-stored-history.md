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
