"""MCP-first control surface for a human or agent operating UserIO."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import InboxMessage
from .service import UserIOService
from .store import SQLiteUserIOStore


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "inputSchema": self.input_schema}


def _schema(properties: dict[str, Any], required: list[str] = []) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


TOOL_SPECS = (
    ToolSpec("userio.inbox.list_new", "List unhandled messages across connected channels.", _schema({"limit": {"type": "integer", "minimum": 1, "maximum": 100}})),
    ToolSpec("userio.conversation.get", "Read durable conversation history and drafts.", _schema({"conversation_id": {"type": "string"}}, ["conversation_id"])),
    ToolSpec("userio.message.mark_seen", "Mark one canonical source message as seen.", _schema({"source": {"type": "string"}, "message_id": {"type": "string"}}, ["source", "message_id"])),
    ToolSpec("userio.draft.create", "Create a human-written reply draft; does not send.", _schema({"conversation_id": {"type": "string"}, "body": {"type": "string"}}, ["conversation_id", "body"])),
    ToolSpec("userio.draft.update", "Edit a proposed reply draft before sending.", _schema({"draft_id": {"type": "string"}, "body": {"type": "string"}}, ["draft_id", "body"])),
    ToolSpec("userio.draft.delete", "Delete a local unsent/rejected draft; provider data is untouched.", _schema({"draft_id": {"type": "string"}}, ["draft_id"])),
    ToolSpec("userio.draft.approve_send", "Explicitly send one exact proposed draft through its scoped Outbox route.", _schema({"draft_id": {"type": "string"}, "confirm": {"type": "boolean"}}, ["draft_id", "confirm"])),
    ToolSpec("userio.conversation.delete_local", "Permanently delete UserIO's local conversation copy; never deletes provider data.", _schema({"conversation_id": {"type": "string"}, "confirm": {"type": "boolean"}}, ["conversation_id", "confirm"])),
    ToolSpec("userio.accounts.list", "List configured accounts and their declared capabilities.", _schema({})),
    ToolSpec("userio.ai.propose", "Opt-in: ask configured AI for draft variants from conversation context.", _schema({"conversation_id": {"type": "string"}, "source": {"type": "string"}, "message_id": {"type": "string"}, "sender": {"type": "string"}, "body": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 3}}, ["conversation_id", "source", "message_id", "sender", "body"])),
)


class UserIOMcpSurface:
    def __init__(self, store: SQLiteUserIOStore, service: UserIOService) -> None:
        self._store = store
        self._service = service

    def tool_manifest(self) -> dict[str, Any]:
        return {"tools": [spec.as_dict() for spec in TOOL_SPECS]}

    def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            if name == "tools/list": return self.tool_manifest()
            if name == "tools/call": return self.dispatch(str(arguments.get("name")), arguments.get("arguments", {}))
            if name == "userio.inbox.list_new": return {"ok": True, "messages": self._store.new_messages(limit=int(arguments.get("limit", 50)))}
            if name == "userio.conversation.get": return {"ok": True, "conversation": self._store.conversation(self._required(arguments, "conversation_id"))}
            if name == "userio.message.mark_seen": return {"ok": True, "changed": self._store.mark_seen(source=self._required(arguments, "source"), message_id=self._required(arguments, "message_id"))}
            if name == "userio.draft.create": return {"ok": True, "draft": self._draft(self._service.create_manual_draft(self._required(arguments, "conversation_id"), body=self._required(arguments, "body")))}
            if name == "userio.draft.update": return {"ok": True, "draft": self._draft(self._store.update_draft(self._required(arguments, "draft_id"), body=self._required(arguments, "body")))}
            if name == "userio.draft.delete": return {"ok": True, "deleted": self._store.delete_draft(self._required(arguments, "draft_id"))}
            if name == "userio.draft.approve_send": return self._approve(arguments)
            if name == "userio.conversation.delete_local": return self._delete_conversation(arguments)
            if name == "userio.accounts.list": return {"ok": True, "accounts": self._store.accounts()}
            if name == "userio.ai.propose": return self._propose(arguments)
        except (KeyError, TypeError, ValueError) as error:
            return {"ok": False, "error": str(error) or "invalid_arguments"}
        return {"ok": False, "error": "unknown_tool"}

    def _approve(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if arguments.get("confirm") is not True:
            return {"ok": False, "error": "exact_confirmation_required"}
        return {"ok": True, "draft": self._draft(self._service.approve(self._required(arguments, "draft_id")))}

    def _delete_conversation(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if arguments.get("confirm") is not True:
            return {"ok": False, "error": "exact_confirmation_required"}
        return {"ok": True, "deleted": self._store.delete_conversation(self._required(arguments, "conversation_id")), "scope": "local_userio_only"}

    def _propose(self, arguments: dict[str, Any]) -> dict[str, Any]:
        message = InboxMessage(self._required(arguments, "source"), self._required(arguments, "message_id"), self._required(arguments, "sender"), self._required(arguments, "body"), 0.0)
        drafts = self._service.propose_variants(self._required(arguments, "conversation_id"), message, limit=int(arguments.get("limit", 3)))
        return {"ok": True, "drafts": [self._draft(draft) for draft in drafts]}

    @staticmethod
    def _required(arguments: dict[str, Any], key: str) -> str:
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip(): raise ValueError(f"{key} is required")
        return value.strip()

    @staticmethod
    def _draft(draft: Any) -> dict[str, str]:
        return {"id": draft.id, "conversation_id": draft.conversation_id, "body": draft.body, "status": draft.status}
