"""Authenticated MCP surface for user-scoped UserIO operations."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Any

from .adapters import AdapterNotSupported, UnifiedChannels
from .contracts import InboxMessage, UserPrincipal
from .delivery_receipt import delivery_fields
from .token_permissions import READ_ONLY_TOOLS, MEETING_RECORDING_TOOLS
from .service import UserIOService
from .store import SQLiteUserIOStore


_WORKSPACE_EVENT_BODY_LIMIT = 4096
_MCP_IMAGE_MAX_BYTES = 2 * 1024 * 1024
_MCP_IMAGE_MAX_TOTAL_BYTES = 4 * 1024 * 1024
_MCP_IMAGE_MAX_COUNT = 4
_MCP_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


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
    ToolSpec("userio.channels.list", "List this user's chats across connected channels, or search their retained history when a search query is given. Read chats from the MCP CallToolResult `structuredContent.chats` array (or `chats` if your client unwraps it); do not assume `chats` is a top-level property on the MCP wrapper.", _schema({
        "channel": {"type": "string", "enum": ["mail", "telegram", "whatsapp", "matrix", "vk", "sms", "chatgpt", "max"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "ignored_chats": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
        "search": {"type": "string", "minLength": 2, "maxLength": 256},
        "scope": {"type": "string", "enum": ["conversation", "message"]},
        "mode": {"type": "string", "enum": ["text", "semantic", "hybrid"]},
        "match_type": {"type": "string", "enum": ["all", "exact", "fuzzy"]},
        "offset": {"type": "integer", "minimum": 0},
        "conversation_ids": {"type": "array", "items": {"type": "string", "maxLength": 128}, "maxItems": 100},
        "account_ref": {"type": "string", "maxLength": 128},
    })),
    ToolSpec("userio.channels.search", "Search this user's chats and retained message history by name, text, conversation id or message id. The same core as the web search: scope=conversation returns one row per chat, scope=message returns one row per matching message with a deep link. Read the rows from `structuredContent.results` and pass a found conversation_id to userio.channels.read.", _schema({
        "query": {"type": "string", "minLength": 2, "maxLength": 256},
        "conversation_id": {"type": "string", "maxLength": 128},
        "message_id": {"type": "string", "maxLength": 256},
        "channel": {"type": "string", "enum": ["mail", "telegram", "whatsapp", "matrix", "vk", "sms", "chatgpt", "max", "yandex"]},
        "scope": {"type": "string", "enum": ["conversation", "message"]},
        "mode": {"type": "string", "enum": ["text", "semantic", "hybrid"]},
        "match_type": {"type": "string", "enum": ["all", "exact", "fuzzy"]},
        "account_ref": {"type": "string", "maxLength": 128},
        "conversation_ids": {"type": "array", "items": {"type": "string", "maxLength": 128}, "maxItems": 100},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "offset": {"type": "integer", "minimum": 0},
        "ignored_chats": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
    })),
    ToolSpec("userio.channels.export", "Export one user-owned chat as a Markdown file: every message with its author, direction and time, voice messages marked as transcribed speech with the stored transcript, replies marked, and media only when include_images is true. The result carries the file path, the authenticated download path and a bounded preview, so the chat is read from the file instead of paged into the conversation.", _schema({
        "conversation_id": {"type": "string", "maxLength": 128},
        "include_images": {"type": "boolean", "default": False},
        "since": {"type": "number"},
        "until": {"type": "number"},
        "max_messages": {"type": "integer", "minimum": 1, "maximum": 50000, "default": 20000},
    })),
    ToolSpec("userio.contacts.resolve", "Resolve a contact that is not in the history yet into a peer the account can write to: a Telegram username or phone number, a phone number for SMS or WhatsApp, a mail address, a VK profile or link, or a Yandex user id. The answer names the account, the channel and the provider-native peer, so the next call can write to it. Nothing is sent to the contact here. A Telegram identifier needs the connector's own live session: when that session is down the answer carries code connector_unreachable or account_not_connected, and userio.messenger.capabilities reports first_contact.supported=false for the same account.", _schema({
        "account_ref": {"type": "string", "maxLength": 128},
        "identifier": {"type": "string", "minLength": 1, "maxLength": 256},
    }, ["account_ref", "identifier"])),
    ToolSpec("userio.contacts.start", "Start a conversation with a contact that is not in the history yet and send the first message. The identifier is resolved to a provider-native peer (Telegram username or phone number, phone number for SMS or WhatsApp, mail address, VK profile, Yandex user id), the canonical chat is opened, and the text is delivered through the same durable path as every other outbound message. The request_id makes a retry reuse the same message instead of sending it twice. The answer reports what the transport really proved: delivery_status=delivered only with a provider confirmation, delivery_status=smtp_accepted when the mail transport merely took the letter.", _schema({
        "account_ref": {"type": "string", "maxLength": 128},
        "identifier": {"type": "string", "minLength": 1, "maxLength": 256},
        "text": {"type": "string", "minLength": 1, "maxLength": 65536},
        "request_id": {"type": "string", "minLength": 16, "maxLength": 80},
    }, ["account_ref", "identifier", "text", "request_id"])),
    ToolSpec("userio.channels.read", "Read one user-owned chat or message. Exact stored task-reply matches may include task_reply_association metadata; unresolved replies are never inferred and this metadata grants no send authority. Image attachments are returned as MCP image blocks by default (up to 4 images / 4 MiB); set include_images=false to opt out. Audio bytes are not sent: the result states whether a transcript reached the model.", _schema({
        "channel": {"type": "string", "enum": ["mail", "telegram", "whatsapp", "matrix", "vk", "sms", "chatgpt", "max"]},
        "chat_id": {"type": "string"}, "message_id": {"type": "string"},
        "before_id": {"type": "integer", "minimum": 1, "maximum": 2147483647},
        "history_limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "ignored_chats": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
        "include_images": {"type": "boolean", "default": True},
    })),
    ToolSpec("userio.channels.download", "Download the selected Telegram or MAX attachment from a stored message. The result includes original bytes, filename, MIME, byte length and SHA-256; caller URLs are not accepted.", _schema({
        "file_ref": {"type": "string"}, "mode":{"type":"string","enum":["base64","mcp_resource"],"default":"base64"},
        "attachment_index":{"type":"integer","minimum":0,"maximum":7,"default":0},
        "include_images":{"type":"boolean","default":True},
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
    ToolSpec("userio.inbox.list_new", "Compatibility alias: list this user's unread messages. Exact stored task-reply matches may include task_reply_association metadata; unresolved replies are never inferred and this metadata grants no send authority.", _schema({
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "channel": {"type": "string", "enum": ["mail", "telegram", "whatsapp", "matrix", "vk", "sms", "chatgpt", "max"]},
        "ignored_chats": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
    })),
    ToolSpec("userio.conversation.get", "Compatibility alias: read a conversation. Exact stored task-reply matches may include task_reply_association metadata; unresolved replies are never inferred and this metadata grants no send authority.", _schema({
        "conversation_id": {"type": "string"},
        "ignored_chats": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
    }, ["conversation_id"])),
    ToolSpec("userio.message.mark_seen", "Mark one user-owned message as seen.", _schema({
        "source": {"type": "string"}, "message_id": {"type": "string"},
    }, ["source", "message_id"])),
    ToolSpec("userio.messages.react", "Set an emoji reaction on a stored message; visible to the peer.", _schema({
        "conversation_id": {"type": "string"}, "message_id": {"type": "string"},
        "emoji": {"type": "string", "maxLength": 16}, "confirm": {"type": "boolean"},
    }, ["conversation_id", "message_id", "emoji", "confirm"])),
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
    }, ["draft_id", "confirm", "expected_text", "expected_chat_id", "expected_attachments"])),
    ToolSpec("userio.draft.reconcile_delivery", "Resolve one in-flight Telegram delivery against the provider record.", _schema({
        "draft_id": {"type": "string"}, "confirm": {"type": "boolean"},
        "expected_text": {"type": "string"},
        "expected_chat_id": {"type": "string", "description": "Exact stored draft conversation_id."},
        "expected_attachments": {"type": "array", "items": {"type": "string"},
                                 "description": "Must be empty for text-only drafts."},
        "provider_history": {"type": "boolean",
                             "description": "Read the provider's own history when the connector journal "
                                            "cannot speak about an older attempt."},
        "connector_available_since": {"type": "number",
                                      "description": "Unix time from which the Telegram connector was able to "
                                                     "serve again. Required for the provider-history resolution."},
    }, ["draft_id", "confirm", "expected_text", "expected_chat_id", "expected_attachments"])),
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



from .mcp_catalog import MESSENGER_SPECS
TOOL_SPECS = (*TOOL_SPECS, *MESSENGER_SPECS)

TOOL_CAPABILITIES = {
    'userio.messenger.capabilities': 'read',
    'userio.messenger.events': 'read',
    'userio.messenger.history': 'read',
    'userio.messenger.resolve': 'read',
    'userio.messenger.media': 'download',
    'userio.messenger.get': 'read',
    'userio.messenger.propose': 'send',
    'userio.messenger.telegram_admin.propose': 'send',
    'userio.messenger.upload': 'send',
    'userio.messenger.approve': 'send',
    "userio.channels.list": "read",
    "userio.channels.search": "read",
    "userio.channels.export": "read",
    "userio.contacts.resolve": "read",
    "userio.contacts.start": "send",
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
    "userio.messages.react": "send",
    "userio.draft.create": "send",
    "userio.draft.update": "send",
    "userio.draft.delete": "send",
    "userio.draft.approve_send": "send",
    "userio.draft.reconcile_delivery": "send",
    "userio.conversation.delete_local": "read",
    "userio.accounts.list": "read",
    "userio.ai.propose": "send",
}

from .mcp_catalog import MATRIX_CALL_SPECS, TELEGRAM_CALL_SPECS, TELEMOST_SPECS, DIAGNOSTIC_SPECS
TOOL_SPECS = (*TOOL_SPECS, *MATRIX_CALL_SPECS, *TELEGRAM_CALL_SPECS, *TELEMOST_SPECS, *DIAGNOSTIC_SPECS)
TOOL_CAPABILITIES.update({'userio.diagnostic.create':'send','userio.diagnostic.get':'read','userio.diagnostic.message':'send'})
TOOL_CAPABILITIES.update({'userio.calls.matrix.start':'send','userio.calls.matrix.get':'read','userio.calls.matrix.stop':'send'})
TOOL_CAPABILITIES.update({'userio.calls.telegram.start':'send','userio.calls.telegram.get':'read','userio.calls.telegram.stop':'send'})
TOOL_CAPABILITIES.update({'userio.meetings.telemost.start':'send','userio.meetings.telemost.get':'read','userio.meetings.telemost.stop':'send'})


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
            if (not principal.read_only or spec.name in READ_ONLY_TOOLS)
            and (principal.messenger_grants is None or any(g['access']=='read_write' for g in principal.messenger_grants)
                 or spec.name in READ_ONLY_TOOLS
                 or spec.name in {'userio.messenger.telegram_admin.propose','userio.messenger.approve'}
                 and any(g.get('telegram_admin') for g in principal.messenger_grants))
            and (spec.name!='userio.messenger.telegram_admin.propose'
                 or principal.messenger_accounts is None and principal.messenger_grants is None
                 or any(g.get('telegram_admin') for g in principal.messenger_grants or ()))
            and (not principal.meeting_recorder or spec.name in MEETING_RECORDING_TOOLS)
            and (principal.messenger_accounts is None or spec.name.startswith('userio.messenger.')
                 or principal.messenger_grants is not None and spec.name in {
                    'userio.accounts.list','userio.channels.list','userio.channels.search',
                    'userio.channels.export','userio.channels.read','userio.channels.download',
                    'userio.channels.send_draft','userio.contacts.resolve','userio.contacts.start'}
                 or principal.messenger_grants is None and spec.name.startswith('userio.calls.telegram.')
                 and any(a.startswith('telegram:') for a in principal.messenger_accounts))
            and ((required := TOOL_CAPABILITIES.get(spec.name)) is None
                 or self._store.capability_enabled(required, user_id=principal.user_id))
        )
        return {"tools": [spec.as_dict() for spec in specs]}

    def resource_manifest(self, *, principal: UserPrincipal | None = None) -> dict[str, Any]:
        principal = self._principal if principal is None else principal
        if principal.messenger_accounts is not None or principal.meeting_recorder: return {'resources':[]}
        if not self._store.capability_enabled("read", user_id=principal.user_id):
            return {"resources": []}
        return {"resources": [
            {"uri": "userio://accounts", "name": "Connected accounts", "mimeType": "application/json"},
            {"uri": "userio://conversations", "name": "Recent conversations", "mimeType": "application/json"},
            {"uri": "userio://inbox/unread", "name": "Unread inbox", "mimeType": "application/json"},
        ]}

    def resource_template_manifest(self, *, principal: UserPrincipal | None = None) -> dict[str, Any]:
        principal = self._principal if principal is None else principal
        if principal.messenger_accounts is not None or principal.meeting_recorder: return {'resourceTemplates':[]}
        if not self._store.capability_enabled("read", user_id=principal.user_id):
            return {"resourceTemplates": []}
        return {"resourceTemplates": [
            {
                "uriTemplate": "userio://conversations/{conversationId}",
                "name": "Conversation by id",
                "mimeType": "application/json",
            },
        ]}

    def _attach_task_reply_associations(
        self, messages: list[Any], *, conversation_id: str, user_id: str,
    ) -> None:
        if not conversation_id:
            return
        for message in messages:
            if (not isinstance(message, dict)
                    or str(message.get("direction") or "incoming") != "incoming"):
                continue
            if "task_reply_association" in message:
                continue
            message_id = str(message.get("message_id") or "")
            if not message_id:
                continue
            association = self._service.task_reply_association_for_message(
                conversation_id=conversation_id, message_id=message_id, user_id=user_id,
            )
            if association is not None:
                message["task_reply_association"] = association

    def _include_read_media(
        self, result: dict[str, Any], *, arguments: dict[str, Any], user_id: str,
    ) -> dict[str, Any]:
        """Attach bounded MCP image blocks and make transcript delivery explicit."""
        include_images = arguments.get("include_images", True)
        if type(include_images) is not bool:
            raise ValueError("include_images must be a boolean")
        record = result.get("chat") or result.get("message") or {}
        messages = record.get("messages") if isinstance(record, dict) else None
        if not isinstance(messages, list):
            messages = [record] if isinstance(record, dict) and record else []
        conversation_id = str(
            record.get("id") or record.get("conversation_id") or ""
        ) if isinstance(record, dict) else ""
        self._attach_task_reply_associations(
            messages, conversation_id=conversation_id, user_id=user_id,
        )
        media_delivery: list[dict[str, Any]] = []
        content_blocks: list[dict[str, Any]] = []
        total_bytes = 0
        image_count = 0
        image_attempts = 0
        channels = UnifiedChannels(self._store, self._service, user_id)

        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            attachments = message.get("attachments")
            if not isinstance(attachments, list):
                continue
            source = str(message.get("source") or arguments.get("channel") or "")
            adapter_name = source.split(":", 1)[0]
            if adapter_name in {"gmail", "email"}:
                adapter_name = "mail"
            message_id = str(message.get("message_id") or "")
            for attachment in reversed(attachments):
                if not isinstance(attachment, dict):
                    continue
                kind = str(attachment.get("kind") or "").casefold()
                content_type = str(attachment.get("content_type") or "").split(";", 1)[0].lower()
                is_image = kind in {"image", "photo", "picture", "sticker"} or content_type.startswith("image/")
                is_audio = kind in {"audio", "voice"} or content_type.startswith("audio/")
                item: dict[str, Any] = {
                    "message_id": message_id,
                    "attachment_idx": int(attachment.get("idx") or 0),
                    "kind": kind or ("image" if is_image else "audio" if is_audio else "file"),
                }
                if message_id and adapter_name:
                    item["file_ref"]=f"{adapter_name}:{message_id}"
                if message_id and adapter_name:
                    item["file_ref"]=f"{adapter_name}:{message_id}"
                if is_image:
                    item["image_included"] = False
                    if not include_images:
                        item["opted_out"] = True
                        item["reason"] = "caller_opted_out"
                    elif image_attempts >= _MCP_IMAGE_MAX_COUNT:
                        item["reason"] = "image_count_limit"
                    elif total_bytes >= _MCP_IMAGE_MAX_TOTAL_BYTES:
                        item["reason"] = "image_byte_limit"
                    elif not message_id or not adapter_name:
                        item["reason"] = "message_reference_missing"
                    else:
                        image_attempts += 1
                        try:
                            adapter = channels.adapter(adapter_name)
                            bounded_download = getattr(adapter, "download_for_ai", None)
                            if callable(bounded_download):
                                kwargs: dict[str, Any] = {
                                    "file_ref": message_id,
                                    "max_bytes": min(_MCP_IMAGE_MAX_BYTES,
                                                      _MCP_IMAGE_MAX_TOTAL_BYTES - total_bytes),
                                }
                                if adapter_name == "max":
                                    kwargs["attachment_idx"] = item["attachment_idx"]
                                file = bounded_download(**kwargs)
                            else:
                                declared_size = attachment.get("size")
                                if type(declared_size) is not int or not 0 < declared_size <= _MCP_IMAGE_MAX_BYTES:
                                    item["reason"] = "bounded_download_unavailable"
                                    media_delivery.append(item)
                                    continue
                                file_ref = str(attachment.get("attachment_id") or message_id)
                                if adapter_name == "vk" and not attachment.get("attachment_id"):
                                    file_ref = f"{message_id}:{item['attachment_idx']}"
                                file = adapter.download(file_ref=file_ref)
                            data = getattr(file, "data", None)
                            mime = str(getattr(file, "content_type", "") or "").split(";", 1)[0].lower()
                            if not isinstance(data, bytes) or not data or len(data) > _MCP_IMAGE_MAX_BYTES:
                                item["reason"] = "image_unavailable_or_too_large"
                            else:
                                signatures = {
                                    "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
                                    "image/jpeg": data.startswith(b"\xff\xd8\xff"),
                                    "image/gif": data.startswith((b"GIF87a", b"GIF89a")),
                                    "image/webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP",
                                }
                                mime = mime if mime in _MCP_IMAGE_MIMES else next(
                                    (candidate for candidate, valid in signatures.items() if valid), "",
                                )
                                if not mime or not signatures[mime]:
                                    item["reason"] = "unsupported_or_invalid_image"
                                elif total_bytes + len(data) > _MCP_IMAGE_MAX_TOTAL_BYTES:
                                    item["reason"] = "image_byte_limit"
                                else:
                                    image_count += 1
                                    total_bytes += len(data)
                                    item.update({"image_included": True, "content_type": mime,
                                                 "size": len(data), "vision_instruction": "describe_image"})
                                    content_blocks.extend([
                                        {"type": "text", "text": (
                                            "Изображение из сообщения " + message_id
                                            + ": просмотри его и скажи, что на нём изображено. "
                                              "Если деталей не видно, прямо укажи это."
                                        )},
                                        {"type": "image", "data": base64.b64encode(data).decode("ascii"),
                                         "mimeType": mime},
                                    ])
                        except Exception as error:
                            item["reason"] = f"download_failed:{type(error).__name__}"
                    media_delivery.append(item)
                elif is_audio:
                    transcript = str(attachment.get("transcript") or "").strip()
                    status = str(attachment.get("transcription_status") or (
                        "completed" if transcript else "unknown"
                    ))
                    item.update({
                        "transcription_status": status,
                        "transcript_available": bool(transcript),
                        "transcript_in_message_body": bool(transcript),
                        "audio_bytes_included": False,
                        "ai_input": ("transcript_only" if transcript else "audio_not_sent_and_not_transcribed"),
                        "delivery_note": (
                            "В модель передан текст транскрипции; аудио не отправлено."
                            if transcript else
                            "Аудио не отправлено модели; транскрипции нет."
                        ),
                    })
                    media_delivery.append(item)
        result["media_delivery"] = media_delivery
        if content_blocks:
            result["_mcp_content_blocks"] = content_blocks
        return result

    def read_resource(
        self, uri: str, *, principal: UserPrincipal | None = None
    ) -> dict[str, Any]:
        principal = self._principal if principal is None else principal
        if principal.meeting_recorder:
            raise PermissionError('meeting_recording_scope_only')
        user_id = principal.user_id
        if principal.messenger_accounts is not None: raise PermissionError('messenger_scope_only')
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
        if principal.messenger_grants is not None:
            from .scoped_permissions import enforce, dispatch_scoped
            try:
                enforce(self._store, principal, name, arguments)
                if name == 'userio.accounts.list':
                    return {'ok':True, 'accounts':[{k:v for k,v in a.items() if k!='credential_ref'}
                        for a in self._store.accounts(user_id=principal.user_id) if a['id'] in principal.messenger_accounts]}
                if name.startswith('userio.messenger.'):
                    return dispatch_scoped(self._store,self._service,principal,name,arguments)
                if name.startswith('userio.channels.'):
                    return dispatch_scoped(self._store,self._service,principal,name,arguments)
                if name.startswith('userio.contacts.'):
                    return dispatch_scoped(self._store,self._service,principal,name,arguments)
            except (PermissionError,ValueError,KeyError) as error:
                return {'ok':False,'error':str(error).strip("'")}
        if principal.meeting_recorder and name not in MEETING_RECORDING_TOOLS | {'tools/list', 'tools/call'}:
            return {'ok': False, 'error': 'meeting_recording_scope_only'}
        if (principal.messenger_accounts is not None and not name.startswith(('userio.messenger.','userio.calls.telegram.'))
                and name not in {'tools/list','tools/call'}):
            return {'ok':False,'error':'messenger_scope_only'}
        if principal.read_only and name not in READ_ONLY_TOOLS | {"tools/list", "tools/call"}:
            return {"ok": False, "error": "read_only_token"}
        if name.startswith('userio.messenger.'):
            from .messenger import dispatch
            return dispatch(self._store, self._service, principal, name, arguments)
        user_id = principal.user_id
        if name.startswith(('userio.calls.matrix.','userio.calls.telegram.','userio.meetings.telemost.','userio.diagnostic.')) or name == "userio.channels.read" and arguments.get("channel") == "max":
            # Keep the compatibility HTTP surface on the authoritative MAX
            # reader, including its bounded cached-history contract.
            from .mcp_dispatch import UserIOToolDispatcher
            result = UserIOToolDispatcher(self._store, self._service).dispatch(
                name, arguments, principal=principal,
            )
            if name == "userio.channels.read" and result.get("ok"):
                result = self._include_read_media(result, arguments=arguments, user_id=user_id)
            return result
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
            if name == "userio.channels.search":
                ignored_chats = self._ignored_chats(arguments)
                from .search_surface import search_tool_response
                return search_tool_response(
                    self._store, arguments, user_id=user_id,
                    source=self._optional(arguments, "channel"), ignored=ignored_chats,
                )
            if name == "userio.channels.export":
                from .chat_export import export_conversation
                conversation_id = str(arguments.get("conversation_id") or "").strip()
                if not conversation_id:
                    raise ValueError("conversation_id is required")
                result = export_conversation(
                    self._store, conversation_id, user_id=user_id,
                    include_images=bool(arguments.get("include_images")),
                    since=arguments.get("since"), until=arguments.get("until"),
                    max_messages=int(arguments.get("max_messages") or 20000),
                )
                if not result:
                    return {"ok": False, "error": "conversation not found"}
                markdown = str(result.pop("markdown"))
                result["preview"] = markdown[:2000]
                if len(markdown.encode("utf-8")) <= 32768:
                    result["content"] = markdown
                return result
            if name == "userio.contacts.resolve":
                from .contact_start import ContactStartError
                account_ref = str(arguments.get("account_ref") or "").strip()
                identifier = str(arguments.get("identifier") or "").strip()
                if not account_ref or not identifier:
                    raise ValueError("account_ref and identifier are required")
                try:
                    resolved = self._service.contact_starter.resolve(
                        user_id=user_id, account_ref=account_ref, identifier=identifier,
                        need_send=False,
                    )
                except ContactStartError as error:
                    return {"ok": False, "error": str(error), "code": error.code}
                return {"ok": True, "peer": resolved.as_dict()}
            if name == "userio.contacts.start":
                from .contact_start import ContactStartError
                account_ref = str(arguments.get("account_ref") or "").strip()
                identifier = str(arguments.get("identifier") or "").strip()
                text = str(arguments.get("text") or "").strip()
                request_id = str(arguments.get("request_id") or "").strip()
                if not account_ref or not identifier or not text or not request_id:
                    raise ValueError(
                        "account_ref, identifier, text and request_id are required")
                try:
                    return self._service.contact_starter.start(
                        user_id=user_id, account_ref=account_ref, identifier=identifier,
                        text=text, request_id=request_id,
                    )
                except ContactStartError as error:
                    return {"ok": False, "error": str(error), "code": error.code}
            if name == "userio.channels.list":
                ignored_chats = self._ignored_chats(arguments)
                query = str(arguments.get("search") or "").strip()
                if query:
                    from .search_surface import search_response
                    result = search_response(
                        self._store, query, arguments, user_id=user_id,
                        source=self._optional(arguments, "channel"), ignored=ignored_chats,
                    )
                    if result.get("scope") == "conversation":
                        # Keep the list tool's established response shape so callers
                        # can pass a found chat straight to userio.channels.read.
                        result["chats"] = [
                            {
                                **row,
                                "channel": row["source"],
                                "title": row["display_name"] or row["identity_id"] or row["sender"],
                                "last_message_snippet": row["preview"],
                                "unread": row["unread_count"],
                            }
                            for row in result.get("results", [])
                        ]
                    return result
                adapter = channels.adapter(self._optional(arguments, "channel"))
                chats = adapter.list(limit=int(arguments.get("limit", 100)))
                if "conversation_ids" in arguments:
                    allowed_ids = {
                        str(value).strip() for value in arguments["conversation_ids"]
                        if str(value).strip()
                    }
                    chats = [chat for chat in chats if str(chat.get("id", "")) in allowed_ids]
                return {"ok": True, "chats": [
                    chat for chat in chats
                    if str(chat["id"]) not in ignored_chats
                ]}
            if name == "userio.channels.read":
                ignored_chats = self._ignored_chats(arguments)
                adapter = channels.adapter(self._optional(arguments, "channel"))
                before_id = arguments.get("before_id")
                history_limit = arguments.get("history_limit")
                if before_id is not None or history_limit is not None:
                    if self._optional(arguments, "channel") != "telegram":
                        raise ValueError("history pagination requires channel=telegram")
                    result = adapter.read(
                        chat_id=self._optional(arguments, "chat_id"),
                        message_id=self._optional(arguments, "message_id"),
                        before_id=before_id,
                        history_limit=50 if history_limit is None else history_limit,
                    )
                    if before_id is None and history_limit is not None and "chat" in result:
                        result["chat"]["messages"] = result["chat"].get("messages", [])[-history_limit:]
                else:
                    result = adapter.read(
                        chat_id=self._optional(arguments, "chat_id"),
                        message_id=self._optional(arguments, "message_id"),
                    )
                record = result.get("chat") or result.get("message") or {}
                conversation_id = str(record.get("id") or record.get("conversation_id") or "")
                if conversation_id in ignored_chats:
                    raise KeyError("chat not found")
                return self._include_read_media(
                    {"ok": True, **result}, arguments=arguments, user_id=user_id,
                )
            if name == "userio.channels.download":
                file_ref=self._required(arguments,"file_ref")
                attachment_index=arguments.get('attachment_index',0)
                if type(attachment_index) is not int or not 0<=attachment_index<8:
                    raise ValueError('invalid_attachment_index')
                mode=arguments.get('mode','base64')
                if mode=='mcp_resource':
                    include_images=arguments.get('include_images',True)
                    if type(include_images) is not bool: raise ValueError('include_images_must_be_boolean')
                    channel,separator,provider_ref=file_ref.partition(':')
                    if not separator or channel not in {'telegram','max'}:
                        raise ValueError('mcp_resource_mode_requires_telegram_or_max')
                    from .mcp_files import MAX_MCP_FILE_BYTES, mcp_attachment
                    if channel=='telegram':
                        download_args={'file_ref':provider_ref,'max_bytes':MAX_MCP_FILE_BYTES}
                        if attachment_index:
                            download_args['attachment_index']=attachment_index
                        file=channels.adapter('telegram').download_for_ai(**download_args)
                    else:
                        file=channels.download(file_ref=file_ref,attachment_index=attachment_index,
                                               max_bytes=MAX_MCP_FILE_BYTES)
                    metadata,blocks=mcp_attachment(file,include_images=include_images)
                    return {'ok':True,'file':metadata,'_mcp_content_blocks':blocks}
                if mode!='base64': raise ValueError('invalid_download_mode')
                from .mcp_files import MAX_MCP_FILE_BYTES
                channel,_,provider_ref=file_ref.partition(':')
                if channel=='telegram':
                    download_args={'file_ref':provider_ref,'max_bytes':MAX_MCP_FILE_BYTES}
                    if attachment_index:
                        download_args['attachment_index']=attachment_index
                    file=channels.adapter('telegram').download_for_ai(**download_args)
                else:
                    file = channels.download(file_ref=file_ref,attachment_index=attachment_index,
                                             max_bytes=MAX_MCP_FILE_BYTES)
                return {"ok": True, "file": {
                    "filename": file.filename, "content_type": file.content_type,
                    "size": len(file.data), "sha256": hashlib.sha256(file.data).hexdigest(),
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
                    "Автоматическая отправка отключена. Чтобы отправить сообщение, "
                    "подтвердите этот черновик."
                    if manual_lock else
                    "Черновик сохранён. Чтобы отправить сообщение, "
                    "подтвердите этот текст и получателя."
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
                        automatic=True,
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
                messages = [
                    message for message in messages
                    if str(message["conversation_id"]) not in ignored_chats
                ]
                for message in messages:
                    self._attach_task_reply_associations(
                        [message], conversation_id=str(message["conversation_id"]),
                        user_id=user_id,
                    )
                return {"ok": True, "messages": messages}
            if name == "userio.conversation.get":
                ignored_chats = self._ignored_chats(arguments)
                conversation_id = self._required(arguments, "conversation_id")
                if conversation_id in ignored_chats:
                    return {"ok": True, "conversation": None}
                conversation = self._store.conversation(conversation_id, user_id=user_id)
                if conversation is not None:
                    self._attach_task_reply_associations(
                        conversation.get("messages", []),
                        conversation_id=conversation_id, user_id=user_id,
                    )
                return {"ok": True, "conversation": conversation}
            if name == "userio.message.mark_seen":
                return {"ok": True, "changed": self._store.mark_seen(
                    source=self._required(arguments, "source"),
                    message_id=self._required(arguments, "message_id"), user_id=user_id,
                )}
            if name == "userio.messages.react":
                if not self._store.send_enabled(user_id=principal.user_id):
                    return {"ok": False, "error": "outbound_delivery_disabled"}
                return self._service.react_to_message(
                    conversation_id=self._required(arguments, "conversation_id"),
                    message_id=self._required(arguments, "message_id"),
                    emoji=self._required(arguments, "emoji"),
                    confirm=arguments.get("confirm") is True, user_id=user_id,
                )
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
            if name == "userio.draft.reconcile_delivery":
                return self._reconcile_delivery(arguments, principal)
            if name == "userio.conversation.delete_local":
                return self._delete_conversation(arguments, principal)
            if name == "userio.accounts.list":
                accounts = self._store.accounts(user_id=user_id)
                if principal.read_only:
                    accounts = [{**a, "capabilities": [c for c in a.get("capabilities", []) if c != "reply"]} for a in accounts]
                return {"ok": True, "accounts": accounts}
            if name == "userio.ai.propose":
                return self._propose(arguments, principal)
        except PermissionError as error:
            return {"ok": False, "error": str(error)}
        except AdapterNotSupported as error:
            return {"ok": False, "error": str(error)}
        except RuntimeError as error:
            return {"ok": False, "error": str(error)}
        except (KeyError, TypeError, ValueError) as error:
            payload = {"ok": False, "error": str(error).strip("'") or "invalid_arguments"}
            code = getattr(error, "code", "")
            if code:
                # A stable machine-readable reason. A consumer must branch on
                # the code, never on the localized message text.
                payload["code"] = str(code)
                payload["retry_allowed"] = False
            return payload
        return {"ok": False, "error": "unknown_tool"}

    @staticmethod
    def _snapshot(arguments: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """The exact draft the consumer reviewed before approving it.

        The whole snapshot is mandatory. A partial snapshot cannot prove the
        draft did not change since it was reviewed, and an absent snapshot
        would approve whatever the draft says at that moment.
        """
        keys = ("expected_text", "expected_chat_id", "expected_attachments")
        if (not all(key in arguments for key in keys)
                or not isinstance(arguments["expected_text"], str)
                or not isinstance(arguments["expected_chat_id"], str)
                or not isinstance(arguments["expected_attachments"], list)
                or not all(isinstance(item, str) for item in arguments["expected_attachments"])):
            return None, "expected_text, expected_chat_id and expected_attachments are all required"
        return {key: arguments[key] for key in keys}, ""

    def _reconcile_delivery(self, arguments: dict[str, Any], principal: UserPrincipal) -> dict[str, Any]:
        if arguments.get("confirm") is not True:
            return {"ok": False, "error": "exact_confirmation_required"}
        # Ownership first, exactly as on the approve path.
        self._store.draft(self._required(arguments, "draft_id"), user_id=principal.user_id)
        snapshot, problem = self._snapshot(arguments)
        if snapshot is None:
            return {"ok": False, "error": "complete_draft_snapshot_required", "detail": problem}
        result = self._service.reconcile_draft_delivery(
            self._required(arguments, "draft_id"), user_id=principal.user_id,
            expected_snapshot=snapshot,
            provider_history=arguments.get("provider_history") is True,
            connector_available_since=float(arguments.get("connector_available_since") or 0.0))
        return {"ok": True, **result}

    def _approve(self, arguments: dict[str, Any], principal: UserPrincipal) -> dict[str, Any]:
        if arguments.get("confirm") is not True:
            return {"ok": False, "error": "exact_confirmation_required"}
        # Ownership first: a principal that does not own the draft must not
        # learn anything about it, not even which arguments are missing.
        self._store.draft(self._required(arguments, "draft_id"), user_id=principal.user_id)
        snapshot, problem = self._snapshot(arguments)
        if snapshot is None:
            return {"ok": False, "error": "complete_draft_snapshot_required", "detail": problem}
        draft = self._service.approve(
            self._required(arguments, "draft_id"), user_id=principal.user_id,
            expected_snapshot=snapshot,
        )
        conversation = self._store.conversation(draft.conversation_id, user_id=principal.user_id) or {}
        delivery = delivery_fields(draft.receipt or "")
        return {
            "ok": True, "draft": self._draft(draft),
            **delivery,
            # The draft keeps its receipt, so an approval is never retried
            # blindly: the same draft replays its own outcome.
            "retry_allowed": False,
            "receipt": draft.receipt or None,
            "channel": str(conversation.get("source") or ""),
            "delivery_note": self._delivery_note(conversation, delivery),
            "replay": bool(getattr(draft, "replayed", False)),
        }

    @staticmethod
    def _delivery_note(conversation: dict[str, Any], delivery: dict[str, Any] | None = None) -> str:
        delivery = delivery or {}
        if delivery.get("delivery_status") == "smtp_accepted":
            note = ("The mail transport accepted the message; UserIO has no provider "
                    "delivery confirmation, so this is not a confirmed delivery.")
            copy = delivery.get("sent_copy") or {}
            if str(copy.get("saved") or "") == "true":
                note += " A copy was saved to the Sent mailbox."
            else:
                note += " No Sent copy was saved: " + str(copy.get("reason") or "unknown") + "."
            return note
        source = str(conversation.get("source") or "")
        if source == "sms":
            return "Android gateway accepted the SMS; carrier delivery is not guaranteed."
        if source == "max":
            return "Sent through the MAX (oneme.ru) session; server accepted the message."
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
            # ``approved`` means the transport took this message; it never
            # means "waiting to be sent".
            "sent": draft.status == "approved",
        }
        receipt = getattr(draft, "receipt", "")
        if receipt:
            payload["receipt"] = receipt
        return payload
