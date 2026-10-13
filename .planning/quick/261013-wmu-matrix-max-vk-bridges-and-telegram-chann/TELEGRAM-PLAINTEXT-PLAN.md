# Telegram plaintext migration — approved 2026-10-14

Owner authorized new unencrypted Telegram rooms, old history retained,
ordinary Matrix chats untouched, existing forum spaces/topics preserved.

1. Protected inventory and SQLite/config backup; select owner joined encrypted portals.
2. Prepare private replacement rooms without encryption, preserving ghosts and portal metadata.
3. Publish reviewed migration source before deployment. Stop only Telegram bridge;
   atomically repoint exact portal keys, remove old event mappings in bridge DB,
   default=false, allow=true; restart same bounded container.
4. Preserve old rooms/history/member keys. History-only permissions, continuation links;
   mirror forum/topic archive tree and replace live child links.
5. Verify ordinary encryption unchanged and every original forum ID preserved;
   final canary only after all changes, self dialog only. No Telegram membership changes.

Budget and test categories are in deploy/matrix-provider-bridge/README.md.
Do not reset/stash foreign WIP. Protected manifest contains operational IDs;
never publish it. Broader VK/MAX task remains in progress independently.
