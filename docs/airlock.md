# Airlock integration

Universal UserIO remains the owner of provider sessions, credentials, cursors,
deduplication, conversations and delivery receipts. An Airlock agent connects
to UserIO through Airlock's callback-only MCP binding; it must not copy a
Telegram, mail, WhatsApp or browser session into the agent application.

## Binding

Register the HTTPS MCP endpoint through `agentsdk.RegisterMCP` with
`MCPAuthToken`. Keep the UserIO bearer token in Airlock's encrypted MCP
credential resource, never in source, settings shown to the model, or tool
arguments.

One MCP credential identifies one UserIO principal. A multi-user Airlock app
must bind each caller to their own UserIO principal. If an application uses one
owner service credential, it must independently compare the current Airlock
user UUID with its configured owner UUID before every UserIO call. Admin status
alone must not borrow another user's communications.

## Durable inbound polling

Airlock's callback MCP handle currently calls tools but does not consume MCP
resource-subscription notifications. Use `userio.workspace.poll`:

```json
{
  "after": 0,
  "limit": 50
}
```

The result uses schema `universal.workspace-events.v1` and contains `events`,
`cursor`, and `head`. Events are append-only, user-scoped incoming messages.
Polling does not mark them seen. Event bodies are bounded previews; when
`body_truncated=true`, read the exact conversation through
`userio.channels.read` before acting on its contents.

Persist the returned `cursor` only after Airlock has durably accepted every
event in that page. Retrying the old cursor intentionally returns the same
events; deduplicate with the canonical `source` and `message_id`. Never advance
to `head` without processing the returned page.

## Read and reply flow

1. Discover available accounts with `userio.accounts.list`.
2. Poll `userio.workspace.poll`, or list chats with `userio.channels.list`.
3. Read the exact chat ID with `userio.channels.read`.
4. Create text with `userio.channels.send_draft`. This never sends.
5. Show the exact stored draft to the human.
6. After explicit approval, call `userio.draft.approve_send` with `confirm=true`
   and the complete snapshot: `draft_id`, `expected_text`,
   `expected_chat_id`, and `expected_attachments`.
7. Report only the returned `sent` and `receipt` state. A gateway acceptance is
   not stronger provider-delivery proof than the receipt says.

Do not combine draft creation and approval in one agent action. A stale body,
recipient, attachment list or already claimed draft fails closed. Read-only
channel accounts remain read-only.

## Acceptance boundary

Unit compatibility covers MCP `2026-07-28`, tool discovery, durable polling,
draft-only behavior, stale-snapshot rejection and one exact approved send. A
release still needs a controlled Airlock consumer canary using a disposable
UserIO principal and destination; an HTTP 200 alone is not delivery proof.
