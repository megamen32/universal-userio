# Additional private Dot mirrors

The existing Telegram connector can mirror additional private bots through the
existing UserIO extension command channel. It creates no new client or poller.
Legacy `dot-binding.json` and `dot-group-binding.json` remain independent.

Enroll each bot through `/bot/enroll` under its actual UserIO owner's personal
login. Verify the exact Chrome profile, ChatGPT identity, Dot URL and room using
the extension before configuring a mirror. Never copy a Telegram session.

`/var/lib/universal-userio/telegram-qr/dot-additional-bindings.json` is an array
of enabled bindings. It must be a regular file owned by the connector UID with
mode `0600`. Each entry has `enabled: true`, `owner` (UserIO user ID), `peerId`
(the existing private Dot policy's Telegram owner), `botAccountId`, `agentId`,
`pageId` (stable logical tab identity), `room` and `url`. Bot IDs must be unique
and must not overlap the legacy mirrors or reserved personal accounts.

Each bot uses `dot-agent-<bot-id>.age` in the same directory for the existing
owner's encrypted UserIO extension credential. The connector verifies its owner
and the live bot account's enrollment owner before provider/browser operations.
Install initial configuration atomically while holding the project release lease.

Each mirror retains its own `dot-relay-<bot-id>.json` and media journal. The first
successful snapshot establishes a present-head baseline without replaying history.
Removing an entry disables further admissions and operations. Changing an active
binding is refused until reviewed and the connector restarted; retain all old
journals and UNKNOWN operations. Never delete a journal to force a rebind.

When moving a bot from private chat to a supergroup, disable its additional
private entry before activating the group binding. Set the group's optional
`journalId` to `group-<supergroup ID without the -100 prefix>`. Only that exact
value is accepted. The group then uses its own journal while the original
private journal remains intact. Bindings without `journalId` keep their legacy
journal path. Back up the old group binding, settings and both journals under
the release lease; activate the new binding after the connector has restarted.

Acceptance requires a real incoming Telegram message, its exact Dot echo, and a
Dot answer independently read from Telegram. A connected bot alone is not proof
of a working mirror. Files and unsupported media retain the existing media
admission and explicit review behavior; text acceptance does not prove media.
