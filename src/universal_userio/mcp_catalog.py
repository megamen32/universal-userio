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
    "enum": ["mail", "telegram", "whatsapp", "matrix", "vk", "sms", "chatgpt", "max", "yandex"],
}
IGNORED_CHATS_SCHEMA = {
    "type": "array",
    "items": {"type": "string"},
    "maxItems": 100,
}


TOOL_SPECS = (
    ToolSpec(
        "userio.channels.list",
        "List this user's chats across connected channels, or search their retained history when a search query is given. "
        "Read chats from the MCP CallToolResult `structuredContent.chats` array (or `chats` if your client unwraps it); "
        "do not assume `chats` is a top-level property on the MCP wrapper.",
        schema(
            {
                "channel": CHANNEL_SCHEMA,
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "ignored_chats": IGNORED_CHATS_SCHEMA,
                "search": {"type": "string", "minLength": 2, "maxLength": 256},
                "scope": {"type": "string", "enum": ["conversation", "message"]},
                "mode": {"type": "string", "enum": ["text", "semantic", "hybrid"]},
                "match_type": {"type": "string", "enum": ["all", "exact", "fuzzy"]},
                "offset": {"type": "integer", "minimum": 0},
                "conversation_ids": {
                    "type": "array", "items": {"type": "string", "maxLength": 128}, "maxItems": 100,
                },
                "account_ref": {"type": "string", "maxLength": 128},
            }
        ),
    ),
    ToolSpec(
        "userio.channels.search",
        "Search this user's chats and retained message history by name, text, conversation id or message id. "
        "The same core as the web search: scope=conversation returns one row per chat, scope=message returns one "
        "row per matching message with a deep link. Read the rows from `structuredContent.results` and pass a found "
        "conversation_id to userio.channels.read.",
        schema(
            {
                "query": {"type": "string", "minLength": 2, "maxLength": 256},
                "conversation_id": {"type": "string", "maxLength": 128},
                "message_id": {"type": "string", "maxLength": 256},
                "channel": CHANNEL_SCHEMA,
                "scope": {"type": "string", "enum": ["conversation", "message"]},
                "mode": {"type": "string", "enum": ["text", "semantic", "hybrid"]},
                "match_type": {"type": "string", "enum": ["all", "exact", "fuzzy"]},
                "account_ref": {"type": "string", "maxLength": 128},
                "conversation_ids": {
                    "type": "array", "items": {"type": "string", "maxLength": 128}, "maxItems": 100,
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "offset": {"type": "integer", "minimum": 0},
                "ignored_chats": IGNORED_CHATS_SCHEMA,
            }
        ),
    ),
    ToolSpec(
        "userio.channels.export",
        "Export one user-owned chat as a Markdown file: every message with its author, direction and time, "
        "voice messages marked as transcribed speech with the stored transcript, replies marked, and media "
        "only when include_images is true. The result carries the file path, the authenticated download path "
        "and a bounded preview, so the chat is read from the file instead of paged into the conversation.",
        schema(
            {
                "conversation_id": {"type": "string", "maxLength": 128},
                "include_images": {"type": "boolean", "default": False},
                "since": {"type": "number"},
                "until": {"type": "number"},
                "max_messages": {"type": "integer", "minimum": 1, "maximum": 50000, "default": 20000},
            }
        ),
    ),
    ToolSpec(
        "userio.contacts.resolve",
        "Resolve a contact that is not in the history yet into a peer the account can write to: a Telegram username "
        "or phone number, a phone number for SMS or WhatsApp, a mail address, a VK profile or link, or a Yandex "
        "user id. The answer names the account, the channel and the provider-native peer, so the next call can write "
        "to it. Nothing is sent to the contact here.",
        schema(
            {
                "account_ref": {"type": "string", "maxLength": 128},
                "identifier": {"type": "string", "minLength": 1, "maxLength": 256},
            },
            ["account_ref", "identifier"],
        ),
    ),
    ToolSpec(
        "userio.contacts.start",
        "Start a conversation with a contact that is not in the history yet and send the first message. The "
        "identifier is resolved to a provider-native peer (Telegram username or phone number, phone number for SMS "
        "or WhatsApp, mail address, VK profile, Yandex user id), the canonical chat is opened, and the text is "
        "delivered through the same durable path as every other outbound message. The request_id makes a retry "
        "reuse the same message instead of sending it twice.",
        schema(
            {
                "account_ref": {"type": "string", "maxLength": 128},
                "identifier": {"type": "string", "minLength": 1, "maxLength": 256},
                "text": {"type": "string", "minLength": 1, "maxLength": 65536},
                "request_id": {"type": "string", "minLength": 16, "maxLength": 80},
            },
            ["account_ref", "identifier", "text", "request_id"],
        ),
    ),
    ToolSpec(
        "userio.channels.read",
        "Read one user-owned chat or message. Exact stored task-reply matches may include task_reply_association metadata; unresolved replies are never inferred and this metadata grants no send authority. Image attachments are returned as MCP image blocks by default (up to 4 images / 4 MiB); set include_images=false to opt out. Audio bytes are not sent: the result states whether a transcript reached the model.",
        schema(
            {
                "channel": CHANNEL_SCHEMA,
                "chat_id": {"type": "string"},
                "message_id": {"type": "string"},
                "before_id": {"type": "integer", "minimum": 1, "maximum": 2147483647},
                "history_limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "ignored_chats": IGNORED_CHATS_SCHEMA,
                "include_images": {"type": "boolean", "default": True},
            }
        ),
    ),
    ToolSpec(
        "userio.channels.download",
        "Download a Telegram attachment as base64 or MCP content. Documents use Firecrawl AnyDoc; videos return up to four key frames and a transcript. Set include_images=false to omit image/frame bytes.",
        schema({"file_ref": {"type": "string"},
                "mode":{"type":"string","enum":["base64","mcp_resource"],"default":"base64"},
                "include_images":{"type":"boolean","default":True}}, ["file_ref"]),
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
    ToolSpec("userio.workspace.policy.get", "Read automatic processing defaults and revision.", schema({})),
    ToolSpec("userio.workspace.policy.set_default", "Set the automatic processing default for future arrivals of one kind.", schema({
        "conversation_kind": {"type": "string", "enum": ["direct", "group", "channel", "unknown", "telegram_bot"]},
        "enabled": {"type": "boolean"},
    }, ["conversation_kind", "enabled"])),
    ToolSpec("userio.workspace.policy.telegram_bots.set", "Enable or disable future Telegram bot-authored notifications, including bot dialogs and allowed groups.", schema({
        "enabled": {"type": "boolean"},
    }, ["enabled"])),
    ToolSpec("userio.workspace.policy.chats.list", "Search known chats by account, peer, title, kind, or rule (at most 200 per page).", schema({
        "source": {"type": "string"}, "account_ref": {"type": "string"},
        "peer_id": {"type": "string"},
        "conversation_kind": {"type": "string", "enum": ["direct", "group", "channel", "unknown", "telegram_bot"]},
        "action": {"type": "string", "enum": ["allow", "ignore", "inherit"]},
        "query": {"type": "string", "maxLength": 128},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        "offset": {"type": "integer", "minimum": 0, "maximum": 100000},
    })),
    ToolSpec("userio.workspace.policy.chats.set", "Set a known chat to allow, ignore, or inherit.", schema({
        "conversation_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "action": {"type": "string", "enum": ["allow", "ignore", "inherit"]},
        "reason": {"type": "string", "maxLength": 500},
    }, ["conversation_id", "action"])),
    ToolSpec("userio.workspace.policy.evaluate", "Explain automatic processing for one known chat.", schema({
        "conversation_id": {"type": "string", "minLength": 1, "maxLength": 128},
    }, ["conversation_id"])),
    ToolSpec("userio.workspace.triage.get", "Read this user's importance-triage thresholds.", schema({})),
    ToolSpec("userio.workspace.triage.set", "Set user-scoped importance-triage thresholds.", schema({
        "enabled": {"type": "boolean"},
        "threshold": {"type": "number", "minimum": 0, "maximum": 1},
        "min_confidence": {"type": "number", "minimum": 0, "maximum": 1},
    })),
    ToolSpec("userio.workspace.triage.feedback", "Record importance feedback for one completed triage.", schema({
        "event_seq": {"type": "integer", "minimum": 1},
        "request_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "label": {"type": "string", "enum": ["important", "not_important", "ignore_chat"]},
    }, ["event_seq", "request_id", "label"])),
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
        "Compatibility alias: list this user's unread messages. Exact stored task-reply matches may include task_reply_association metadata; unresolved replies are never inferred and this metadata grants no send authority.",
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
        "Compatibility alias: read a conversation. Exact stored task-reply matches may include task_reply_association metadata; unresolved replies are never inferred and this metadata grants no send authority.",
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
            ["draft_id", "confirm", "expected_text", "expected_chat_id", "expected_attachments"],
        ),
    ),
    ToolSpec(
        "userio.draft.reconcile_delivery",
        "Resolve one in-flight Telegram delivery against the provider record.",
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
                "provider_history": {
                    "type": "boolean",
                    "description": "Read the provider's own history when the connector journal "
                                   "cannot speak about an older attempt.",
                },
                "connector_available_since": {
                    "type": "number",
                    "description": "Unix time from which the Telegram connector was able to "
                                   "serve again. Required for the provider-history resolution.",
                },
            },
            ["draft_id", "confirm", "expected_text", "expected_chat_id", "expected_attachments"],
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


MESSENGER_SPECS = (
    ToolSpec('userio.messenger.upload',
        'Stage a private file for one source-bound approved send. Telegram uses the selected account upload limit; other providers use their published limit. Bytes expire after 900s. No send, URLs or local paths. Returns upload_id and metadata only.',
        schema({'account_ref':{'type':'string'},'conversation_id':{'type':'string'},'source_message_id':{'type':'string'},
                'filename':{'type':'string','maxLength':128},'mime_type':{'type':'string','maxLength':128},
                'data_base64':{'type':'string'},'sha256':{'type':'string','pattern':'^[0-9a-f]{64}$'},
                'request_id':{'type':'string','minLength':16,'maxLength':128}},
               ['account_ref','conversation_id','source_message_id','filename','mime_type','data_base64','sha256','request_id'])),
    ToolSpec('userio.messenger.events',
        'Poll account-scoped live incoming sources without read ACK. Omit after to initialize at current head without historical dispatch. Persist next_cursor; history/recovery is excluded.',
        schema({'account_ref':{'type':'string'},'after':{'type':'integer','minimum':0},
                'limit':{'type':'integer','minimum':1,'maximum':100}},['account_ref'])),
    ToolSpec('userio.messenger.media',
        'Read bounded actual media/ASR through the account provider. Return bytes/transcript or explicit unavailable, never infer from a sticker label.',
        schema({'account_ref':{'type':'string'},'conversation_id':{'type':'string'},'message_id':{'type':'string'},
                'attachment_index':{'type':'integer','minimum':0,'maximum':7}},['account_ref','conversation_id','message_id'])),
    ToolSpec('userio.messenger.resolve',
        'Resolve one exact actual provider source into the owned UserIO account. No fake conversations, sends or read ACK.',
        schema({'account_ref':{'type':'string'},'peer_id':{'type':'string'},'provider_message_id':{'type':'string'}},
               ['account_ref','peer_id','provider_message_id'])),
    ToolSpec('userio.messenger.capabilities',
        'Discover effective account/provider/credential operations; unsupported actions are explicit.',
        schema({'account_ref':{'type':'string'}}, ['account_ref'])),
    ToolSpec('userio.messenger.history',
        'Read a bounded stored page without provider refresh or read ACK; preserve author/source/thread and opaque provider IDs.',
        schema({'account_ref':{'type':'string'},'conversation_id':{'type':'string'},
                'limit':{'type':'integer','minimum':1,'maximum':100}, 'cursor':{'type':'string','maxLength':1024},
                'refresh':{'type':'boolean','default':False}},
               ['account_ref','conversation_id'])),
    ToolSpec('userio.messenger.propose',
        'Propose a source-bound reply/edit/reaction/media/typing operation. Does not execute. Keep request_id for dedup.',
        schema({'account_ref':{'type':'string'},'conversation_id':{'type':'string'},'source_message_id':{'type':'string'},
                'action':{'type':'string','enum':['reply','edit','react','sticker','file','typing']},
                'payload':{'type':'object'},'request_id':{'type':'string','minLength':16,'maxLength':128}},
               ['account_ref','conversation_id','source_message_id','action','payload','request_id'])),
    ToolSpec('userio.messenger.telegram_admin.propose',
        'Prepare one Telegram group or private broadcast-channel action for explicit approval. Never executes the Telegram change. Channel deletion requires the exact phrase DELETE CHANNEL <peer_id>; supergroup removal is a ban.',
        schema({'account_ref':{'type':'string'},'operation':{'type':'string','enum':['group_create','group_invite','group_remove','channel_create','channel_delete']},
                'payload':{'type':'object'},'request_id':{'type':'string','minLength':16,'maxLength':128}},
               ['account_ref','operation','payload','request_id'])),
    ToolSpec('userio.messenger.get',
        'Read the durable operation and receipt. Sending means UNKNOWN: never replay with a new request ID.',
        schema({'action_id':{'type':'string'}}, ['action_id'])),
    ToolSpec('userio.messenger.approve',
        'Execute only this explicitly approved immutable operation. Bind approval to its exact hash; no automatic model approval.',
        schema({'action_id':{'type':'string'}, 'expected_hash':{'type':'string'}, 'confirm':{'type':'boolean'}},
               ['action_id','expected_hash','confirm'])),
)
TOOL_SPECS = (*TOOL_SPECS, *MESSENGER_SPECS)

MATRIX_CALL_SPECS = (
    ToolSpec('userio.calls.matrix.start',
        'Start one explicitly authorized AI voice call in a joined two-person Matrix room. '
        'Keep request_id: accepted or unknown calls must never be replayed. Returns a durable call receipt.',
        schema({'account_ref':{'type':'string'},'room_id':{'type':'string'},'target':{'type':'string'},
                'purpose':{'type':'string','maxLength':12000},'greeting':{'type':'string','maxLength':1000},
                'request_id':{'type':'string','minLength':16,'maxLength':128},
                'max_seconds':{'type':'integer','minimum':30,'maximum':600},'confirm':{'type':'boolean'}},
               ['account_ref','room_id','target','purpose','request_id','confirm'])),
    ToolSpec('userio.calls.matrix.get','Read an owned call receipt without calling again.',
        schema({'call_id':{'type':'string'}},['call_id'])),
    ToolSpec('userio.calls.matrix.stop','End this owned Matrix call; never dial another call.',
        schema({'call_id':{'type':'string'}},['call_id'])),
)
TOOL_SPECS = (*TOOL_SPECS, *MATRIX_CALL_SPECS)

TELEGRAM_CALL_SPECS = (
    ToolSpec('userio.calls.telegram.start',
        'Start one explicitly authorized AI Secretary voice call through its existing Telegram client. '
        'Keep request_id: admitted or unknown calls must never be replayed.',
        schema({'account_ref':{'type':'string'},'target':{'type':'string'},
                'purpose':{'type':'string','maxLength':500},'greeting':{'type':'string','maxLength':1000},
                'request_id':{'type':'string','minLength':16,'maxLength':128},
                'max_seconds':{'type':'integer','minimum':30,'maximum':600},'confirm':{'type':'boolean'}},
               ['account_ref','target','purpose','request_id','confirm'])),
    ToolSpec('userio.calls.telegram.get','Read this owned Telegram call receipt without dialing again.',
        schema({'call_id':{'type':'string'}},['call_id'])),
    ToolSpec('userio.calls.telegram.stop','End this owned Telegram call; never dial another call.',
        schema({'call_id':{'type':'string'}},['call_id'])),
)
TOOL_SPECS = (*TOOL_SPECS, *TELEGRAM_CALL_SPECS)

TELEMOST_SPECS = (
    ToolSpec('userio.meetings.telemost.start',
        'Join one explicitly requested Telemost meeting as a visible, muted recorder. Never replay UNKNOWN joins.',
        schema({'url':{'type':'string','maxLength':2048},'request_id':{'type':'string','minLength':16,'maxLength':128},
                'confirm':{'type':'boolean'},'max_seconds':{'type':'integer','minimum':30,'maximum':7200}},
               ['url','request_id','confirm'])),
    ToolSpec('userio.meetings.telemost.get','Read recording, audio links and transcription for this owned meeting.',
        schema({'meeting_id':{'type':'string'}},['meeting_id'])),
    ToolSpec('userio.meetings.telemost.stop','Stop this owned recording without joining another meeting.',
        schema({'meeting_id':{'type':'string'}},['meeting_id'])),
)
TOOL_SPECS = (*TOOL_SPECS, *TELEMOST_SPECS)

DIAGNOSTIC_SPECS = (
    ToolSpec('userio.diagnostic.create',
        'Create an owned virtual test chat in UserIO. actor_ref is a test label, not identity or permission. No external provider.',
        schema({'request_id':{'type':'string','minLength':16,'maxLength':128},
                'title':{'type':'string','maxLength':160},'actor_ref':{'type':'string','maxLength':160}}, ['request_id'])),
    ToolSpec('userio.diagnostic.get', 'Read this owned UserIO diagnostic conversation and its actual persisted test messages.',
        schema({'conversation_id':{'type':'string'}}, ['conversation_id'])),
    ToolSpec('userio.diagnostic.message', 'Write ONE virtual test input or answer in an owned diagnostic chat. Durable request dedup; never invokes any real messaging provider.',
        schema({'conversation_id':{'type':'string'},'request_id':{'type':'string','minLength':16,'maxLength':128},
                'body':{'type':'string','maxLength':16384},'direction':{'type':'string','enum':['incoming','outgoing']}},
               ['conversation_id','request_id','body'])),
)
TOOL_SPECS = (*TOOL_SPECS, *DIAGNOSTIC_SPECS)

TOOL_CAPABILITIES = {
    'userio.diagnostic.create':'send','userio.diagnostic.get':'read','userio.diagnostic.message':'send',
    'userio.meetings.telemost.start':'send','userio.meetings.telemost.get':'read','userio.meetings.telemost.stop':'send',
    'userio.calls.telegram.start':'send', 'userio.calls.telegram.get':'read', 'userio.calls.telegram.stop':'send',
    'userio.calls.matrix.start':'send', 'userio.calls.matrix.get':'read', 'userio.calls.matrix.stop':'send',
    'userio.messenger.upload': 'send',
    'userio.messenger.events': 'read',
    'userio.messenger.capabilities': 'read',
    'userio.messenger.history': 'read',
    'userio.messenger.resolve': 'read',
    'userio.messenger.media': 'download',
    'userio.messenger.get': 'read',
    'userio.messenger.propose': 'send',
    'userio.messenger.telegram_admin.propose': 'send',
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
    "userio.draft.create": "send",
    "userio.draft.update": "send",
    "userio.draft.delete": "send",
    "userio.draft.approve_send": "send",
    "userio.draft.reconcile_delivery": "send",
    "userio.conversation.delete_local": "read",
    "userio.accounts.list": "read",
    "userio.ai.propose": "send",
}
