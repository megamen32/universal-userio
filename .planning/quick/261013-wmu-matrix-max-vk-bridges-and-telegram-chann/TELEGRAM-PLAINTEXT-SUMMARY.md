# Telegram plaintext migration — verified 2026-10-14

User explicitly authorized replacing only encrypted Telegram bridge rooms,
retaining old history and forum/topic structure. Scope is complete.

- 153 owner joined Telegram portal rooms replaced by 153 private rooms without m.room.encryption.
- All 8 original forum/community spaces retain their IDs and original portal parent keys.
- 153 old rooms remain joined and encrypted, with history-only permissions,
  continuation links and a separate history space with 8 corresponding subspaces.
- Telegram DM entries in owner m.direct point to replacement rooms.
  Unrelated account-data entries are preserved exactly.
- All 34 ordinary Matrix rooms in the before snapshot retain their encryption state.
- 23 stale personal-space links to previously excluded channels/discussions were removed.
  Removed and historical rooms are not mixed into the live Telegram navigation.
- No Telegram membership, logout, remote delete, Synapse encryption setting,
  MAS configuration, ordinary Matrix chat or existing forum space was modified.
- Only mautrix-telegram stopped/restarted; cutover stage completed in 3.277s.
  Its established 512MiB/swap0/CPU1/Tasks128 budget did not change.

Validation: six fast unit checks PASS (0.907s); focused live state verification
PASS (5.128s), including every replacement, archive child link, parent mapping,
owner membership and ordinary encryption state. Final real consumer canary
after all intentional runtime changes PASS: Matrix owner -> Telegram Saved
Messages acknowledged by native Telegram bridge; genuine incoming Telegram
ghost event and outgoing event both m.room.message, not m.room.encrypted.
A deterministic Matrix transaction made only one self send. The initial
receipt assertion expected an older composite ID; this installed connector
uses a numeric message ID for the self dialog. Readback corrected the
assertion and verified the same acknowledged event without another send.
96 sampled original historical event payloads remain byte-for-byte unchanged
after canonical serialization. Final consumer readback took 0.852s.

Tested migration implementation: db54933 on published main. Shared unrelated
WIP was preserved; no clean-checkout claim or worktree was manufactured.
Real Matrix API / native Telegram receipt evidence is used; no simulated
UI success or unrelated recipient was substituted.

Protected runtime evidence and rollback:
server-88 /home/roomhacker/matrix-synapse/.tmp/telegram-plaintext-20261014/
contains manifest, pre-migration and cutover SQLite snapshots, original
config, original event fingerprints, verification.json and final-canary.json.
No secret values or private room IDs are copied into this summary.
Old rooms/history are never purged for rollback. Restoring the old bridge DB
after new delivery requires reconciling the cutover-era mappings first.

The broader MAX/VK media/bridge task retains its own unfinished acceptance
items; this result closes the explicitly approved Telegram room migration.

