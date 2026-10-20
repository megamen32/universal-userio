---
name: userio
description: Use Universal UserIO to inspect the owner's unified communications and message-related files across connected channels.
---
# Universal UserIO

Use the active UserIO MCP server for the owner's connected communication channels. If both the plugin connection and a workspace connection are present, prefer the workspace connection named `userio-authenticated` when it has a valid token; do not call a connection that reports an authorization error.

Prefer resource reads/subscriptions for inbox monitoring and tools for bounded queries/actions. Preserve source provenance when summarizing or extracting tasks. Never send, approve a draft, mark read, delete, or otherwise mutate communication state unless the current UserIO user's capabilities expose that operation and the user explicitly asked for it.

## Authentication boundary (fail closed)

The user's own configured UserIO MCP connection, carrying that user's authorized token, is the only accepted path to UserIO data. The rules below are mandatory for every attempt to reach UserIO.

- If the UserIO MCP connection is missing, or a call returns 401, `unauthorized`, or any other authentication error, stop. Report the failure and the exact setup step the user must complete, then wait for the user. Do not retry against another connection, identity, or credential.
- Never obtain, read, or reuse a UserIO token, secret, or credential from server files, environment variables, container configuration, SSH sessions, process listings, secret stores, or logs. The token is configured by the user in their own client; do not hunt for it, and do not ask the user to paste a token into the conversation.
- Never call the UserIO backend directly over HTTP, and never substitute another server identity, service account, shared token, or hidden credential, to work around an authentication failure. The MCP server is the only supported entry point.
- Never present results obtained under a different identity as the user's authorized data, and never report that UserIO was read, listed, or fetched when the call failed. Report the failure instead.
- Continue only when calls succeed through the user's configured UserIO MCP connection with its authorized user token, and say which connection was used.
