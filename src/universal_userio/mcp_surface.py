"""Authenticated MCP surface for user-scoped UserIO operations."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from .adapters import AdapterNotSupported, UnifiedChannels
from .contracts import InboxMessage, UserPrincipal
from .service import UserIOService
from .store import SQLiteUserIOStore


_WORKSPACE_EVENT_BODY_LIMIT = 4096


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
        "channel": {"type": "string", "enum": ["mail", "telegram", "whatsapp", "matrix", "vk", "sms", "chatgpt"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "ignored_chats": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
    })),
    ToolSpec("userio.channels.read", "Read one user-owned chat or message with bounded text.", _schema({
        "channel": {"type": "string", "enum": ["mail", "telegram", "whatsapp", "matrix", "vk", "sms", "chatgpt"]},
        "chat_id": {"type": "string"}, "message_id": {"type": "string"},
        "ignored_chats": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
    })),
    ToolSpec("userio.channels.download", "Download a file when its adapter supports it.", _schema({
        "file_ref": {"type": "string"},
    }, ["file_ref"])),
    ToolSpec("userio.channels.send_draft", "Queue a draft; never send or bypass approval.", _schema({
        "chat_id": {"type": "string"}, "text": {"type": "string"},
        "attachments": {"type": "array", "items": {"type": "string"}},
    }, ["chat_id", "text"])),
    ToolSpec(
        "userio.workspace.poll",
        "Poll durable inbound events after a user-scoped cursor without marking messages seen.",
        _schema({
            "after": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        }),
    ),
    ToolSpec("userio.workspace.claim", "Atomically lease the oldest available inbound event to this worker.", _schema({
        "worker_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "lease_seconds": {"type": "integer", "minimum": 30, "maximum": 3600},
        "after": {"type": "integer", "minimum": 0},
        "telegram_direct_only": {"type": "boolean"},
    }, ["worker_id"])),
    ToolSpec("userio.workspace.renew", "Renew an active workspace event lease owned by this worker.", _schema({
        "event_seq": {"type": "integer", "minimum": 1},
        "worker_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "lease_token": {"type": "string"},
        "lease_seconds": {"type": "integer", "minimum": 30, "maximum": 3600},
    }, ["event_seq", "worker_id", "lease_token"])),
    ToolSpec("userio.workspace.complete", "Mark an owned workspace event lease done after successful processing.", _schema({
        "event_seq": {"type": "integer", "minimum": 1},
        "worker_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "lease_token": {"type": "string"}, "detail": {"type": "string", "maxLength": 2000},
    }, ["event_seq", "worker_id", "lease_token"])),
    ToolSpec("userio.workspace.fail", "Record a failed attempt and immediately release the event for another worker.", _schema({
        "event_seq": {"type": "integer", "minimum": 1},
        "worker_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "lease_token": {"type": "string"}, "detail": {"type": "string", "maxLength": 2000},
    }, ["event_seq", "worker_id", "lease_token"])),
    ToolSpec("userio.workspace.claim_log", "Read the durable claim status and attempt log for one inbound event.", _schema({
        "event_seq": {"type": "integer", "minimum": 1},
    }, ["event_seq"])),
    ToolSpec("userio.workspace.exclusions.list", "List chats excluded from all automatic workspace claims.", _schema({})),
    ToolSpec("userio.workspace.exclusions.add", "Exclude a known chat from automatic claims without deleting its messages.", _schema({
        "conversation_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "reason": {"type": "string", "maxLength": 500},
    }, ["conversation_id"])),
    ToolSpec("userio.workspace.exclusions.remove", "Remove a chat from the automatic-claim exclusion list.", _schema({
        "conversation_id": {"type": "string", "minLength": 1, "maxLength": 128},
    }, ["conversation_id"])),
    ToolSpec("userio.workspace.policy.get", "Read user-scoped automatic processing defaults and revision.", _schema({})),
    ToolSpec("userio.workspace.policy.set_default", "Enable or disable automatic processing for a conversation kind; only future arrivals gain eligibility.", _schema({
        "conversation_kind": {"type": "string", "enum": ["direct", "group", "channel", "unknown", "telegram_bot"]},
        "enabled": {"type": "boolean"},
    }, ["conversation_kind", "enabled"])),
    ToolSpec("userio.workspace.policy.telegram_bots.set", "Enable or disable future Telegram bot-authored notifications, including bot dialogs and allowed groups.", _schema({
        "enabled": {"type": "boolean"},
    }, ["enabled"])),
    ToolSpec("userio.workspace.policy.chats.list", "Search known chats by account, peer, title, kind, or rule (at most 200 per page).", _schema({
        "source": {"type": "string"}, "account_ref": {"type": "string"},
        "peer_id": {"type": "string"},
        "conversation_kind": {"type": "string", "enum": ["direct", "group", "channel", "unknown", "telegram_bot"]},
        "action": {"type": "string", "enum": ["allow", "ignore", "inherit"]},
        "query": {"type": "string", "maxLength": 128},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        "offset": {"type": "integer", "minimum": 0, "maximum": 100000},
    })),
    ToolSpec("userio.workspace.policy.chats.set", "Set a known chat to allow, ignore, or inherit its kind default.", _schema({
        "conversation_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "action": {"type": "string", "enum": ["allow", "ignore", "inherit"]},
        "reason": {"type": "string", "maxLength": 500},
    }, ["conversation_id", "action"])),
    ToolSpec("userio.workspace.policy.evaluate", "Explain the current automatic processing decision for one known chat.", _schema({
        "conversation_id": {"type": "string", "minLength": 1, "maxLength": 128},
    }, ["conversation_id"])),
    ToolSpec("userio.workspace.triage.get", "Read this user's importance-triage thresholds.", _schema({})),
    ToolSpec("userio.workspace.triage.set", "Set user-scoped importance-triage thresholds.", _schema({
        "enabled": {"type": "boolean"},
        "threshold": {"type": "number", "minimum": 0, "maximum": 1},
        "min_confidence": {"type": "number", "minimum": 0, "maximum": 1},
    })),
    ToolSpec("userio.workspace.triage.feedback", "Record importance feedback for one completed triage.", _schema({
        "event_seq": {"type": "integer", "minimum": 1},
        "request_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "label": {"type": "string", "enum": ["important", "not_important", "ignore_chat"]},
    }, ["event_seq", "request_id", "label"])),
    ToolSpec("userio.users.create", "Owner only: create a user and return one token once.", _schema({
        "username": {"type": "string"}, "password": {"type": "string"},
    }, ["username", "password"])),
    ToolSpec("userio.inbox.list_new", "Compatibility alias: list this user's unread messages.", _schema({
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "channel": {"type": "string", "enum": ["mail", "telegram", "whatsapp", "matrix", "vk", "sms", "chatgpt"]},
        "ignored_chats": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
    })),
    ToolSpec("userio.conversation.get", "Compatibility alias: read a conversation.", _schema({
        "conversation_id": {"type": "string"},
        "ignored_chats": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
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
        "expected_text": {"type": "string"},
        "expected_chat_id": {"type": "string", "description": "Exact stored draft conversation_id."},
        "expected_attachments": {"type": "array", "items": {"type": "string"},
                                 "description": "Must be empty for text-only drafts."},
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
    "userio.workspace.exclusions.add": "send",
    "userio.workspace.exclusions.remove": "send",
    "userio.workspace.policy.get": "read",
    "userio.workspace.policy.set_default": "send",
    "userio.workspace.policy.telegram_bots.set": "send",
    "userio.workspace.policy.chats.list": "read",
    "userio.workspace.policy.chats.set": "send",
    "userio.workspace.policy.evaluate": "read",
    "userio.workspace.triage.get": "read",
    "userio.workspace.triage.set": "send",
    "userio.workspace.triage.feedback": "send",
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

class UserIOMcpSurface:
    def __init__(
        self, store: SQLiteUserIOStore, service: UserIOService,
        principal: UserPrincipal | None = None,
    ) -> None:
        self._store, self._service = store, service
        self._principal = store.owner() if principal is None else principal

    def tool_manifest(self, *, principal: UserPrincipal | None = None) -> dict[str, Any]:
        principal = self._principal if principal is None else principal
        specs = tuple(
            spec for spec in TOOL_SPECS
            if (required := TOOL_CAPABILITIES.get(spec.name)) is None
            or self._store.capability_enabled(required, user_id=principal.user_id)
        )
        return {"tools": [spec.as_dict() for spec in specs]}

    def resource_manifest(self, *, principal: UserPrincipal | None = None) -> dict[str, Any]:
        principal = self._principal if principal is None else principal
        if not self._store.capability_enabled("read", user_id=principal.user_id):
            return {"resources": []}
        return {"resources": [
            {"uri": "userio://accounts", "name": "Connected accounts", "mimeType": "application/json"},
            {"uri": "userio://conversations", "name": "Recent conversations", "mimeType": "application/json"},
            {"uri": "userio://inbox/unread", "name": "Unread inbox", "mimeType": "application/json"},
        ]}

    def resource_template_manifest(self, *, principal: UserPrincipal | None = None) -> dict[str, Any]:
        principal = self._principal if principal is None else principal
        if not self._store.capability_enabled("read", user_id=principal.user_id):
            return {"resourceTemplates": []}
        return {"resourceTemplates": [
            {
                "uriTemplate": "userio://conversations/{conversationId}",
                "name": "Conversation by id",
                "mimeType": "application/json",
            },
        ]}

    def read_resource(
        self, uri: str, *, principal: UserPrincipal | None = None
    ) -> dict[str, Any]:
        principal = self._principal if principal is None else principal
        user_id = principal.user_id
        if not self._store.capability_enabled("read", user_id=user_id):
            raise PermissionError("read capability disabled")
        if uri == "userio://accounts":
            payload = {"accounts": self._store.accounts(user_id=user_id)}
        elif uri == "userio://conversations":
            payload = {"conversations": self._store.conversations(limit=100, user_id=user_id)}
        elif uri == "userio://inbox/unread":
            payload = {"messages": self._store.new_messages(limit=100, user_id=user_id)}
        elif uri.startswith("userio://conversations/"):
            conversation_id = uri.removeprefix("userio://conversations/").strip()
            if not conversation_id or "/" in conversation_id:
                raise ValueError("invalid conversation resource uri")
            conversation = self._store.conversation(conversation_id, user_id=user_id)
            if conversation is None:
                raise KeyError("conversation not found")
            payload = {"conversation": conversation}
        else:
            raise KeyError("resource not found")
        import json
        return {
            "contents": [{
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            }]
        }

    def dispatch(
        self, name: str, arguments: dict[str, Any], *, principal: UserPrincipal | None = None
    ) -> dict[str, Any]:
        principal = self._principal if principal is None else principal
        user_id = principal.user_id
        channels = UnifiedChannels(self._store, self._service, user_id)
        try:
            if name == "tools/list":
                return self.tool_manifest(principal=principal)
            if name == "tools/call":
                return self.dispatch(
                    str(arguments.get("name")), arguments.get("arguments", {}), principal=principal
                )
            required_capability = TOOL_CAPABILITIES.get(name)
            if required_capability and not self._store.capability_enabled(required_capability, user_id=user_id):
                return {"ok": False, "error": f"{required_capability}_capability_disabled"}
            if name == "userio.channels.list":
                ignored_chats = self._ignored_chats(arguments)
                adapter = channels.adapter(self._optional(arguments, "channel"))
                chats = adapter.list(limit=int(arguments.get("limit", 100)))
                return {"ok": True, "chats": [
                    chat for chat in chats
                    if str(chat["id"]) not in ignored_chats
                ]}
            if name == "userio.channels.read":
                ignored_chats = self._ignored_chats(arguments)
                adapter = channels.adapter(self._optional(arguments, "channel"))
                result = adapter.read(
                    chat_id=self._optional(arguments, "chat_id"),
                    message_id=self._optional(arguments, "message_id"),
                )
                record = result.get("chat") or result.get("message") or {}
                conversation_id = str(record.get("id") or record.get("conversation_id") or "")
                if conversation_id in ignored_chats:
                    raise KeyError("chat not found")
                return {"ok": True, **result}
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
                conversation = self._store.conversation(draft.conversation_id, user_id=user_id) or {}
                manual_lock = self._service.manual_approval_required(conversation)
                hint = (
                    "SMS manual-approve lock is ON: nothing is sent until the owner explicitly "
                    "approves this exact draft via userio_draft_approve_send (confirm=true)."
                    if manual_lock else
                    "Draft only: nothing is sent until userio_draft_approve_send is called "
                    "with confirm=true."
                )
                return {
                    "ok": True, "draft": self._draft(draft),
                    "sent": False, "approval_required": True,
                    "channel": str(conversation.get("source") or ""),
                    "response_mode": str(conversation.get("response_mode") or ""),
                    "manual_approval_only": manual_lock,
                    "approval_hint": hint,
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
            if name == "userio.workspace.poll":
                page = self._store.workspace_events(
                    after=arguments.get("after", 0),
                    limit=arguments.get("limit", 50),
                    user_id=user_id,
                )
                return {
                    "ok": True,
                    "schema": "universal.workspace-events.v1",
                    **page,
                    "events": [self._workspace_event(event) for event in page["events"]],
                }
            if name == "userio.workspace.claim":
                claimed = self._store.claim_workspace_event(
                    worker_id=self._required(arguments, "worker_id"),
                    lease_seconds=arguments.get("lease_seconds", 600),
                    after=arguments.get("after", 0),
                    telegram_direct_only=arguments.get("telegram_direct_only", False),
                    user_id=user_id,
                )
                if claimed is None:
                    return {"ok": True, "claimed": False}
                claimed["event"] = self._workspace_event(claimed["event"])
                return {"ok": True, "claimed": True, **claimed}
            if name == "userio.workspace.renew":
                claim = self._store.renew_workspace_claim(
                    event_seq=arguments.get("event_seq"),
                    worker_id=self._required(arguments, "worker_id"),
                    lease_token=self._required(arguments, "lease_token"),
                    lease_seconds=arguments.get("lease_seconds", 600),
                    user_id=user_id,
                )
                return {"ok": True, "claim": claim}
            if name in {"userio.workspace.complete", "userio.workspace.fail"}:
                transition = (
                    self._store.complete_workspace_claim
                    if name.endswith("complete") else self._store.fail_workspace_claim
                )
                claim = transition(
                    event_seq=arguments.get("event_seq"),
                    worker_id=self._required(arguments, "worker_id"),
                    lease_token=self._required(arguments, "lease_token"),
                    detail=self._optional(arguments, "detail"),
                    user_id=user_id,
                )
                return {"ok": True, "claim": claim}
            if name == "userio.workspace.claim_log":
                return {"ok": True, **self._store.workspace_claim_log(
                    event_seq=arguments.get("event_seq"), user_id=user_id,
                )}
            if name == "userio.workspace.exclusions.list":
                return {
                    "ok": True,
                    "exclusions": self._store.workspace_exclusions(user_id=user_id),
                }
            if name == "userio.workspace.exclusions.add":
                exclusion = self._store.add_workspace_exclusion(
                    conversation_id=self._required(arguments, "conversation_id"),
                    reason=self._optional(arguments, "reason") or "",
                    user_id=user_id,
                )
                return {"ok": True, "exclusion": exclusion}
            if name == "userio.workspace.exclusions.remove":
                return {
                    "ok": True,
                    "removed": self._store.remove_workspace_exclusion(
                        conversation_id=self._required(arguments, "conversation_id"),
                        user_id=user_id,
                    ),
                }
            if name == "userio.workspace.policy.get":
                return {"ok": True, **self._store.workspace_policy(user_id=user_id)}
            if name == "userio.workspace.policy.set_default":
                return {"ok": True, **self._store.set_workspace_default(
                    conversation_kind=self._required(arguments, "conversation_kind"),
                    enabled=arguments.get("enabled"), user_id=user_id,
                )}
            if name == "userio.workspace.policy.telegram_bots.set":
                return {"ok": True, **self._store.set_workspace_default(
                    conversation_kind="telegram_bot",
                    enabled=arguments.get("enabled"), user_id=user_id,
                )}
            if name == "userio.workspace.policy.chats.list":
                return {"ok": True, "chats": self._store.workspace_chat_rules(
                    user_id=user_id, source=arguments.get("source", ""),
                    account_ref=arguments.get("account_ref", ""),
                    peer_id=arguments.get("peer_id", ""),
                    conversation_kind=arguments.get("conversation_kind", ""),
                    action=arguments.get("action", ""), query=arguments.get("query", ""),
                    limit=arguments.get("limit", 100), offset=arguments.get("offset", 0)),
                        "revision": self._store.workspace_policy(user_id=user_id)["revision"]}
            if name == "userio.workspace.policy.chats.set":
                return {"ok": True, "chat": self._store.set_workspace_chat_rule(
                    conversation_id=self._required(arguments, "conversation_id"),
                    action=self._required(arguments, "action"),
                    reason=self._optional(arguments, "reason") or "", user_id=user_id,
                )}
            if name == "userio.workspace.policy.evaluate":
                return {"ok": True, "chat": self._store.evaluate_workspace_chat(
                    conversation_id=self._required(arguments, "conversation_id"), user_id=user_id,
                )}
            if name == "userio.workspace.triage.get":
                return {"ok": True, "settings": self._store.workspace_triage_settings(user_id=user_id)}
            if name == "userio.workspace.triage.set":
                return {"ok": True, "settings": self._store.set_workspace_triage_settings(
                    enabled=arguments.get("enabled"), threshold=arguments.get("threshold"),
                    min_confidence=arguments.get("min_confidence"), user_id=user_id,
                )}
            if name == "userio.workspace.triage.feedback":
                return self._service.feedback_workspace_triage(
                    event_seq=arguments.get("event_seq"),
                    request_id=self._required(arguments, "request_id"),
                    label=self._required(arguments, "label"),
                    actor=f"mcp:{principal.username}", user_id=user_id,
                )
            if name == "userio.inbox.list_new":
                ignored_chats = self._ignored_chats(arguments)
                channel = self._optional(arguments, "channel")
                messages = self._store.new_messages(
                    source=channel, limit=int(arguments.get("limit", 50)), user_id=user_id
                )
                return {"ok": True, "messages": [
                    message for message in messages
                    if str(message["conversation_id"]) not in ignored_chats
                ]}
            if name == "userio.conversation.get":
                ignored_chats = self._ignored_chats(arguments)
                conversation_id = self._required(arguments, "conversation_id")
                if conversation_id in ignored_chats:
                    return {"ok": True, "conversation": None}
                return {"ok": True, "conversation": self._store.conversation(
                    conversation_id, user_id=user_id
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
                if not self._store.send_enabled(user_id=principal.user_id):
                    return {"ok": False, "error": "outbound_delivery_disabled"}
                return self._approve(arguments, principal)
            if name == "userio.conversation.delete_local":
                return self._delete_conversation(arguments, principal)
            if name == "userio.accounts.list":
                return {"ok": True, "accounts": self._store.accounts(user_id=user_id)}
            if name == "userio.ai.propose":
                return self._propose(arguments, principal)
        except PermissionError as error:
            return {"ok": False, "error": str(error)}
        except AdapterNotSupported as error:
            return {"ok": False, "error": str(error)}
        except RuntimeError as error:
            return {"ok": False, "error": str(error)}
        except (KeyError, TypeError, ValueError) as error:
            return {"ok": False, "error": str(error).strip("'") or "invalid_arguments"}
        return {"ok": False, "error": "unknown_tool"}

    def _approve(self, arguments: dict[str, Any], principal: UserPrincipal) -> dict[str, Any]:
        if arguments.get("confirm") is not True:
            return {"ok": False, "error": "exact_confirmation_required"}
        keys = ("expected_text", "expected_chat_id", "expected_attachments")
        snapshot = None
        if any(key in arguments for key in keys):
            if (not all(key in arguments for key in keys)
                    or not isinstance(arguments["expected_text"], str)
                    or not isinstance(arguments["expected_chat_id"], str)
                    or not isinstance(arguments["expected_attachments"], list)
                    or not all(isinstance(item, str) for item in arguments["expected_attachments"])):
                raise ValueError("complete_draft_snapshot_required")
            snapshot = {key: arguments[key] for key in keys}
        draft = self._service.approve(
            self._required(arguments, "draft_id"), user_id=principal.user_id,
            expected_snapshot=snapshot,
        )
        conversation = self._store.conversation(draft.conversation_id, user_id=principal.user_id) or {}
        return {
            "ok": True, "draft": self._draft(draft),
            "sent": draft.status == "approved",
            "receipt": draft.receipt or None,
            "channel": str(conversation.get("source") or ""),
            "delivery_note": self._delivery_note(conversation),
        }

    @staticmethod
    def _delivery_note(conversation: dict[str, Any]) -> str:
        source = str(conversation.get("source") or "")
        if source == "sms":
            return "Android gateway accepted the SMS; carrier delivery is not guaranteed."
        if source.startswith("gmail:"):
            return "Sent through the Gmail account."
        if source.startswith("chatgpt"):
            return "Sent through the ChatGPT web session."
        if source == "telegram":
            return "Sent through the Telegram account."
        if source == "whatsapp":
            return "Sent through the WhatsApp bridge."
        return f"Sent through route {conversation.get('route_id') or source or 'unknown'}."

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
        drafts = self._service.propose_for_approval(
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
    def _ignored_chats(arguments: dict[str, Any]) -> frozenset[str]:
        values = arguments.get("ignored_chats", [])
        if not isinstance(values, list) or len(values) > 100:
            raise ValueError("ignored_chats must be an array of at most 100 chat ids")
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("ignored_chats must contain non-empty chat ids")
        return frozenset(value.strip() for value in values)

    @staticmethod
    def _workspace_event(event: dict[str, Any]) -> dict[str, Any]:
        payload = dict(event)
        body = str(payload.get("body") or "")
        if len(body) > _WORKSPACE_EVENT_BODY_LIMIT:
            payload["body"] = body[:_WORKSPACE_EVENT_BODY_LIMIT]
            payload["body_truncated"] = True
        return payload

    @staticmethod
    def _draft(draft: Any) -> dict[str, str]:
        payload = {
            "id": draft.id, "conversation_id": draft.conversation_id,
            "body": draft.body, "status": draft.status,
        }
        receipt = getattr(draft, "receipt", "")
        if receipt:
            payload["receipt"] = receipt
        return payload
