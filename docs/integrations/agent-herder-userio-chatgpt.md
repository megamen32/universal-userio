# ChatGPT Agent Herder via Universal UserIO MV3

Agent Herder owns ChatGPT through Universal UserIO Agent, one extension instance
per Chrome/Edge profile. Z.AI remains a separate judge transport. Vibe is not
permitted as the ChatGPT owner transport.

## Minimal owner configuration

Each profile uses its own protected configuration and identity:

    AHAP_VERSION=0.1
    AHAP_HARNESS=chatgpt
    AHAP_BROWSER_TRANSPORT=userio
    AHAP_USERIO_ENDPOINT=https://msg.bezrabotnyi.com
    AHAP_USERIO_AGENT_ID=<extension agentId>
    AHAP_USERIO_INSTANCE_ID=browser-<UUID from this profile>
    AHAP_BROWSER_ACCOUNT_SCOPE=<SHA-256 of browser-native account ID>
    AHAP_USERIO_ACCESS_TOKEN_FILE=/absolute/path/to/owner.token

Do NOT record bearer tokens or raw browser cookies in Git or logs.
The token file must be owned by the worker UID, be an ordinary file without
symlinks, and have permissions 0600. The bearer principal MUST match the
UserIO OAuth principal of the selected extension: UserIO queues are
segregated by principal. A token for a different user is not interchangeable.

## Enrollment and live acceptance

1. Install the same MV3 package in each profile with separate extension
   storage and agentInstanceId. Reload the extension, not the browser.
2. Verify gpt_scope and gpt_session_proof. gpt_scope only returns a digest.
3. Enqueue gpt_list with agent_id and exact agent_instance_id. Confirm
   only that instance picks it up.
4. gpt_read(chat_ref) must bind to exact /c/<UUID>, return the last user
   and completed final assistant, preserving native status, end_turn,
   phase, model and timestamps. ready is true ONLY for a native assistant
   with status finished_successfully and end_turn=true.
5. Test AHAP initialize, init, listSessions, getSessionMessages. Then
   perform exactly one send/readback canary in a disposable ChatGPT chat,
   verifying a new native user message ID.
6. Confirm release/selector leases and enroll only explicitly approved
   existing chats. Keep Z.AI fallback separate.
7. Respect 429 Retry-After. UNKNOWN sends are never replayed automatically.
8. Never activate a production worker without an exact profile, matching
   UserIO principal, completed native turn and successful canary.

Backend enqueue must route addressed commands by the tuple
(principal, agent_instance_id), not the ambiguous legacy agent_id.
Legacy aliases may report conflicts while UUID-addressed commands remain
deliverable. Message reads and sends only use exact chat refs.

The extension supports current /backend-api/conversations/<id> native
message arrays, and falls back to legacy singular mapping only on 404.
Message text is bounded to 16,000 characters; no cookies or site tokens
are included in the result.

## Rollback

The macOS installer creates protected ZIP backups under
~/Library/Application Support/UserIO/extension-backups/.
Retain the previous backend image and agent_channel.py backup. Do not
disable a working owner or enable new autopilot workers before a verified
read and isolated send canary.
