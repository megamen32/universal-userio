"""Declarative MCP tool catalog and capability policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One stable MCP tool declaration."""

    name: str
    description: str
    input_schema: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


def schema(
    properties: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    """Build a strict object schema for one tool input."""

    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


CHANNEL_SCHEMA = {
    "type": "string",
    "enum": ["mail", "telegram", "whatsapp", "matrix", "vk", "sms", "chatgpt"],
}
IGNORED_CHATS_SCHEMA = {
    "type": "array",
    "items": {"type": "string"},
    "maxItems": 100,
}


TOOL_SPECS = (
    ToolSpec(
        "userio.channels.list",
        "List this user's chats across connected channels.",
        schema(
            {
                "channel": CHANNEL_SCHEMA,
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "ignored_chats": IGNORED_CHATS_SCHEMA,
            }
        ),
    ),
    ToolSpec(
        "userio.channels.read",
        "Read one user-owned chat or message with bounded text.",
        schema(
            {
                "channel": CHANNEL_SCHEMA,
                "chat_id": {"type": "string"},
                "message_id": {"type": "string"},
                "ignored_chats": IGNORED_CHATS_SCHEMA,
            }
        ),
    ),
    ToolSpec(
        "userio.channels.download",
        "Download a file when its adapter supports it.",
        schema({"file_ref": {"type": "string"}}, ["file_ref"]),
    ),
    ToolSpec(
        "userio.channels.send_draft",
        "Queue a draft; never send or bypass approval.",
        schema(
            {
                "chat_id": {"type": "string"},
                "text": {"type": "string"},
                "attachments": {"type": "array", "items": {"type": "string"}},
            },
            ["chat_id", "text"],
        ),
    ),
    ToolSpec(
        "userio.workspace.poll",
        "Poll durable inbound events after a user-scoped cursor without marking messages seen.",
        schema(
            {
                "after": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            }
        ),
    ),
    ToolSpec(
        "userio.workspace.claim",
        "Atomically lease the oldest available inbound event to this worker.",
        schema({
            "worker_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "lease_seconds": {"type": "integer", "minimum": 30, "maximum": 3600},
            "after": {"type": "integer", "minimum": 0},
            "telegram_direct_only": {"type": "boolean"},
        }, ["worker_id"]),
    ),
    ToolSpec(
        "userio.workspace.renew",
        "Renew an active workspace event lease owned by this worker.",
        schema({
            "event_seq": {"type": "integer", "minimum": 1},
            "worker_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "lease_token": {"type": "string"},
            "lease_seconds": {"type": "integer", "minimum": 30, "maximum": 3600},
        }, ["event_seq", "worker_id", "lease_token"]),
    ),
    ToolSpec(
        "userio.workspace.complete",
        "Mark an owned workspace event lease done after successful processing.",
        schema({
            "event_seq": {"type": "integer", "minimum": 1},
            "worker_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "lease_token": {"type": "string"},
            "detail": {"type": "string", "maxLength": 2000},
        }, ["event_seq", "worker_id", "lease_token"]),
    ),
    ToolSpec(
        "userio.workspace.fail",
        "Record a failed attempt and immediately release the event for another worker.",
        schema({
            "event_seq": {"type": "integer", "minimum": 1},
            "worker_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "lease_token": {"type": "string"},
            "detail": {"type": "string", "maxLength": 2000},
        }, ["event_seq", "worker_id", "lease_token"]),
    ),
    ToolSpec(
        "userio.workspace.claim_log",
        "Read the durable claim status and attempt log for one inbound event.",
        schema({"event_seq": {"type": "integer", "minimum": 1}}, ["event_seq"]),
    ),
    ToolSpec(
        "userio.workspace.exclusions.list",
        "List chats excluded from all automatic workspace claims.",
        schema({}),
    ),
    ToolSpec(
        "userio.workspace.exclusions.add",
        "Exclude a known chat from automatic claims without deleting its messages.",
        schema({
            "conversation_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "reason": {"type": "string", "maxLength": 500},
        }, ["conversation_id"]),
    ),
    ToolSpec(
        "userio.workspace.exclusions.remove",
        "Remove a chat from the automatic-claim exclusion list.",
        schema({
            "conversation_id": {"type": "string", "minLength": 1, "maxLength": 128},
        }, ["conversation_id"]),
    ),
    ToolSpec(
        "userio.users.create",
        "Owner only: create a user and return one token once.",
        schema(
            {"username": {"type": "string"}, "password": {"type": "string"}},
            ["username", "password"],
        ),
    ),
    ToolSpec(
        "userio.inbox.list_new",
        "Compatibility alias: list this user's unread messages.",
        schema(
            {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "channel": CHANNEL_SCHEMA,
                "ignored_chats": IGNORED_CHATS_SCHEMA,
            }
        ),
    ),
    ToolSpec(
        "userio.conversation.get",
        "Compatibility alias: read a conversation.",
        schema(
            {
                "conversation_id": {"type": "string"},
                "ignored_chats": IGNORED_CHATS_SCHEMA,
            },
            ["conversation_id"],
        ),
    ),
    ToolSpec(
        "userio.message.mark_seen",
        "Mark one user-owned message as seen.",
        schema(
            {"source": {"type": "string"}, "message_id": {"type": "string"}},
            ["source", "message_id"],
        ),
    ),
    ToolSpec(
        "userio.draft.create",
        "Create a reply draft; does not send.",
        schema(
            {
                "conversation_id": {"type": "string"},
                "body": {"type": "string"},
            },
            ["conversation_id", "body"],
        ),
    ),
    ToolSpec(
        "userio.draft.update",
        "Edit a proposed user-owned draft.",
        schema(
            {"draft_id": {"type": "string"}, "body": {"type": "string"}},
            ["draft_id", "body"],
        ),
    ),
    ToolSpec(
        "userio.draft.delete",
        "Delete a local unsent/rejected draft.",
        schema({"draft_id": {"type": "string"}}, ["draft_id"]),
    ),
    ToolSpec(
        "userio.draft.approve_send",
        "Explicitly send one exact approved draft.",
        schema(
            {
                "draft_id": {"type": "string"},
                "confirm": {"type": "boolean"},
                "expected_text": {"type": "string"},
                "expected_chat_id": {
                    "type": "string",
                    "description": "Exact stored draft conversation_id.",
                },
                "expected_attachments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Must be empty for text-only drafts.",
                },
            },
            ["draft_id", "confirm"],
        ),
    ),
    ToolSpec(
        "userio.conversation.delete_local",
        "Delete only the local conversation copy.",
        schema(
            {
                "conversation_id": {"type": "string"},
                "confirm": {"type": "boolean"},
            },
            ["conversation_id", "confirm"],
        ),
    ),
    ToolSpec("userio.accounts.list", "List this user's connected accounts.", schema({})),
    ToolSpec(
        "userio.ai.propose",
        "Opt-in: create AI draft variants.",
        schema(
            {
                "conversation_id": {"type": "string"},
                "source": {"type": "string"},
                "message_id": {"type": "string"},
                "sender": {"type": "string"},
                "body": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 3},
            },
            ["conversation_id", "source", "message_id", "sender", "body"],
        ),
    ),
)


TOOL_CAPABILITIES = {
    "userio.channels.list": "read",
    "userio.channels.read": "read",
    "userio.channels.download": "download",
    "userio.channels.send_draft": "send",
    "userio.workspace.poll": "read",
    "userio.workspace.claim": "read",
    "userio.workspace.renew": "read",
    "userio.workspace.complete": "read",
    "userio.workspace.fail": "read",
    "userio.workspace.claim_log": "read",
    "userio.workspace.exclusions.list": "read",
    "userio.workspace.exclusions.add": "read",
    "userio.workspace.exclusions.remove": "read",
    "userio.inbox.list_new": "read",
    "userio.conversation.get": "read",
    "userio.message.mark_seen": "read",
    "userio.draft.create": "send",
    "userio.draft.update": "send",
    "userio.draft.delete": "send",
    "userio.draft.approve_send": "send",
    "userio.conversation.delete_local": "read",
    "userio.accounts.list": "read",
    "userio.ai.propose": "send",
}
