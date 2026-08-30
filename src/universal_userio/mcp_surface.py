"""Authenticated MCP surface for user-scoped UserIO operations."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from .adapters import AdapterNotSupported, UnifiedChannels
from .contracts import InboxMessage, UserPrincipal
from .service import UserIOService
from .store import SQLiteUserIOStore


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "inputSchema": self.input_schema}


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object", "properties": properties, "required": required or [],
        "additionalProperties": False,
    }


TOOL_SPECS = (
    ToolSpec("userio.channels.list", "List this user's chats across connected channels.", _schema({
        "channel": {"type": "string", "enum": ["mail", "telegram", "whatsapp", "vk", "chatgpt"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    })),
    ToolSpec("userio.channels.read", "Read one user-owned chat or message with bounded text.", _schema({
        "channel": {"type": "string", "enum": ["mail", "telegram", "whatsapp", "vk", "chatgpt"]},
        "chat_id": {"type": "string"}, "message_id": {"type": "string"},
    })),
    ToolSpec("userio.channels.download", "Download a file when its adapter supports it.", _schema({
        "file_ref": {"type": "string"},
    }, ["file_ref"])),
    ToolSpec("userio.channels.send_draft", "Queue a draft; never send or bypass approval.", _schema({
        "chat_id": {"type": "string"}, "text": {"type": "string"},
        "attachments": {"type": "array", "items": {"type": "string"}},
    }, ["chat_id", "text"])),
    ToolSpec("userio.users.create", "Owner only: create a user and return one token once.", _schema({
        "username": {"type": "string"}, "password": {"type": "string"},
    }, ["username", "password"])),
    ToolSpec("userio.inbox.list_new", "Compatibility alias: list this user's unread messages.", _schema({
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    })),
    ToolSpec("userio.conversation.get", "Compatibility alias: read a conversation.", _schema({
        "conversation_id": {"type": "string"},
    }, ["conversation_id"])),
    ToolSpec("userio.message.mark_seen", "Mark one user-owned message as seen.", _schema({
        "source": {"type": "string"}, "message_id": {"type": "string"},
    }, ["source", "message_id"])),
    ToolSpec("userio.draft.create", "Create a reply draft; does not send.", _schema({
        "conversation_id": {"type": "string"}, "body": {"type": "string"},
    }, ["conversation_id", "body"])),
    ToolSpec("userio.draft.update", "Edit a proposed user-owned draft.", _schema({
        "draft_id": {"type": "string"}, "body": {"type": "string"},
    }, ["draft_id", "body"])),
    ToolSpec("userio.draft.delete", "Delete a local unsent/rejected draft.", _schema({
        "draft_id": {"type": "string"},
    }, ["draft_id"])),
    ToolSpec("userio.draft.approve_send", "Explicitly send one exact approved draft.", _schema({
        "draft_id": {"type": "string"}, "confirm": {"type": "boolean"},
    }, ["draft_id", "confirm"])),
    ToolSpec("userio.conversation.delete_local", "Delete only the local conversation copy.", _schema({
        "conversation_id": {"type": "string"}, "confirm": {"type": "boolean"},
    }, ["conversation_id", "confirm"])),
    ToolSpec("userio.accounts.list", "List this user's connected accounts.", _schema({})),
    ToolSpec("userio.ai.propose", "Opt-in: create AI draft variants.", _schema({
        "conversation_id": {"type": "string"}, "source": {"type": "string"},
        "message_id": {"type": "string"}, "sender": {"type": "string"},
        "body": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 3},
    }, ["conversation_id", "source", "message_id", "sender", "body"])),
)


class UserIOMcpSurface:
    def __init__(
        self, store: SQLiteUserIOStore, service: UserIOService,
        principal: UserPrincipal | None = None,
    ) -> None:
        self._store, self._service = store, service
        self._principal = store.owner() if principal is None else principal

    def tool_manifest(self) -> dict[str, Any]:
        return {"tools": [spec.as_dict() for spec in TOOL_SPECS]}

    def dispatch(
        self, name: str, arguments: dict[str, Any], *, principal: UserPrincipal | None = None
    ) -> dict[str, Any]:
        principal = self._principal if principal is None else principal
        user_id = principal.user_id
        channels = UnifiedChannels(self._store, self._service, user_id)
        try:
            if name == "tools/list":
                return self.tool_manifest()
            if name == "tools/call":
                return self.dispatch(
                    str(arguments.get("name")), arguments.get("arguments", {}), principal=principal
                )
            if name == "userio.channels.list":
                adapter = channels.adapter(self._optional(arguments, "channel"))
                return {"ok": True, "chats": adapter.list(limit=int(arguments.get("limit", 100)))}
            if name == "userio.channels.read":
                adapter = channels.adapter(self._optional(arguments, "channel"))
                return {"ok": True, **adapter.read(
                    chat_id=self._optional(arguments, "chat_id"),
                    message_id=self._optional(arguments, "message_id"),
                )}
            if name == "userio.channels.download":
                file = channels.download(file_ref=self._required(arguments, "file_ref"))
                return {"ok": True, "file": {
                    "filename": file.filename, "content_type": file.content_type,
                    "encoding": "base64", "data": base64.b64encode(file.data).decode(),
                }}
            if name == "userio.channels.send_draft":
                attachments = arguments.get("attachments")
                if attachments is not None and not isinstance(attachments, list):
                    raise ValueError("attachments must be an array")
                draft = channels.send(
                    chat_id=self._required(arguments, "chat_id"),
                    text=self._required(arguments, "text"), attachments=attachments,
                )
                return {
                    "ok": True, "draft": self._draft(draft),
                    "sent": False, "approval_required": True,
                }
            if name == "userio.users.create":
                if principal.role != "owner":
                    return {"ok": False, "error": "owner_required"}
                user, token = self._store.create_user(
                    self._required(arguments, "username"), self._required(arguments, "password")
                )
                return {
                    "ok": True,
                    "user": {"id": user.user_id, "username": user.username, "role": user.role},
                    "token": token, "token_returned_once": True,
                }
            if name == "userio.inbox.list_new":
                return {"ok": True, "messages": self._store.new_messages(
                    limit=int(arguments.get("limit", 50)), user_id=user_id
                )}
            if name == "userio.conversation.get":
                return {"ok": True, "conversation": self._store.conversation(
                    self._required(arguments, "conversation_id"), user_id=user_id
                )}
            if name == "userio.message.mark_seen":
                return {"ok": True, "changed": self._store.mark_seen(
                    source=self._required(arguments, "source"),
                    message_id=self._required(arguments, "message_id"), user_id=user_id,
                )}
            if name == "userio.draft.create":
                draft = self._service.create_manual_draft(
                    self._required(arguments, "conversation_id"),
                    body=self._required(arguments, "body"), user_id=user_id,
                )
                return {"ok": True, "draft": self._draft(draft)}
            if name == "userio.draft.update":
                draft = self._store.update_draft(
                    self._required(arguments, "draft_id"),
                    body=self._required(arguments, "body"), user_id=user_id,
                )
                return {"ok": True, "draft": self._draft(draft)}
            if name == "userio.draft.delete":
                return {"ok": True, "deleted": self._store.delete_draft(
                    self._required(arguments, "draft_id"), user_id=user_id
                )}
            if name == "userio.draft.approve_send":
                return self._approve(arguments, principal)
            if name == "userio.conversation.delete_local":
                return self._delete_conversation(arguments, principal)
            if name == "userio.accounts.list":
                return {"ok": True, "accounts": self._store.accounts(user_id=user_id)}
            if name == "userio.ai.propose":
                return self._propose(arguments, principal)
        except AdapterNotSupported as error:
            return {"ok": False, "error": str(error)}
        except (KeyError, TypeError, ValueError) as error:
            return {"ok": False, "error": str(error).strip("'") or "invalid_arguments"}
        return {"ok": False, "error": "unknown_tool"}

    def _approve(self, arguments: dict[str, Any], principal: UserPrincipal) -> dict[str, Any]:
        if arguments.get("confirm") is not True:
            return {"ok": False, "error": "exact_confirmation_required"}
        draft = self._service.approve(
            self._required(arguments, "draft_id"), user_id=principal.user_id
        )
        return {"ok": True, "draft": self._draft(draft)}

    def _delete_conversation(
        self, arguments: dict[str, Any], principal: UserPrincipal
    ) -> dict[str, Any]:
        if arguments.get("confirm") is not True:
            return {"ok": False, "error": "exact_confirmation_required"}
        deleted = self._store.delete_conversation(
            self._required(arguments, "conversation_id"), user_id=principal.user_id
        )
        return {"ok": True, "deleted": deleted, "scope": "local_userio_only"}

    def _propose(self, arguments: dict[str, Any], principal: UserPrincipal) -> dict[str, Any]:
        message = InboxMessage(
            self._required(arguments, "source"), self._required(arguments, "message_id"),
            self._required(arguments, "sender"), self._required(arguments, "body"), 0.0,
        )
        drafts = self._service.propose_variants(
            self._required(arguments, "conversation_id"), message,
            limit=int(arguments.get("limit", 3)), user_id=principal.user_id,
        )
        return {"ok": True, "drafts": [self._draft(draft) for draft in drafts]}

    @staticmethod
    def _required(arguments: dict[str, Any], key: str) -> str:
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} is required")
        return value.strip()

    @staticmethod
    def _optional(arguments: dict[str, Any], key: str) -> str | None:
        value = arguments.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        return value.strip() or None

    @staticmethod
    def _draft(draft: Any) -> dict[str, str]:
        return {
            "id": draft.id, "conversation_id": draft.conversation_id,
            "body": draft.body, "status": draft.status,
        }
