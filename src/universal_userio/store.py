"""Durable, user-scoped authentication and UserIO state."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from .contracts import ConversationPolicy, InboxMessage, ReplyDraft, UserPrincipal


_ITERATIONS = 310_000
DRAFT_RETENTION_SECONDS = 3 * 24 * 60 * 60
_USERNAME = re.compile(r"[A-Za-z0-9_.@+-]{3,64}")
_DATA_TABLES = ("conversations", "messages", "drafts", "identities", "reply_rules", "provider_accounts")
CONTEXT_MESSAGE_COUNT_DEFAULT = 3
CONTEXT_MESSAGE_COUNT_MAX = 20
CONTEXT_TOKEN_BUDGET_DEFAULT = 1000
CONTEXT_TOKEN_BUDGET_MAX = 8_000
CONTEXT_MAX_MESSAGE_COUNT_DEFAULT = 150
CONTEXT_MAX_MESSAGE_COUNT_MAX = 1_000
CONTEXT_MAX_TOKEN_BUDGET_DEFAULT = 48_000
CONTEXT_MAX_TOKEN_BUDGET_MAX = 100_000
SUMMARY_TOKEN_BUDGET_DEFAULT = 1_200
SUMMARY_TOKEN_BUDGET_MAX = 4_096
SUMMARY_RETENTION_DAYS_DEFAULT = 90
SUMMARY_RETENTION_DAYS_MAX = 3_650
SUMMARY_CACHE_CHANNEL_DEFAULTS = {
    "telegram": True,
    "gmail": False,
    "whatsapp": False,
    "max": False,
    "sms": False,
    "chatgpt": False,
    "vk": False,
}
SUMMARY_CACHE_KIND_DEFAULTS = {
    "direct": True,
    "group": False,
    "channel": False,
    "unknown": False,
}


def _estimated_tokens(value: str) -> int:
    """Conservative model-neutral estimate for mixed Cyrillic/ASCII text."""
    ascii_chars = sum(1 for char in value if ord(char) < 128)
    return (ascii_chars + 2) // 3 + (len(value) - ascii_chars)


def _prefix_for_tokens(value: object, token_limit: int) -> str:
    text = str(value or "").strip()
    if token_limit <= 0 or not text:
        return ""
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if _estimated_tokens(text[:middle]) <= token_limit:
            low = middle
        else:
            high = middle - 1
    return text[:low]


def _cache_channel(source: object) -> str:
    value = str(source or "").strip().lower()
    if value in {"mail", "email", "gmail"} or value.startswith(("gmail:", "email:")):
        return "gmail"
    if value.startswith("matrix:"):
        return "matrix"
    if value == "chatgpt" or value.startswith("chatgpt:"):
        return "chatgpt"
    return value


class SQLiteUserIOStore:
    def __init__(self, path: str | Path) -> None:
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        from .search import normalize
        self._connection.create_function("userio_search_text", 1, normalize, deterministic=True)
        with self._lock, self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._auth_schema()
            self._bootstrap_owner()
            if self._is_legacy() or self._table_exists("legacy_conversations"):
                self._migrate_legacy()
            self._data_schema()
            self._contact_names_migration()
            self._conversation_secretary_jobs_migration()
            self._workspace_policy_migration()
            message_columns = {
                str(row["name"])
                for row in self._connection.execute("PRAGMA table_info(messages)")
            }
            if "direction" not in message_columns:
                self._connection.execute(
                    "ALTER TABLE messages ADD COLUMN direction TEXT NOT NULL DEFAULT 'incoming'"
                )
            if "sender_is_bot" not in message_columns:
                self._connection.execute(
                    "ALTER TABLE messages ADD COLUMN sender_is_bot INTEGER NOT NULL DEFAULT 0"
                )
            for name, definition in (("provider_read", "INTEGER"), ("deleted_at", "REAL"), ("outbox_receipt", "TEXT NOT NULL DEFAULT ''")):
                if name not in message_columns:
                    self._connection.execute(f"ALTER TABLE messages ADD COLUMN {name} {definition}")
            self._connection.execute("""CREATE TABLE IF NOT EXISTS telegram_read_state (
                user_id TEXT NOT NULL, account_ref TEXT NOT NULL, peer_id TEXT NOT NULL,
                inbox_max INTEGER NOT NULL DEFAULT 0, outbox_max INTEGER NOT NULL DEFAULT 0,
                unread_count INTEGER, unread_observed_at REAL,
                PRIMARY KEY(user_id,account_ref,peer_id))""")
            read_state_cols = {str(row["name"]) for row in self._connection.execute(
                "PRAGMA table_info(telegram_read_state)")}
            if "unread_count" not in read_state_cols:
                self._connection.execute(
                    "ALTER TABLE telegram_read_state ADD COLUMN unread_count INTEGER")
            if "unread_observed_at" not in read_state_cols:
                self._connection.execute(
                    "ALTER TABLE telegram_read_state ADD COLUMN unread_observed_at REAL")
            self._connection.execute("""CREATE TABLE IF NOT EXISTS telegram_history_state (
                user_id TEXT NOT NULL, account_ref TEXT NOT NULL, peer_id TEXT NOT NULL,
                oldest_id INTEGER NOT NULL DEFAULT 0, complete INTEGER NOT NULL DEFAULT 0,
                fetched_count INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
                PRIMARY KEY(user_id,account_ref,peer_id))""")
            self._backfill_workspace_events()
            columns = {r["name"] for r in self._connection.execute("PRAGMA table_info(workspace_events)")}
            if "queued_at" not in columns:
                self._connection.execute("ALTER TABLE workspace_events ADD COLUMN queued_at REAL NOT NULL DEFAULT 0")
                self._connection.execute("UPDATE workspace_events SET queued_at=?", (time.time(),))
            self._connection.execute("""CREATE TABLE IF NOT EXISTS workspace_batches (
                user_id TEXT NOT NULL,leader_seq INTEGER NOT NULL,event_seq INTEGER NOT NULL,
                PRIMARY KEY(user_id,event_seq))""")
            self._connection.execute("CREATE INDEX IF NOT EXISTS workspace_batches_leader_idx ON workspace_batches(user_id,leader_seq,event_seq)")
            self.maintain_conversation_summaries()
            from .search import initialize_search
            initialize_search(self)
            # Derived semantic state is always created empty; it is only filled
            # after the owner opts in, so an unconfigured deployment costs
            # three empty tables and no vectors.
            from .semantic_index import attach_triggers, initialize as initialize_semantic_index
            initialize_semantic_index(self)
            attach_triggers(self)

    @property
    def default_user_id(self) -> str:
        return self.owner().user_id

    def _auth_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                password_salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,
                password_iterations INTEGER NOT NULL,
                role TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                auth_kind TEXT NOT NULL DEFAULT 'password'
            );
            CREATE TABLE IF NOT EXISTS api_tokens (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                token_hash BLOB UNIQUE NOT NULL,
                created_at REAL NOT NULL,
                revoked_at REAL,
                kind TEXT NOT NULL DEFAULT 'personal',
                token_type TEXT NOT NULL DEFAULT 'access',
                expires_at REAL,
                oauth_client_id TEXT,
                scope TEXT
            );
            CREATE INDEX IF NOT EXISTS api_tokens_user_idx ON api_tokens(user_id,revoked_at);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS oauth_clients (
                id TEXT PRIMARY KEY,
                secret_hash BLOB,
                redirect_uris TEXT NOT NULL,
                token_endpoint_auth_method TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oauth_authorization_codes (
                code_hash BLOB PRIMARY KEY,
                user_id TEXT NOT NULL,
                client_id TEXT NOT NULL,
                redirect_uri TEXT NOT NULL,
                code_challenge TEXT,
                scope TEXT NOT NULL,
                expires_at REAL NOT NULL,
                used_at REAL
            );
            CREATE TABLE IF NOT EXISTS oauth_sessions (
                session_hash BLOB PRIMARY KEY,
                user_id TEXT NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS login_challenges (
                id TEXT PRIMARY KEY,
                channel TEXT NOT NULL,
                destination TEXT NOT NULL,
                code_hash BLOB NOT NULL,
                salt BLOB NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                consumed_at REAL,
                attempts INTEGER NOT NULL DEFAULT 0,
                request_ip TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS login_challenges_destination_idx
                ON login_challenges(channel,destination,created_at);
            CREATE INDEX IF NOT EXISTS login_challenges_ip_idx
                ON login_challenges(request_ip,created_at);
            CREATE TABLE IF NOT EXISTS login_identities (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                identifier TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                verified_at REAL NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(channel,identifier)
            );
            CREATE INDEX IF NOT EXISTS login_identities_user_idx ON login_identities(user_id);
            """
        )
        # Users registered with a one-time code never own a usable password,
        # so their row records the authentication kind explicitly.
        user_columns = {
            str(row["name"]) for row in self._connection.execute("PRAGMA table_info(users)")
        }
        if "auth_kind" not in user_columns:
            self._connection.execute(
                "ALTER TABLE users ADD COLUMN auth_kind TEXT NOT NULL DEFAULT 'password'"
            )
        # Existing v1 databases have the original, smaller api_tokens table.
        columns = {
            str(row["name"]) for row in self._connection.execute("PRAGMA table_info(api_tokens)")
        }
        for name, definition in (
            ("kind", "TEXT NOT NULL DEFAULT 'personal'"),
            ("token_type", "TEXT NOT NULL DEFAULT 'access'"),
            ("expires_at", "REAL"),
            ("oauth_client_id", "TEXT"),
            ("scope", "TEXT"),
            ("account_refs", "TEXT"),
            ("mandate_id", "TEXT"),
        ):
            if name not in columns:
                self._connection.execute(f"ALTER TABLE api_tokens ADD COLUMN {name} {definition}")
        self._connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS api_tokens_task_mandate_idx "
            "ON api_tokens(mandate_id) WHERE kind='task_mandate' AND mandate_id IS NOT NULL"
        )

    def _contact_names_migration(self) -> None:
        columns = {str(row["name"]) for row in self._connection.execute("PRAGMA table_info(contact_names)")}
        if "is_explicit" in columns:
            return
        # Older rows do not record whether the label came from Telegram sync or
        # an explicit alias. Treat them as synced so a fresh Telegram profile
        # can replace stale labels; new explicit aliases carry is_explicit=1.
        self._connection.execute(
            "ALTER TABLE contact_names ADD COLUMN is_explicit INTEGER NOT NULL DEFAULT 0"
        )

    def _bootstrap_owner(self) -> None:
        row = self._connection.execute(
            "SELECT id FROM users WHERE role='owner' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            salt, digest = self._digest(secrets.token_urlsafe(32))
            owner_id, now = "user_owner", time.time()
            self._connection.execute(
                """
                INSERT INTO users
                (id,username,password_salt,password_hash,password_iterations,role,created_at,updated_at,auth_kind)
                VALUES (?,?,?,?,?,?,?,?,'password')
                """,
                (owner_id, "owner", salt, digest, _ITERATIONS, "owner", now, now),
            )
        else:
            owner_id = str(row["id"])
        self._connection.execute(
            "INSERT OR IGNORE INTO settings VALUES ('service_owner_user_id',?)", (owner_id,)
        )

    def _is_legacy(self) -> bool:
        exists = self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='conversations'"
        ).fetchone()
        if not exists:
            return False
        columns = {str(row["name"]) for row in self._connection.execute("PRAGMA table_info(conversations)")}
        return "user_id" not in columns

    def _migrate_legacy(self) -> None:
        for table in _DATA_TABLES:
            if self._table_exists(table) and not self._table_exists(f"legacy_{table}"):
                self._connection.execute(f"ALTER TABLE {table} RENAME TO legacy_{table}")
        self._data_schema()
        fields = {
            "conversations": (
                "id,conversation_key,route_id,source,sender,identity_id,response_mode,updated_at",
                ("id", "conversation_key", "route_id", "source", "sender", "identity_id", "response_mode", "updated_at"),
            ),
            "messages": (
                "source,message_id,conversation_id,sender,body,received_at,seen_at",
                ("source", "message_id", "conversation_id", "sender", "body", "received_at", "seen_at"),
            ),
            "drafts": (
                "id,conversation_id,body,status,created_at,approved_at,outbox_receipt",
                ("id", "conversation_id", "body", "status", "created_at", "approved_at", "outbox_receipt"),
            ),
            "identities": (
                "source,external_id,identity_id,display_name",
                ("source", "external_id", "identity_id", "display_name"),
            ),
            "reply_rules": (
                "identity_id,source,route_id,response_mode",
                ("identity_id", "source", "route_id", "response_mode"),
            ),
            "provider_accounts": (
                "id,provider,display_name,can_read,can_reply,credential_ref,enabled",
                ("id", "provider", "display_name", "can_read", "can_reply", "credential_ref", "enabled"),
            ),
        }
        for table, (targets, candidates) in fields.items():
            if self._table_exists(f"legacy_{table}"):
                available = {
                    str(row["name"])
                    for row in self._connection.execute(f"PRAGMA table_info(legacy_{table})")
                }
                defaults = {"response_mode": "'approve'"}
                expressions = [
                    candidate if candidate in available else defaults.get(candidate, "NULL")
                    for candidate in candidates
                ]
                self._connection.execute(
                    f"""
                    INSERT OR IGNORE INTO {table}(user_id,{targets})
                    SELECT ?,{','.join(expressions)} FROM legacy_{table}
                    """,
                    (self.default_user_id,),
                )
                self._connection.execute(f"DROP TABLE legacy_{table}")

    def _table_exists(self, name: str) -> bool:
        return self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None

    def _data_schema(self) -> None:
        script = """
            CREATE TABLE IF NOT EXISTS conversations (
                user_id TEXT NOT NULL,id TEXT NOT NULL,conversation_key TEXT NOT NULL,
                route_id TEXT NOT NULL,source TEXT NOT NULL,sender TEXT NOT NULL,
                identity_id TEXT,response_mode TEXT NOT NULL DEFAULT 'approve',updated_at REAL NOT NULL,
                PRIMARY KEY(user_id,id),UNIQUE(user_id,conversation_key)
            );
            CREATE TABLE IF NOT EXISTS phone_conversation_aliases (
                user_id TEXT NOT NULL,old_id TEXT NOT NULL,canonical_id TEXT NOT NULL,
                PRIMARY KEY(user_id,old_id)
            );
            CREATE TABLE IF NOT EXISTS messages (
                user_id TEXT NOT NULL,source TEXT NOT NULL,message_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,sender TEXT NOT NULL,body TEXT NOT NULL,
                direction TEXT NOT NULL DEFAULT 'incoming',received_at REAL NOT NULL,seen_at REAL,
                sender_is_bot INTEGER NOT NULL DEFAULT 0,edited_at REAL,
                PRIMARY KEY(user_id,source,message_id)
            );
            CREATE INDEX IF NOT EXISTS messages_conversation_idx
                ON messages(user_id,conversation_id,received_at);
            CREATE TABLE IF NOT EXISTS workspace_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,source TEXT NOT NULL,
                message_id TEXT NOT NULL,conversation_id TEXT NOT NULL,
                eligible INTEGER NOT NULL DEFAULT 0,
                reconciled INTEGER NOT NULL DEFAULT 0,
                UNIQUE(user_id,source,message_id)
            );
            CREATE INDEX IF NOT EXISTS workspace_events_user_seq_idx
                ON workspace_events(user_id,seq);
            CREATE TABLE IF NOT EXISTS workspace_claims (
                user_id TEXT NOT NULL,event_seq INTEGER NOT NULL,
                worker_id TEXT NOT NULL,lease_token_hash TEXT NOT NULL,
                status TEXT NOT NULL,lease_expires_at REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 1,claimed_at REAL NOT NULL,
                updated_at REAL NOT NULL,completed_at REAL,last_error TEXT,
                PRIMARY KEY(user_id,event_seq)
            );
            CREATE INDEX IF NOT EXISTS workspace_claims_user_status_idx
                ON workspace_claims(user_id,status,lease_expires_at,event_seq);
            CREATE TABLE IF NOT EXISTS workspace_claim_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,event_seq INTEGER NOT NULL,
                worker_id TEXT NOT NULL,action TEXT NOT NULL,
                at REAL NOT NULL,detail TEXT
            );
            CREATE INDEX IF NOT EXISTS workspace_claim_log_event_idx
                ON workspace_claim_log(user_id,event_seq,id);
            CREATE TABLE IF NOT EXISTS workspace_triage_settings (
                user_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 1,
                threshold REAL NOT NULL DEFAULT 0.75,
                min_confidence REAL NOT NULL DEFAULT 0.65,
                revision INTEGER NOT NULL DEFAULT 1,updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workspace_triage (
                user_id TEXT NOT NULL,event_seq INTEGER NOT NULL,request_id TEXT NOT NULL,
                status TEXT NOT NULL,result_json TEXT,draft_ids_json TEXT NOT NULL DEFAULT '[]',
                attempts INTEGER NOT NULL DEFAULT 0,last_error TEXT,
                send_state TEXT NOT NULL DEFAULT 'none',selected_draft_id TEXT,
                send_actor TEXT,send_receipt TEXT,deep_actor TEXT,deep_requested_at REAL,
                feedback_label TEXT,feedback_actor TEXT,feedback_at REAL,
                created_at REAL NOT NULL,updated_at REAL NOT NULL,
                PRIMARY KEY(user_id,event_seq),UNIQUE(user_id,request_id)
            );
            CREATE INDEX IF NOT EXISTS workspace_triage_status_idx
                ON workspace_triage(user_id,status,event_seq);
            CREATE TABLE IF NOT EXISTS conversation_secretary_jobs (
                user_id TEXT NOT NULL,job_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,event_seq INTEGER NOT NULL,
                triage_request_id TEXT NOT NULL,actor TEXT NOT NULL,
                source TEXT NOT NULL,account_ref TEXT NOT NULL,
                peer_id TEXT NOT NULL,message_id TEXT NOT NULL,
                parent_job_id TEXT,attempt INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'accepted',worker_id TEXT,
                session_id TEXT,session_url TEXT,result_text TEXT,last_error TEXT,
                created_at REAL NOT NULL,updated_at REAL NOT NULL,
                PRIMARY KEY(user_id,job_id),
                UNIQUE(user_id,parent_job_id),
                UNIQUE(user_id,conversation_id,event_seq,attempt)
            );
            CREATE INDEX IF NOT EXISTS conversation_secretary_jobs_status_idx
                ON conversation_secretary_jobs(user_id,status,created_at);
            CREATE TABLE IF NOT EXISTS workspace_exclusions (
                user_id TEXT NOT NULL,conversation_id TEXT NOT NULL,
                source TEXT NOT NULL,reason TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,updated_at REAL NOT NULL,
                PRIMARY KEY(user_id,conversation_id)
            );
            CREATE INDEX IF NOT EXISTS workspace_exclusions_user_source_idx
                ON workspace_exclusions(user_id,source,conversation_id);
            CREATE TABLE IF NOT EXISTS workspace_policy_defaults (
                user_id TEXT NOT NULL,conversation_kind TEXT NOT NULL,
                enabled INTEGER NOT NULL,updated_at REAL NOT NULL,
                PRIMARY KEY(user_id,conversation_kind)
            );
            CREATE TABLE IF NOT EXISTS workspace_chat_rules (
                user_id TEXT NOT NULL,conversation_id TEXT NOT NULL,
                action TEXT NOT NULL,reason TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,updated_at REAL NOT NULL,
                PRIMARY KEY(user_id,conversation_id)
            );
            CREATE TABLE IF NOT EXISTS contact_names (
                user_id TEXT NOT NULL,source TEXT NOT NULL,sender TEXT NOT NULL,
                name TEXT NOT NULL,updated_at REAL NOT NULL,is_explicit INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(user_id,source,sender)
            );
            CREATE TABLE IF NOT EXISTS drafts (
                user_id TEXT NOT NULL,id TEXT NOT NULL,conversation_id TEXT NOT NULL,
                body TEXT NOT NULL,status TEXT NOT NULL,created_at REAL NOT NULL,
                approved_at REAL,outbox_receipt TEXT,browser_notified_at REAL,
                sending_at REAL,delivery_state TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(user_id,id)
            );
            CREATE TABLE IF NOT EXISTS task_mandates (
                user_id TEXT NOT NULL,id TEXT NOT NULL,issuer_user_id TEXT NOT NULL,
                task_id TEXT NOT NULL,actor_id TEXT NOT NULL,account_ref TEXT NOT NULL,
                channel TEXT NOT NULL,recipient_ids_json TEXT NOT NULL,purpose TEXT NOT NULL,
                max_sends INTEGER NOT NULL,version INTEGER NOT NULL,
                policy_hash TEXT NOT NULL,created_at REAL NOT NULL,expires_at REAL NOT NULL,
                revoked_at REAL,PRIMARY KEY(user_id,id)
            );
            CREATE INDEX IF NOT EXISTS task_mandates_active_idx
                ON task_mandates(user_id,revoked_at,expires_at);
            CREATE TABLE IF NOT EXISTS task_mandate_operations (
                user_id TEXT NOT NULL,mandate_id TEXT NOT NULL,operation_id TEXT NOT NULL,
                mandate_version INTEGER NOT NULL,operation_hash TEXT NOT NULL,
                operation_json TEXT NOT NULL,status TEXT NOT NULL,created_at REAL NOT NULL,
                PRIMARY KEY(user_id,mandate_id,operation_id),
                FOREIGN KEY(user_id,mandate_id) REFERENCES task_mandates(user_id,id)
            );
            CREATE INDEX IF NOT EXISTS task_mandate_operations_count_idx
                ON task_mandate_operations(user_id,mandate_id);
            CREATE TABLE IF NOT EXISTS task_contact_identity_bindings (
                user_id TEXT NOT NULL,id TEXT NOT NULL,task_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,project_id TEXT NOT NULL,
                catalog_namespace TEXT NOT NULL,catalog_record_id TEXT NOT NULL,
                catalog_contact_id TEXT NOT NULL,provider TEXT NOT NULL,
                account_ref TEXT NOT NULL,peer_id TEXT NOT NULL,peer_kind TEXT NOT NULL,
                endpoint_id TEXT NOT NULL,conversation_id TEXT NOT NULL,
                identity_hash TEXT NOT NULL,created_at REAL NOT NULL,
                PRIMARY KEY(user_id,id),
                UNIQUE(user_id,task_id,actor_id,catalog_namespace,catalog_record_id,
                       catalog_contact_id,account_ref,provider)
            );
            CREATE TABLE IF NOT EXISTS task_contact_qualifications (
                user_id TEXT NOT NULL,id TEXT NOT NULL,task_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,project_id TEXT NOT NULL,
                catalog_namespace TEXT NOT NULL,catalog_record_id TEXT NOT NULL,
                catalog_contact_id TEXT NOT NULL,catalog_revision TEXT NOT NULL,
                contact_version TEXT NOT NULL,provider TEXT NOT NULL,
                account_ref TEXT NOT NULL,peer_id TEXT NOT NULL,peer_kind TEXT NOT NULL,
                endpoint_id TEXT NOT NULL,conversation_id TEXT NOT NULL,
                identity_binding_id TEXT NOT NULL,
                binding_hash TEXT NOT NULL,
                expires_at REAL NOT NULL,created_at REAL NOT NULL,
                PRIMARY KEY(user_id,id),
                UNIQUE(user_id,task_id,actor_id,catalog_namespace,catalog_record_id,
                       catalog_contact_id,catalog_revision,account_ref,provider,peer_id),
                FOREIGN KEY(user_id,identity_binding_id)
                    REFERENCES task_contact_identity_bindings(user_id,id)
            );
            CREATE INDEX IF NOT EXISTS task_contact_qualification_identity_idx
                ON task_contact_qualifications(user_id,account_ref,provider,peer_id);
            CREATE TABLE IF NOT EXISTS task_mandate_qualifications (
                user_id TEXT NOT NULL,mandate_id TEXT NOT NULL,
                qualification_id TEXT NOT NULL,task_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,account_ref TEXT NOT NULL,provider TEXT NOT NULL,
                peer_id TEXT NOT NULL,binding_hash TEXT NOT NULL,
                PRIMARY KEY(user_id,mandate_id,qualification_id),
                UNIQUE(user_id,mandate_id,account_ref,provider,peer_id),
                FOREIGN KEY(user_id,mandate_id) REFERENCES task_mandates(user_id,id),
                FOREIGN KEY(user_id,qualification_id)
                    REFERENCES task_contact_qualifications(user_id,id)
            );
            CREATE TABLE IF NOT EXISTS task_mandate_draft_claims (
                user_id TEXT NOT NULL,draft_id TEXT NOT NULL,mandate_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,qualification_id TEXT NOT NULL,
                operation_hash TEXT NOT NULL,binding_hash TEXT NOT NULL,created_at REAL NOT NULL,
                PRIMARY KEY(user_id,draft_id),
                UNIQUE(user_id,mandate_id,operation_id),
                FOREIGN KEY(user_id,mandate_id,operation_id)
                    REFERENCES task_mandate_operations(user_id,mandate_id,operation_id),
                FOREIGN KEY(user_id,qualification_id)
                    REFERENCES task_contact_qualifications(user_id,id)
            );
            CREATE INDEX IF NOT EXISTS task_mandate_draft_claim_operation_idx
                ON task_mandate_draft_claims(user_id,mandate_id,operation_id);
            CREATE INDEX IF NOT EXISTS drafts_conversation_idx
                ON drafts(user_id,conversation_id,created_at);
            CREATE TABLE IF NOT EXISTS draft_context_snapshots (
                user_id TEXT NOT NULL,draft_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,context_json TEXT NOT NULL,
                PRIMARY KEY(user_id,draft_id)
            );
            CREATE TABLE IF NOT EXISTS draft_reply_origins (
                user_id TEXT NOT NULL,draft_id TEXT NOT NULL,conversation_id TEXT NOT NULL,
                account_ref TEXT NOT NULL,peer_id TEXT NOT NULL,message_key TEXT NOT NULL,
                provider_message_id TEXT NOT NULL,source_body_sha256 TEXT NOT NULL,
                PRIMARY KEY(user_id,draft_id)
            );
            CREATE TABLE IF NOT EXISTS identities (
                user_id TEXT NOT NULL,source TEXT NOT NULL,external_id TEXT NOT NULL,
                identity_id TEXT NOT NULL,display_name TEXT NOT NULL,
                PRIMARY KEY(user_id,source,external_id)
            );
            CREATE TABLE IF NOT EXISTS reply_rules (
                user_id TEXT NOT NULL,identity_id TEXT NOT NULL,source TEXT NOT NULL,
                route_id TEXT NOT NULL,response_mode TEXT NOT NULL,
                PRIMARY KEY(user_id,identity_id,source)
            );
            CREATE TABLE IF NOT EXISTS provider_accounts (
                user_id TEXT NOT NULL,id TEXT NOT NULL,provider TEXT NOT NULL,
                display_name TEXT NOT NULL,can_read INTEGER NOT NULL,can_reply INTEGER NOT NULL,
                credential_ref TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(user_id,id)
            );
            CREATE TABLE IF NOT EXISTS channel_routes (
                user_id TEXT NOT NULL,source TEXT NOT NULL,route_id TEXT NOT NULL,
                PRIMARY KEY(user_id,source,route_id)
            );
            CREATE TABLE IF NOT EXISTS message_attachments (
                user_id TEXT NOT NULL,source TEXT NOT NULL,message_id TEXT NOT NULL,
                idx INTEGER NOT NULL,kind TEXT NOT NULL,content_type TEXT NOT NULL,
                filename TEXT NOT NULL,size INTEGER,src TEXT,
                attachment_id TEXT,provider_ref TEXT,transcript TEXT,
                transcription_status TEXT,transcription_model TEXT,
                PRIMARY KEY(user_id,source,message_id,idx)
            );
            CREATE INDEX IF NOT EXISTS message_attachments_msg_idx
                ON message_attachments(user_id,source,message_id);
            CREATE TABLE IF NOT EXISTS user_ai_settings (
                user_id TEXT PRIMARY KEY,
                endpoint TEXT NOT NULL,
                model TEXT NOT NULL,
                token TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_search_settings (
                user_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                qdrant_url TEXT NOT NULL DEFAULT '',
                qdrant_collection TEXT NOT NULL DEFAULT '',
                qdrant_token TEXT NOT NULL DEFAULT '',
                embedding_url TEXT NOT NULL DEFAULT '',
                embedding_model TEXT NOT NULL DEFAULT '',
                embedding_token TEXT NOT NULL DEFAULT '',
                dimensions INTEGER NOT NULL DEFAULT 2048,
                priority_conversations TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(user_id,key)
            );
            CREATE TABLE IF NOT EXISTS user_webhook_settings (
                user_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                endpoint TEXT NOT NULL,
                secret_ref TEXT NOT NULL,
                include_message_text INTEGER NOT NULL DEFAULT 0,
                cursor INTEGER NOT NULL DEFAULT 0,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL DEFAULT 0,
                last_attempt_at REAL,
                last_success_at REAL,
                last_error TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversation_summaries (
                user_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                through_message_rowid INTEGER NOT NULL DEFAULT 0,
                through_message_id TEXT NOT NULL DEFAULT '',
                summarized_message_count INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                PRIMARY KEY(user_id,conversation_id)
            );
            CREATE INDEX IF NOT EXISTS conversation_summaries_updated_idx
                ON conversation_summaries(user_id,updated_at);
        """
        for statement in script.split(";"):
            if statement.strip():
                self._connection.execute(statement)
        for table in ("task_contact_identity_bindings", "task_contact_qualifications"):
            columns = {str(row["name"]) for row in self._connection.execute(
                f"PRAGMA table_info({table})",
            )}
            if "endpoint_id" not in columns:
                # Rows produced by an earlier unshipped U52 draft cannot be
                # authorized without their exact endpoint; empty is fail-closed.
                self._connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN endpoint_id TEXT NOT NULL DEFAULT ''",
                )
        self._connection.execute("""
            CREATE TRIGGER IF NOT EXISTS task_mandates_immutable_update
            BEFORE UPDATE ON task_mandates
            WHEN NEW.user_id IS NOT OLD.user_id OR NEW.id IS NOT OLD.id
              OR NEW.issuer_user_id IS NOT OLD.issuer_user_id
              OR NEW.task_id IS NOT OLD.task_id OR NEW.actor_id IS NOT OLD.actor_id
              OR NEW.account_ref IS NOT OLD.account_ref OR NEW.channel IS NOT OLD.channel
              OR NEW.recipient_ids_json IS NOT OLD.recipient_ids_json
              OR NEW.purpose IS NOT OLD.purpose OR NEW.max_sends IS NOT OLD.max_sends
              OR NEW.policy_hash IS NOT OLD.policy_hash OR NEW.created_at IS NOT OLD.created_at
              OR NEW.expires_at IS NOT OLD.expires_at
              OR NOT ((NEW.revoked_at IS OLD.revoked_at AND NEW.version=OLD.version)
                      OR (OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL
                          AND NEW.version=OLD.version+1))
            BEGIN SELECT RAISE(ABORT,'task mandate policy is immutable'); END
        """)
        self._connection.execute("""
            CREATE TRIGGER IF NOT EXISTS task_mandates_no_delete
            BEFORE DELETE ON task_mandates
            BEGIN SELECT RAISE(ABORT,'task mandates are durable'); END
        """)
        self._connection.execute("""
            CREATE TRIGGER IF NOT EXISTS task_mandate_operations_no_update
            BEFORE UPDATE ON task_mandate_operations
            BEGIN SELECT RAISE(ABORT,'task mandate reservations are immutable'); END
        """)
        self._connection.execute("""
            CREATE TRIGGER IF NOT EXISTS task_mandate_operations_no_delete
            BEFORE DELETE ON task_mandate_operations
            BEGIN SELECT RAISE(ABORT,'task mandate reservations are durable'); END
        """)
        for table, label in (
            ("task_contact_identity_bindings", "task contact identity bindings"),
            ("task_contact_qualifications", "task contact qualifications"),
            ("task_mandate_qualifications", "task mandate qualification bindings"),
            ("task_mandate_draft_claims", "task mandate draft bindings"),
        ):
            self._connection.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_no_update
                BEFORE UPDATE ON {table}
                BEGIN SELECT RAISE(ABORT,'{label} are immutable'); END
            """)
            self._connection.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_no_delete
                BEFORE DELETE ON {table}
                BEGIN SELECT RAISE(ABORT,'{label} are durable'); END
            """)
        # Lightweight migration: conversations learn which connected account
        # owns them so replies always leave from the identity the user picked.
        try:
            self._connection.execute("ALTER TABLE conversations ADD COLUMN account_ref TEXT NOT NULL DEFAULT ''")
        except Exception:  # column already exists
            pass
        for column in ("conversation_kind TEXT NOT NULL DEFAULT 'unknown'", "peer_id TEXT NOT NULL DEFAULT ''"):
            try:
                self._connection.execute(f"ALTER TABLE conversations ADD COLUMN {column}")
            except sqlite3.OperationalError:
                pass
        try:
            self._connection.execute("ALTER TABLE workspace_events ADD COLUMN reconciled INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        # Attachment transcript metadata was added after the original media
        # table. Keep upgrades in-place; old databases must not need a rebuild.
        for column in ("transcript TEXT", "transcription_status TEXT", "transcription_model TEXT"):
            try:
                self._connection.execute(f"ALTER TABLE message_attachments ADD COLUMN {column}")
            except sqlite3.OperationalError:  # column already exists
                pass
        # Provider-side edit timestamps arrived after the original messages
        # table. Old databases upgrade in-place; absent means "never edited".
        try:
            self._connection.execute("ALTER TABLE messages ADD COLUMN edited_at REAL")
        except sqlite3.OperationalError:  # column already exists
            pass
        for column in ('provider_reply_to', 'provider_thread_id'):
            columns = {row['name'] for row in self._connection.execute('PRAGMA table_info(messages)')}
            if column not in columns:
                self._connection.execute(f"ALTER TABLE messages ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        try:
            self._connection.execute("ALTER TABLE drafts ADD COLUMN browser_notified_at REAL")
        except sqlite3.OperationalError:  # column already exists
            pass
        # When the send claim was taken, and how its outcome was resolved.
        # A claim without a timestamp cannot be reconciled against a provider
        # record, so the column is added idempotently next to the old ones.
        for column, definition in (("sending_at", "REAL"), ("delivery_state", "TEXT NOT NULL DEFAULT ''")):
            columns = {row['name'] for row in self._connection.execute('PRAGMA table_info(drafts)')}
            if column not in columns:
                try:
                    self._connection.execute(f"ALTER TABLE drafts ADD COLUMN {column} {definition}")
                except sqlite3.OperationalError:  # column already exists
                    pass
        for column in (
            "source TEXT NOT NULL DEFAULT ''",
            "account_ref TEXT NOT NULL DEFAULT ''",
            "peer_id TEXT NOT NULL DEFAULT ''",
            "message_id TEXT NOT NULL DEFAULT ''",
        ):
            try:
                self._connection.execute(
                    f"ALTER TABLE conversation_secretary_jobs ADD COLUMN {column}"
                )
            except sqlite3.OperationalError:  # column already exists
                pass

    def _conversation_secretary_jobs_migration(self) -> None:
        """Replace the pre-retry table whose event uniqueness erased attempt history."""
        columns = {
            str(row["name"])
            for row in self._connection.execute(
                "PRAGMA table_info(conversation_secretary_jobs)"
            )
        }
        unique_indexes = []
        for index in self._connection.execute(
            "PRAGMA index_list(conversation_secretary_jobs)"
        ).fetchall():
            if not int(index["unique"]) or int(index["partial"]):
                continue
            names = tuple(
                str(row["name"])
                for row in self._connection.execute(
                    f"PRAGMA index_info({index['name']})"
                ).fetchall()
            )
            unique_indexes.append(names)
        old_event_unique = ("user_id", "conversation_id", "event_seq") in unique_indexes
        if {"parent_job_id", "attempt"}.issubset(columns) and not old_event_unique:
            self._connection.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS conversation_secretary_jobs_root_idx
                   ON conversation_secretary_jobs(user_id,conversation_id,event_seq)
                   WHERE parent_job_id IS NULL"""
            )
            return
        self._connection.execute("DROP INDEX IF EXISTS conversation_secretary_jobs_status_idx")
        self._connection.execute("DROP INDEX IF EXISTS conversation_secretary_jobs_root_idx")
        self._connection.execute(
            "ALTER TABLE conversation_secretary_jobs RENAME TO legacy_conversation_secretary_jobs"
        )
        self._connection.execute(
            """CREATE TABLE conversation_secretary_jobs (
                user_id TEXT NOT NULL,job_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,event_seq INTEGER NOT NULL,
                triage_request_id TEXT NOT NULL,actor TEXT NOT NULL,
                source TEXT NOT NULL,account_ref TEXT NOT NULL,
                peer_id TEXT NOT NULL,message_id TEXT NOT NULL,
                parent_job_id TEXT,attempt INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'accepted',worker_id TEXT,
                session_id TEXT,session_url TEXT,result_text TEXT,last_error TEXT,
                created_at REAL NOT NULL,updated_at REAL NOT NULL,
                PRIMARY KEY(user_id,job_id),
                UNIQUE(user_id,parent_job_id),
                UNIQUE(user_id,conversation_id,event_seq,attempt)
            )"""
        )
        legacy_columns = {
            str(row["name"])
            for row in self._connection.execute(
                "PRAGMA table_info(legacy_conversation_secretary_jobs)"
            )
        }
        def source(name: str, default: str) -> str:
            return "l." + name if name in legacy_columns else default
        parent = source("parent_job_id", "NULL")
        if "parent_job_id" not in legacy_columns and "attempt" in legacy_columns:
            parent = """(SELECT p.job_id FROM legacy_conversation_secretary_jobs p
                WHERE p.user_id=l.user_id AND p.conversation_id=l.conversation_id
                  AND p.event_seq=l.event_seq AND p.attempt=l.attempt-1)"""
        message = source("message_id", "''")
        message = f"""COALESCE(NULLIF({message},''),(SELECT e.message_id
            FROM workspace_events e WHERE e.user_id=l.user_id AND e.seq=l.event_seq),'')"""
        self._connection.execute(
            f"""INSERT INTO conversation_secretary_jobs
                (user_id,job_id,conversation_id,event_seq,triage_request_id,actor,
                 source,account_ref,peer_id,message_id,parent_job_id,attempt,status,
                 worker_id,session_id,session_url,result_text,last_error,created_at,updated_at)
                SELECT user_id,job_id,conversation_id,event_seq,triage_request_id,actor,
                 {source('source', "''")},{source('account_ref', "''")},
                 {source('peer_id', "''")},{message},
                 {parent},{source('attempt', '1')},status,
                 worker_id,session_id,session_url,result_text,last_error,created_at,updated_at
                FROM legacy_conversation_secretary_jobs l"""
        )
        self._connection.execute("DROP TABLE legacy_conversation_secretary_jobs")
        self._connection.execute(
            """CREATE INDEX conversation_secretary_jobs_status_idx
               ON conversation_secretary_jobs(user_id,status,created_at)"""
        )
        self._connection.execute(
            """CREATE UNIQUE INDEX conversation_secretary_jobs_root_idx
               ON conversation_secretary_jobs(user_id,conversation_id,event_seq)
               WHERE parent_job_id IS NULL"""
        )

    def _backfill_workspace_events(self) -> None:
        marker = "workspace_events_backfilled_v1"
        if self._connection.execute(
            "SELECT 1 FROM settings WHERE key=?", (marker,)
        ).fetchone():
            return
        self._connection.execute(
            """
            INSERT OR IGNORE INTO workspace_events(user_id,source,message_id,conversation_id,eligible)
            SELECT m.user_id,m.source,m.message_id,m.conversation_id,
                   CASE
                     WHEN x.conversation_id IS NOT NULL OR r.action='ignore' THEN 0
                     WHEN r.action='allow' THEN 1
                     WHEN COALESCE(d.enabled, CASE WHEN c.conversation_kind='direct' THEN 1 ELSE 0 END)=1 THEN 1
                     ELSE 0
                   END
            FROM messages m
            JOIN conversations c ON c.user_id=m.user_id AND c.id=m.conversation_id
            LEFT JOIN workspace_exclusions x ON x.user_id=c.user_id AND x.conversation_id=c.id
            LEFT JOIN workspace_chat_rules r ON r.user_id=c.user_id AND r.conversation_id=c.id
            LEFT JOIN workspace_policy_defaults d ON d.user_id=c.user_id
                AND d.conversation_kind=c.conversation_kind
            WHERE m.direction='incoming' ORDER BY m.received_at,m.rowid
            """
        )
        self._connection.execute(
            "INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (marker, "1")
        )

    def _workspace_policy_migration(self) -> None:
        columns = {str(row["name"]) for row in self._connection.execute("PRAGMA table_info(workspace_events)")}
        eligible_added = "eligible" not in columns
        if eligible_added:
            self._connection.execute("ALTER TABLE workspace_events ADD COLUMN eligible INTEGER NOT NULL DEFAULT 0")
        marker = "workspace_conversation_kind_backfilled_v1"
        if self._connection.execute("SELECT 1 FROM settings WHERE key=?", (marker,)).fetchone():
            return
        # A numeric Telegram peer, unlike the display name, is stable. Historical
        # negative peers can be groups or channels; both stay disabled.
        self._connection.execute("""UPDATE conversations SET
            peer_id=substr((SELECT m.message_id FROM messages m WHERE m.user_id=conversations.user_id
                AND m.conversation_id=conversations.id AND m.source='telegram'
                ORDER BY m.received_at LIMIT 1),1,
                instr((SELECT m.message_id FROM messages m WHERE m.user_id=conversations.user_id
                AND m.conversation_id=conversations.id AND m.source='telegram'
                ORDER BY m.received_at LIMIT 1),':')-1)
            WHERE source='telegram' AND peer_id='' AND EXISTS
                (SELECT 1 FROM messages m WHERE m.user_id=conversations.user_id
                 AND m.conversation_id=conversations.id AND m.message_id GLOB '*:*')""")
        self._connection.execute("""UPDATE conversations SET conversation_kind='direct'
            WHERE source='telegram' AND conversation_kind='unknown'
              AND peer_id GLOB '[0-9]*' AND peer_id NOT LIKE '-%' AND peer_id!=''""")
        self._connection.execute("""UPDATE conversations SET conversation_kind='direct'
            WHERE (source IN ('mail','email','gmail','sms','phone','chatgpt','max')
                OR source LIKE 'gmail:%' OR source LIKE 'chatgpt:%')
              AND conversation_kind='unknown'""")
        self._connection.execute("""UPDATE conversations SET conversation_kind='group'
            WHERE source='whatsapp' AND sender LIKE '%@g.us' AND conversation_kind='unknown'""")
        self._connection.execute("""UPDATE conversations SET conversation_kind='direct'
            WHERE source='whatsapp' AND (sender LIKE '%@s.whatsapp.net' OR sender LIKE '%@lid')
              AND conversation_kind='unknown'""")
        legacy_rows = self._connection.execute("""SELECT user_id,id,account_ref,peer_id,conversation_key
            FROM conversations WHERE source='telegram' AND account_ref!='' AND peer_id!=''""").fetchall()
        targets: dict[tuple[str, str], list[str]] = {}
        for row in legacy_rows:
            peer = str(row["peer_id"])
            if not peer.lstrip("-").isdigit():
                continue
            key = f"telegram:{row['account_ref']}:{peer}"
            targets.setdefault((str(row["user_id"]), key), []).append(str(row["id"]))
        for (user, key), ids in targets.items():
            occupied = self._connection.execute("""SELECT id FROM conversations
                WHERE user_id=? AND conversation_key=?""", (user, key)).fetchone()
            if len(ids) == 1 and (occupied is None or str(occupied["id"]) == ids[0]):
                self._connection.execute("""UPDATE conversations SET conversation_key=?
                    WHERE user_id=? AND id=?""", (key, user, ids[0]))
                continue
            # Refuse to merge ambiguous historical chats or drop their rules.
            for conversation_id in set(ids + ([str(occupied["id"])] if occupied else [])):
                row = self._connection.execute("SELECT source FROM conversations WHERE user_id=? AND id=?",
                    (user, conversation_id)).fetchone()
                self._connection.execute("""UPDATE conversations SET conversation_kind='unknown'
                    WHERE user_id=? AND id=?""", (user, conversation_id))
                self._connection.execute("""INSERT OR IGNORE INTO workspace_exclusions
                    (user_id,conversation_id,source,reason,created_at,updated_at)
                    VALUES (?,?,?,?,?,?)""",
                    (user, conversation_id, str(row["source"]),
                     "ambiguous legacy Telegram account and peer", time.time(), time.time()))
        self._connection.execute("""UPDATE workspace_events SET eligible=0
            WHERE EXISTS (SELECT 1 FROM conversations c
                WHERE c.user_id=workspace_events.user_id AND c.id=workspace_events.conversation_id
                  AND c.conversation_kind!='direct')""")
        if eligible_added:
            # Preserve pre-policy direct inbox work, but never make historical
            # groups, channels, unknown chats or hard-denied chats claimable.
            self._connection.execute("""UPDATE workspace_events AS e SET eligible=1
                WHERE EXISTS (SELECT 1 FROM conversations c
                    LEFT JOIN workspace_chat_rules r ON r.user_id=c.user_id AND r.conversation_id=c.id
                    LEFT JOIN workspace_exclusions x ON x.user_id=c.user_id AND x.conversation_id=c.id
                    LEFT JOIN workspace_policy_defaults d ON d.user_id=c.user_id
                        AND d.conversation_kind=c.conversation_kind
                    WHERE c.user_id=e.user_id AND c.id=e.conversation_id
                      AND x.conversation_id IS NULL
                      AND (r.action='allow' OR (COALESCE(r.action,'inherit')='inherit'
                        AND COALESCE(d.enabled, CASE WHEN c.conversation_kind='direct' THEN 1 ELSE 0 END)=1)))""")
        self._connection.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (marker, "1"))

    @staticmethod
    def _digest(password: str, salt: bytes | None = None, iterations: int = _ITERATIONS) -> tuple[bytes, bytes]:
        salt = secrets.token_bytes(16) if salt is None else salt
        return salt, hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)

    @staticmethod
    def _token_hash(token: str) -> bytes:
        return hashlib.sha256(token.encode()).digest()

    @staticmethod
    def _credentials(username: str, password: str) -> tuple[str, str]:
        username = username.strip()
        if not _USERNAME.fullmatch(username):
            raise ValueError("username must be 3-64 safe characters")
        if len(password) < 8:
            raise ValueError("password must be at least 8 characters")
        return username, password

    def owner(self) -> UserPrincipal:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT u.id,u.username,u.role FROM settings s
                JOIN users u ON u.id=s.value WHERE s.key='service_owner_user_id'
                """
            ).fetchone()
        if row is None:
            raise RuntimeError("owner user is not configured")
        return UserPrincipal(str(row["id"]), str(row["username"]), str(row["role"]), True)

    def seed_owner(self, username: str, password: str) -> UserPrincipal:
        username, password = self._credentials(username, password)
        salt, digest = self._digest(password)
        now = time.time()
        with self._lock, self._connection:
            current = self.owner()
            row = self._connection.execute(
                "SELECT id FROM users WHERE username=? COLLATE NOCASE", (username,)
            ).fetchone()
            if row is None:
                user_id = current.user_id
                self._connection.execute(
                    """
                    UPDATE users SET username=?,password_salt=?,password_hash=?,
                        password_iterations=?,role='owner',updated_at=? WHERE id=?
                    """,
                    (username, salt, digest, _ITERATIONS, now, user_id),
                )
            else:
                user_id = str(row["id"])
                if user_id != current.user_id:
                    raise ValueError("seed username belongs to another user")
                self._connection.execute(
                    """
                    UPDATE users SET password_salt=?,password_hash=?,password_iterations=?,
                        role='owner',updated_at=? WHERE id=?
                    """,
                    (salt, digest, _ITERATIONS, now, user_id),
                )
            self._connection.execute(
                "INSERT OR REPLACE INTO settings VALUES ('service_owner_user_id',?)", (user_id,)
            )
        return UserPrincipal(user_id, username, "owner")

    def _create_user_record(
        self, username: str, password: str, *, role: str, issue_token: bool
    ) -> tuple[UserPrincipal, str | None]:
        username, password = self._credentials(username, password)
        if role not in {"user", "owner"}:
            raise ValueError("unsupported user role")
        salt, digest = self._digest(password)
        user_id, now = "user_" + uuid.uuid4().hex, time.time()
        try:
            with self._lock, self._connection:
                self._connection.execute(
                    """
                    INSERT INTO users
                    (id,username,password_salt,password_hash,password_iterations,role,created_at,updated_at,auth_kind)
                    VALUES (?,?,?,?,?,?,?,?,'password')
                    """,
                    (user_id, username, salt, digest, _ITERATIONS, role, now, now),
                )
                token = self._issue_token(user_id) if issue_token else None
        except sqlite3.IntegrityError as error:
            raise ValueError("username already exists") from error
        return UserPrincipal(user_id, username, role), token

    def create_user(self, username: str, password: str, *, role: str = "user") -> tuple[UserPrincipal, str]:
        principal, token = self._create_user_record(
            username, password, role=role, issue_token=True
        )
        assert token is not None
        return principal, token

    def register_user(self, username: str, password: str) -> UserPrincipal:
        principal, _ = self._create_user_record(
            username, password, role="user", issue_token=False
        )
        return principal

    def login(self, username: str, password: str) -> tuple[UserPrincipal, str] | None:
        principal = self.authenticate_credentials(username, password)
        if principal is None:
            return None
        with self._lock, self._connection:
            token = self._issue_token(principal.user_id)
        return principal, token

    def authenticate_credentials(self, username: str, password: str) -> UserPrincipal | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM users WHERE username=? COLLATE NOCASE", (username.strip(),)
            ).fetchone()
            salt = bytes(16) if row is None else bytes(row["password_salt"])
            iterations = _ITERATIONS if row is None else int(row["password_iterations"])
            _, digest = self._digest(password, salt, iterations)
            expected = bytes(32) if row is None else bytes(row["password_hash"])
            if row is None or not hmac.compare_digest(digest, expected):
                return None
            if str(row["auth_kind"] or "password") != "password":
                # Users registered with a one-time code own no usable
                # password; they authenticate with a code only.
                return None
        return UserPrincipal(str(row["id"]), str(row["username"]), str(row["role"]))

    # -- passwordless login ------------------------------------------------
    # Challenges and identities are durable state: a code is stored only as a
    # hash, and a verified channel identity is bound to exactly one user.

    def create_login_challenge(self, *, channel: str, destination: str, code_hash: bytes,
                               salt: bytes, ttl_seconds: int, request_ip: str = "") -> str:
        challenge_id = "login_" + uuid.uuid4().hex
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO login_challenges
                (id,channel,destination,code_hash,salt,created_at,expires_at,consumed_at,attempts,request_ip)
                VALUES (?,?,?,?,?,?,?,NULL,0,?)
                """,
                (challenge_id, channel, destination, code_hash, salt, now,
                 now + max(1, int(ttl_seconds)), request_ip),
            )
        return challenge_id

    def login_challenge_count(self, *, channel: str, destination: str, since: float) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS total FROM login_challenges"
                " WHERE channel=? AND destination=? AND created_at>=?",
                (channel, destination, since),
            ).fetchone()
        return int(row["total"])

    def login_challenge_count_from_ip(self, *, request_ip: str, since: float) -> int:
        if not request_ip:
            return 0
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS total FROM login_challenges"
                " WHERE request_ip=? AND created_at>=?",
                (request_ip, since),
            ).fetchone()
        return int(row["total"])

    def login_challenge_candidates(self, *, channel: str, destination: str, now: float) -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id,code_hash,salt,expires_at,attempts FROM login_challenges
                WHERE channel=? AND destination=? AND consumed_at IS NULL AND expires_at>=?
                ORDER BY created_at DESC LIMIT 5
                """,
                (channel, destination, now),
            ).fetchall()
        return [dict(row) for row in rows]

    def consume_login_challenge(self, challenge_id: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE login_challenges SET consumed_at=? WHERE id=? AND consumed_at IS NULL",
                (time.time(), challenge_id),
            )
        return cursor.rowcount == 1

    def bump_login_challenge_attempts(self, challenge_ids: list[str]) -> None:
        if not challenge_ids:
            return
        with self._lock, self._connection:
            self._connection.executemany(
                "UPDATE login_challenges SET attempts=attempts+1 WHERE id=?",
                [(challenge_id,) for challenge_id in challenge_ids],
            )

    def login_identity(self, *, channel: str, identifier: str) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT id,user_id,channel,identifier,display_name,verified_at"
                " FROM login_identities WHERE channel=? AND identifier=?",
                (channel, identifier),
            ).fetchone()
        return None if row is None else dict(row)

    def login_identities(self) -> list[dict[str, object]]:
        """List every bound identity with the user it opens."""

        with self._lock:
            rows = self._connection.execute(
                "SELECT i.id,i.user_id,i.channel,i.identifier,i.display_name,i.verified_at,"
                " u.username,u.role FROM login_identities i"
                " LEFT JOIN users u ON u.id=i.user_id"
                " ORDER BY i.channel,i.identifier"
            ).fetchall()
        return [dict(row) for row in rows]

    def login_identity_user(self, *, channel: str, identifier: str) -> UserPrincipal | None:
        identity = self.login_identity(channel=channel, identifier=identifier)
        if identity is None:
            return None
        return self.user(str(identity["user_id"]))

    def register_login_identity(self, *, user_id: str, channel: str, identifier: str,
                                display_name: str = "") -> dict[str, object]:
        """Bind a verified channel identity to an existing user.

        An owner who already owns a user binds their own verified address or
        Telegram account to it, so a one-time code opens that user instead of
        registering a second one.
        """
        if self.user(user_id) is None:
            raise ValueError("unknown user")
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO login_identities
                (id,user_id,channel,identifier,display_name,verified_at,created_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(channel,identifier) DO UPDATE SET
                    user_id=excluded.user_id, display_name=excluded.display_name,
                    verified_at=excluded.verified_at
                """,
                ("identity_" + uuid.uuid4().hex, user_id, channel, identifier, display_name, now, now),
            )
        identity = self.login_identity(channel=channel, identifier=identifier)
        assert identity is not None
        return identity

    def remove_login_identity(self, *, channel: str, identifier: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM login_identities WHERE channel=? AND identifier=?",
                (channel, identifier),
            )
        return cursor.rowcount > 0

    def create_login_user(self, *, channel: str, identifier: str,
                          display_name: str = "") -> tuple[UserPrincipal, str]:
        """Register a passwordless user for a verified channel identity.

        The generated password is random and never disclosed, so the account
        can only be entered with a one-time code.
        """
        username = "u" + hashlib.sha256((channel + "\0" + identifier).encode()).hexdigest()[:16]
        salt, digest = self._digest(secrets.token_urlsafe(32))
        user_id, now = "user_" + uuid.uuid4().hex, time.time()
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT id FROM users WHERE username=? COLLATE NOCASE", (username,)
            ).fetchone()
            if row is None:
                self._connection.execute(
                    """
                    INSERT INTO users
                    (id,username,password_salt,password_hash,password_iterations,role,created_at,updated_at,auth_kind)
                    VALUES (?,?,?,?,?,'user',?,?,'code')
                    """,
                    (user_id, username, salt, digest, _ITERATIONS, now, now),
                )
            else:
                user_id = str(row["id"])
            self._connection.execute(
                """
                INSERT INTO login_identities
                (id,user_id,channel,identifier,display_name,verified_at,created_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(channel,identifier) DO UPDATE SET
                    user_id=excluded.user_id, display_name=excluded.display_name,
                    verified_at=excluded.verified_at
                """,
                ("identity_" + uuid.uuid4().hex, user_id, channel, identifier, display_name, now, now),
            )
            token = self._issue_token(user_id)
        return UserPrincipal(user_id, username, "user"), token

    def issue_access_token(self, user_id: str) -> str:
        with self._lock, self._connection:
            return self._issue_token(user_id)

    def _issue_token(self, user_id: str) -> str:
        token = "uio_" + secrets.token_urlsafe(32)
        self._connection.execute(
            """
            INSERT INTO api_tokens
            (id,user_id,token_hash,created_at,revoked_at,kind,token_type)
            VALUES (?,?,?,?,NULL,'personal','access')
            """,
            ("token_" + uuid.uuid4().hex, user_id, self._token_hash(token), time.time()),
        )
        return token

    def authenticate_token(self, token: str) -> UserPrincipal | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT u.id,u.username,u.role,t.kind,t.scope,t.account_refs FROM api_tokens t JOIN users u ON u.id=t.user_id
                WHERE t.token_hash=? AND t.revoked_at IS NULL AND t.token_type='access'
                  AND t.kind<>'task_mandate'
                  AND (t.expires_at IS NULL OR t.expires_at>?)
                """,
                (self._token_hash(token), time.time()),
            ).fetchone()
        return None if row is None else UserPrincipal(
            str(row["id"]), str(row["username"]), str(row["role"]),
            # Legacy admin-only tokens fail closed after that token scope was
            # replaced by per-account rights on scoped_messenger tokens.
            read_only=row["kind"] == "personal" and row["scope"] in {"read_only", "telegram_admin"},
            messenger_accounts=(tuple(json.loads(row['account_refs'] or '[]'))
                                if row['scope'] in {'messenger_actions','telegram_admin'} else tuple(g['account_ref'] for g in json.loads(row['account_refs'] or '[]')) if row['scope']=='scoped_messenger' else None),
            meeting_recorder=row['kind']=='personal' and row['scope']=='meeting_recording',
            messenger_grants=tuple(json.loads(row['account_refs'] or '[]')) if row['scope']=='scoped_messenger' else None,
        )

    def register_oauth_client(
        self, *, redirect_uris: list[str], token_endpoint_auth_method: str
    ) -> tuple[str, str | None]:
        client_id = "client_" + secrets.token_urlsafe(24)
        secret = None if token_endpoint_auth_method == "none" else secrets.token_urlsafe(32)
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO oauth_clients VALUES (?,?,?,?,?)",
                (
                    client_id, None if secret is None else self._token_hash(secret),
                    json.dumps(redirect_uris), token_endpoint_auth_method, time.time(),
                ),
            )
        return client_id, secret

    def oauth_client(self, client_id: str) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT id,redirect_uris,token_endpoint_auth_method FROM oauth_clients WHERE id=?",
                (client_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "client_id": str(row["id"]),
            "redirect_uris": json.loads(str(row["redirect_uris"])),
            "token_endpoint_auth_method": str(row["token_endpoint_auth_method"]),
        }

    def authenticate_oauth_client(self, client_id: str, secret: str | None) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM oauth_clients WHERE id=?", (client_id,)
            ).fetchone()
        if row is None:
            return None
        method = str(row["token_endpoint_auth_method"])
        if method == "none":
            if secret:
                return None
        elif not secret or not hmac.compare_digest(bytes(row["secret_hash"]), self._token_hash(secret)):
            return None
        return {
            "client_id": str(row["id"]),
            "redirect_uris": json.loads(str(row["redirect_uris"])),
            "token_endpoint_auth_method": method,
        }

    def create_oauth_session(self, user_id: str, *, lifetime: int = 600) -> str:
        session = secrets.token_urlsafe(32)
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO oauth_sessions VALUES (?,?,?)",
                (self._token_hash(session), user_id, time.time() + lifetime),
            )
        return session

    def oauth_session_user(self, session: str) -> UserPrincipal | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT u.id,u.username,u.role FROM oauth_sessions s JOIN users u ON u.id=s.user_id
                WHERE s.session_hash=? AND s.expires_at>?
                """,
                (self._token_hash(session), time.time()),
            ).fetchone()
        return None if row is None else UserPrincipal(
            str(row["id"]), str(row["username"]), str(row["role"])
        )

    def create_oauth_code(
        self, *, user_id: str, client_id: str, redirect_uri: str, code_challenge: str | None,
        scope: str,
    ) -> str:
        code = "code_" + secrets.token_urlsafe(32)
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO oauth_authorization_codes VALUES (?,?,?,?,?,?,?,NULL)",
                (
                    self._token_hash(code), user_id, client_id, redirect_uri, code_challenge,
                    scope, time.time() + 60,
                ),
            )
        return code

    def redeem_oauth_code(
        self, *, code: str, client_id: str, redirect_uri: str, code_challenge: str | None
    ) -> tuple[str, str] | None:
        now = time.time()
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM oauth_authorization_codes WHERE code_hash=?",
                (self._token_hash(code),),
            ).fetchone()
            if (
                row is None or row["used_at"] is not None or float(row["expires_at"]) <= now
                or str(row["client_id"]) != client_id or str(row["redirect_uri"]) != redirect_uri
                or not hmac.compare_digest(str(row["code_challenge"] or ""), code_challenge or "")
            ):
                return None
            changed = self._connection.execute(
                "UPDATE oauth_authorization_codes SET used_at=? WHERE code_hash=? AND used_at IS NULL",
                (now, self._token_hash(code)),
            ).rowcount
        return None if not changed else (str(row["user_id"]), str(row["scope"]))

    def issue_oauth_tokens(self, *, user_id: str, client_id: str, scope: str) -> dict[str, object]:
        access_token = "uio_oauth_" + secrets.token_urlsafe(32)
        refresh_token = "uio_refresh_" + secrets.token_urlsafe(32)
        now = time.time()
        with self._lock, self._connection:
            self._insert_oauth_token(access_token, user_id, client_id, scope, "access", now + 3600)
            self._insert_oauth_token(refresh_token, user_id, client_id, scope, "refresh", now + 30 * 86400)
        return {
            "access_token": access_token, "token_type": "Bearer", "expires_in": 3600,
            "refresh_token": refresh_token, "scope": scope,
        }

    def rotate_oauth_refresh(self, *, refresh_token: str, client_id: str) -> dict[str, object] | None:
        now = time.time()
        with self._lock, self._connection:
            row = self._connection.execute(
                """
                SELECT * FROM api_tokens WHERE token_hash=? AND kind='oauth'
                AND token_type='refresh' AND revoked_at IS NULL AND expires_at>?
                """,
                (self._token_hash(refresh_token), now),
            ).fetchone()
            if row is None or str(row["oauth_client_id"]) != client_id:
                return None
            if not self._connection.execute(
                "UPDATE api_tokens SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                (now, str(row["id"])),
            ).rowcount:
                return None
            access_token = "uio_oauth_" + secrets.token_urlsafe(32)
            next_refresh = "uio_refresh_" + secrets.token_urlsafe(32)
            user_id, scope = str(row["user_id"]), str(row["scope"])
            self._insert_oauth_token(access_token, user_id, client_id, scope, "access", now + 3600)
            self._insert_oauth_token(next_refresh, user_id, client_id, scope, "refresh", now + 30 * 86400)
        return {
            "access_token": access_token, "token_type": "Bearer", "expires_in": 3600,
            "refresh_token": next_refresh, "scope": scope,
        }

    def _insert_oauth_token(
        self, token: str, user_id: str, client_id: str, scope: str, token_type: str, expires_at: float
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO api_tokens
            (id,user_id,token_hash,created_at,revoked_at,kind,token_type,expires_at,oauth_client_id,scope)
            VALUES (?,?,?,?,NULL,'oauth',?,?,?,?)
            """,
            (
                "token_" + uuid.uuid4().hex, user_id, self._token_hash(token), time.time(),
                token_type, expires_at, client_id, scope,
            ),
        )

    def user(self, reference: str) -> UserPrincipal | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT id,username,role FROM users
                WHERE id=? OR username=? COLLATE NOCASE LIMIT 1
                """,
                (reference, reference),
            ).fetchone()
        return None if row is None else UserPrincipal(
            str(row["id"]), str(row["username"]), str(row["role"])
        )

    def bind_channel_route(self, *, user_id: str, source: str, route_id: str) -> None:
        if self.user(user_id) is None or not source.strip() or not route_id.strip():
            raise ValueError("user, source and route_id are required")
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO channel_routes VALUES (?,?,?)",
                (user_id, source.strip().lower(), route_id.strip()),
            )

    def route_allowed(self, *, user_id: str, source: str, route_id: str) -> bool:
        if user_id == self.default_user_id:
            return True
        public_source = "mail" if source in {"mail", "email", "gmail"} or source.startswith(("gmail:", "email:")) else source
        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1 FROM channel_routes
                WHERE user_id=? AND source IN (?,?) AND route_id=?
                """,
                (user_id, source, public_source, route_id),
            ).fetchone()
        return row is not None

    def ingress_user(self, *, source: str, account_id: str = "") -> str | None:
        clauses, values = ["enabled=1"], []
        if account_id:
            clauses.append("(id=? OR credential_ref=?)")
            # Himalaya ingress sends the himalaya account name ("gmail",
            # "careviolan"); registered gmail accounts carry it as
            # credential_ref ("himalaya:<name>"), not in the id.
            values.extend([account_id, "himalaya:" + account_id])
        elif source.startswith("gmail:"):
            clauses.append("credential_ref=?")
            values.append("himalaya:" + source.partition(":")[2])
        else:
            return None
        with self._lock:
            rows = self._connection.execute(
                f"SELECT DISTINCT user_id FROM provider_accounts WHERE {' AND '.join(clauses)} LIMIT 2",
                values,
            ).fetchall()
        if len(rows) == 1:
            return str(rows[0]["user_id"])
        if len(rows) > 1:
            raise ValueError("connector account ownership is ambiguous")
        if account_id:
            raise ValueError("connector account is not registered")
        return None

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _user(self, user_id: str | None) -> str:
        return self.default_user_id if user_id is None else user_id

    def register_identity(
        self, *, source: str, external_id: str, identity_id: str, display_name: str,
        user_id: str | None = None,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO identities VALUES (?,?,?,?,?)",
                (self._user(user_id), source, external_id, identity_id, display_name),
            )

    def register_account(
        self, *, account_id: str, provider: str, display_name: str, can_read: bool,
        can_reply: bool, credential_ref: str, enabled: bool = True,
        user_id: str | None = None,
    ) -> None:
        if not account_id.strip() or not provider.strip() or not credential_ref.strip():
            raise ValueError("account id, provider and credential reference are required")
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO provider_accounts VALUES (?,?,?,?,?,?,?,?)",
                (
                    self._user(user_id), account_id, provider.lower(), display_name or account_id,
                    int(can_read), int(can_reply), credential_ref, int(enabled),
                ),
            )

    def accounts(self, *, user_id: str | None = None) -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id,provider,display_name,can_read,can_reply,credential_ref,enabled
                FROM provider_accounts WHERE user_id=? ORDER BY id
                """,
                (self._user(user_id),),
            ).fetchall()
        return [{
            "id": row["id"], "provider": row["provider"], "display_name": row["display_name"],
            "capabilities": [
                name for name, value in (("read", row["can_read"]), ("reply", row["can_reply"])) if value
            ],
            "credential_ref": row["credential_ref"], "enabled": bool(row["enabled"]),
        } for row in rows]

    def source_can_reply(self, source: str, *, user_id: str | None = None) -> bool | None:
        """Return a configured source account's reply capability, when it exists."""
        account_id = source.strip().lower()
        if account_id.startswith("gmail:"):
            account_id = "gmail-" + account_id.removeprefix("gmail:")
        with self._lock:
            row = self._connection.execute(
                "SELECT can_reply FROM provider_accounts WHERE user_id=? AND id=? AND enabled=1",
                (self._user(user_id), account_id),
            ).fetchone()
        return None if row is None else bool(row["can_reply"])

    def latest_message_at(self, source: str, *, user_id: str | None = None, account_ref: str = '') -> float | None:
        """Return message freshness for one exact provider source."""
        with self._lock:
            row = self._connection.execute(
                "SELECT MAX(m.received_at) AS latest FROM messages m JOIN conversations c ON c.user_id=m.user_id AND c.id=m.conversation_id WHERE m.user_id=? AND m.source=? AND (?='' OR c.account_ref=?)",
                (self._user(user_id), source, account_ref, account_ref),
            ).fetchone()
        return None if row is None or row["latest"] is None else float(row["latest"])

    def match_sms_receipt(self, receipt: str, *, account_ref: str, sender: str, body: str, user_id: str | None = None) -> str:
        """Link a native sent-store message only to this exact approved reply."""
        with self._lock:
            row=self._connection.execute("""SELECT d.outbox_receipt FROM drafts d JOIN conversations c ON c.user_id=d.user_id AND c.id=d.conversation_id
                WHERE d.user_id=? AND d.status='approved' AND d.outbox_receipt=? AND d.body=? AND c.source='sms' AND c.account_ref=? AND c.sender=?""", (self._user(user_id),receipt,body,account_ref,sender)).fetchone()
        return str(row[0]) if row is not None else ''

    def delete_account(self, account_id: str, *, user_id: str | None = None) -> bool:
        with self._lock, self._connection:
            return self._connection.execute(
                "DELETE FROM provider_accounts WHERE user_id=? AND id=?",
                (self._user(user_id), account_id),
            ).rowcount == 1

    def set_rule(
        self, *, identity_id: str, source: str, route_id: str, mode: str,
        user_id: str | None = None,
    ) -> None:
        if mode not in {"suggest", "approve", "auto_send"}:
            raise ValueError("unsupported reply mode")
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO reply_rules VALUES (?,?,?,?,?)",
                (self._user(user_id), identity_id, source, route_id, mode),
            )

    def policy_for(
        self, message: InboxMessage, *, fallback_route_id: str, user_id: str | None = None
    ) -> ConversationPolicy:
        user_id = self._user(user_id)
        with self._lock:
            identity = self._connection.execute(
                "SELECT identity_id FROM identities WHERE user_id=? AND source=? AND external_id=?",
                (user_id, message.source, message.sender),
            ).fetchone()
            identity_id = str(identity["identity_id"]) if identity else None
            rule = None if identity_id is None else self._connection.execute(
                """
                SELECT route_id,response_mode FROM reply_rules
                WHERE user_id=? AND identity_id=? AND source=?
                """,
                (user_id, identity_id, message.source),
            ).fetchone()
        return ConversationPolicy(
            str(rule["route_id"]), str(rule["response_mode"]), identity_id
        ) if rule else ConversationPolicy(fallback_route_id, "approve", identity_id)

    def conversation_id_for_key(self, conversation_key: str, *, user_id: str, account_ref: str | None = None) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT id FROM conversations WHERE user_id=? AND conversation_key=?",
                (user_id, conversation_key),
            ).fetchone()
            if row is not None and account_ref is not None and conversation_key.startswith('phone:'):
                scoped = self._connection.execute('SELECT account_ref FROM conversations WHERE user_id=? AND id=?',
                    (user_id, self.resolve_conversation_id(str(row['id']), user_id=user_id))).fetchone()
                if scoped is not None and scoped[0] not in ('', account_ref):
                    row = None
            if row is None and conversation_key.startswith("phone:"):
                # Historical call UUID keys still resolve after grouping, even
                # for the canonical call whose old key was replaced.
                account_sql = ' AND c.account_ref IN (\'\',?)' if account_ref is not None else ''
                values = [user_id, conversation_key[len('phone:'):]]
                if account_ref is not None:
                    values.append(account_ref)
                row = self._connection.execute("SELECT m.conversation_id AS id FROM messages m JOIN conversations c ON c.user_id=m.user_id AND c.id=m.conversation_id WHERE m.user_id=? AND m.source='phone' AND m.sender=?" + account_sql + ' LIMIT 1', values).fetchone()
        return None if row is None else self.resolve_conversation_id(str(row["id"]), user_id=user_id)

    def resolve_conversation_id(self, conversation_id: str, *, user_id: str | None = None) -> str:
        with self._lock:
            row = self._connection.execute("SELECT canonical_id FROM phone_conversation_aliases WHERE user_id=? AND old_id=?",
                (self._user(user_id), conversation_id)).fetchone()
        return str(row[0]) if row is not None else conversation_id

    def group_phone_conversations(self, *, account_ref: str, call_peers: dict[str, str],
                                  labels: dict[str, str] | None = None,
                                  user_id: str | None = None, dry_run: bool = False) -> dict:
        from .phone_grouping import grouping_plan, apply_grouping
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            plan = grouping_plan(self._connection, user_id=self._user(user_id),
                                 account_ref=account_ref, call_peers=call_peers)
            if dry_run:
                return plan
            return apply_grouping(self._connection, plan, labels or {})

    @staticmethod
    def _message_kind(message: InboxMessage) -> str:
        # Provider-native peer shapes are stronger evidence than an ingress
        # hint. Contradictory envelopes must fail closed instead of smuggling a
        # group through the default-enabled direct policy.
        if message.source == "telegram":
            peer = message.peer_id or message.message_id.rsplit("|", 1)[-1].split(":", 1)[0]
            if peer.startswith("-") and message.conversation_kind == "direct":
                return "unknown"  # Negative peers can be groups or channels.
        if message.source == "whatsapp":
            peer = (message.peer_id or message.sender).lower()
            if peer.endswith("@g.us") and message.conversation_kind == "direct":
                return "group"
        if message.conversation_kind:
            if message.conversation_kind not in {"direct", "group", "channel", "unknown", "telegram_bot"}:
                raise ValueError("unsupported conversation_kind")
            return message.conversation_kind
        if message.source == "telegram":
            peer = message.peer_id or message.message_id.rsplit("|", 1)[-1].split(":", 1)[0]
            if peer.lstrip("-").isdigit() and peer.startswith("-"):
                return "unknown"  # Could be a group or channel; both deny by default.
            return "direct" if peer.isdigit() else "unknown"
        if message.source == "whatsapp":
            peer = (message.peer_id or message.sender).lower()
            return "group" if peer.endswith("@g.us") else (
                "direct" if peer.endswith("@s.whatsapp.net") or peer.endswith("@lid") else "unknown")
        if message.source in {"mail", "email", "gmail", "sms", "phone", "chatgpt", "max"} or (
            message.source.startswith(("gmail:", "email:", "chatgpt:"))):
            return "direct"
        return "unknown"

    def workspace_policy(self, *, user_id: str | None = None) -> dict[str, object]:
        user = self._user(user_id)
        defaults = {"direct": True, "group": False, "channel": False, "unknown": False,
                    "telegram_bot": False}
        with self._lock:
            rows = self._connection.execute(
                "SELECT conversation_kind,enabled FROM workspace_policy_defaults WHERE user_id=?", (user,)
            ).fetchall()
            revision = self.user_preference("workspace_policy_revision", user_id=user, default="0")
        defaults.update({str(row["conversation_kind"]): bool(row["enabled"]) for row in rows})
        return {"defaults": defaults, "revision": int(revision or 0)}

    def account_ai_disabled(self, account_ref: str, *, user_id: str | None = None) -> bool:
        """Keep an operator-excluded account readable without Secretary processing."""
        return bool(account_ref) and self.user_preference(
            "ai_disabled_account:" + account_ref, user_id=user_id, default="0",
        ) == "1"

    def set_account_ai_enabled(self, account_ref: str, enabled: bool, *,
                               user_id: str | None = None) -> None:
        user = self._user(user_id)
        if type(enabled) is not bool or not account_ref or len(account_ref) > 256:
            raise ValueError("invalid account AI policy")
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO user_preferences(user_id,key,value,updated_at) VALUES (?,?,?,?)",
                (user, "ai_disabled_account:" + account_ref, "0" if enabled else "1", time.time()),
            )
            if not enabled:
                self._connection.execute("""UPDATE workspace_events SET eligible=0
                    WHERE user_id=? AND conversation_id IN (
                        SELECT id FROM conversations WHERE user_id=? AND account_ref=?)""",
                    (user, user, account_ref))
                self._connection.execute("""DELETE FROM conversation_summaries
                    WHERE user_id=? AND conversation_id IN (
                        SELECT id FROM conversations WHERE user_id=? AND account_ref=?)""",
                    (user, user, account_ref))
            self._bump_workspace_policy(user)

    def _bump_workspace_policy(self, user_id: str) -> int:
        self._connection.execute("""INSERT INTO user_preferences(user_id,key,value,updated_at)
            VALUES (?,'workspace_policy_revision','1',?)
            ON CONFLICT(user_id,key) DO UPDATE SET
                value=CAST(CAST(value AS INTEGER)+1 AS TEXT),updated_at=excluded.updated_at""",
            (user_id, time.time()))
        return int(self.user_preference("workspace_policy_revision", user_id=user_id) or 0)

    def workspace_triage_settings(self, *, user_id: str | None = None) -> dict[str, object]:
        user = self._user(user_id)
        with self._lock:
            row = self._connection.execute(
                "SELECT enabled,threshold,min_confidence,revision,updated_at "
                "FROM workspace_triage_settings WHERE user_id=?", (user,),
            ).fetchone()
        if row is None:
            return {
                "enabled": True, "threshold": 0.75, "min_confidence": 0.65,
                "revision": 1, "policy_version": "workspace-triage-v1:r1",
            }
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
        result["policy_version"] = f"workspace-triage-v1:r{int(result['revision'])}"
        return result

    def set_workspace_triage_settings(
        self, *, enabled: bool | None = None, threshold: float | None = None,
        min_confidence: float | None = None, user_id: str | None = None,
    ) -> dict[str, object]:
        if enabled is None and threshold is None and min_confidence is None:
            raise ValueError("at least one triage setting is required")
        if enabled is not None and type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        for name, value in (("threshold", threshold), ("min_confidence", min_confidence)):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1
            ):
                raise ValueError(f"{name} must be between 0 and 1")
        user = self._user(user_id)
        current = self.workspace_triage_settings(user_id=user)
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO workspace_triage_settings
                   (user_id,enabled,threshold,min_confidence,revision,updated_at)
                   VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                   enabled=excluded.enabled,threshold=excluded.threshold,
                   min_confidence=excluded.min_confidence,
                   revision=workspace_triage_settings.revision+1,
                   updated_at=excluded.updated_at""",
                (
                    user, int(current["enabled"] if enabled is None else enabled),
                    float(current["threshold"] if threshold is None else threshold),
                    float(current["min_confidence"] if min_confidence is None else min_confidence),
                    int(current["revision"]) + 1, now,
                ),
            )
        return self.workspace_triage_settings(user_id=user)

    def _invalidate_workspace_events_for_chat(self, user_id: str, conversation_id: str) -> None:
        """Irreversibly retire queued work when a chat becomes disabled.

        Policy changes never flip an old event back to eligible; a later allow
        applies only to messages ingested after that change.
        """
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user_id)
        self._connection.execute(
            "UPDATE workspace_events SET eligible=0 WHERE user_id=? AND conversation_id=?",
            (user_id, conversation_id),
        )

    def set_workspace_default(
        self, *, conversation_kind: str, enabled: bool, user_id: str | None = None,
    ) -> dict[str, object]:
        if conversation_kind not in {"direct", "group", "channel", "unknown", "telegram_bot"}:
            raise ValueError("unsupported conversation_kind")
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        user = self._user(user_id)
        with self._lock, self._connection:
            self._connection.execute("""INSERT INTO workspace_policy_defaults
                (user_id,conversation_kind,enabled,updated_at) VALUES (?,?,?,?)
                ON CONFLICT(user_id,conversation_kind) DO UPDATE SET
                enabled=excluded.enabled,updated_at=excluded.updated_at""",
                (user, conversation_kind, int(enabled), time.time()))
            # Keep explicit allows alive, but retire every event whose effective
            # policy is denied after this change. No later enable reactivates it.
            self._connection.execute("""UPDATE workspace_events SET eligible=0
                WHERE user_id=? AND conversation_id IN (
                    SELECT c.id FROM conversations c
                    LEFT JOIN workspace_chat_rules r
                      ON r.user_id=c.user_id AND r.conversation_id=c.id
                    LEFT JOIN workspace_exclusions x
                      ON x.user_id=c.user_id AND x.conversation_id=c.id
                    WHERE c.user_id=? AND c.conversation_kind=?
                      AND (x.conversation_id IS NOT NULL OR r.action='ignore'
                        OR (COALESCE(r.action,'inherit')='inherit' AND ?=0))
                )""", (user, user, conversation_kind, int(enabled)))
            if conversation_kind == "telegram_bot" and not enabled:
                self._connection.execute("""UPDATE workspace_events SET eligible=0
                    WHERE user_id=? AND EXISTS (SELECT 1 FROM messages m
                        WHERE m.user_id=workspace_events.user_id
                          AND m.source=workspace_events.source
                          AND m.message_id=workspace_events.message_id
                          AND m.sender_is_bot=1)""", (user,))
            self._bump_workspace_policy(user)
        return self.workspace_policy(user_id=user)

    def workspace_chat_rules(
        self, *, user_id: str | None = None, source: str = "", account_ref: str = "",
        peer_id: str = "",
        conversation_kind: str = "", action: str = "", query: str = "",
        conversation_id: str = "", limit: int = 100, offset: int = 0,
    ) -> list[dict[str, object]]:
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user_id)
        user = self._user(user_id)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if type(offset) is not int or not 0 <= offset <= 100_000:
            raise ValueError("offset must be between 0 and 100000")
        if conversation_kind and conversation_kind not in {"direct", "group", "channel", "unknown", "telegram_bot"}:
            raise ValueError("unsupported conversation_kind")
        if action and action not in {"allow", "ignore", "inherit"}:
            raise ValueError("unsupported action")
        filters: list[str] = ["c.user_id=?"]
        values: list[object] = [user]
        for column, value in (("c.source", source), ("c.account_ref", account_ref),
                              ("c.peer_id", peer_id),
                              ("c.conversation_kind", conversation_kind), ("c.id", conversation_id)):
            if value:
                filters.append(f"{column}=?")
                values.append(value)
        if query:
            filters.append("(c.sender LIKE ? OR c.peer_id LIKE ?)")
            pattern = "%" + query[:128] + "%"
            values.extend((pattern, pattern))
        if action:
            filters.append("(CASE WHEN x.conversation_id IS NOT NULL THEN 'ignore' ELSE COALESCE(r.action,'inherit') END)=?")
            values.append(action)
        with self._lock:
            rows = self._connection.execute(f"""SELECT c.id AS conversation_id,c.source,
                c.account_ref,c.peer_id,c.sender AS chat_name,c.conversation_kind,
                CASE WHEN x.conversation_id IS NOT NULL THEN 'ignore'
                    ELSE COALESCE(r.action,'inherit') END AS action,
                COALESCE(r.reason,x.reason,'') AS reason
                FROM conversations c LEFT JOIN workspace_chat_rules r
                  ON r.user_id=c.user_id AND r.conversation_id=c.id
                LEFT JOIN workspace_exclusions x
                  ON x.user_id=c.user_id AND x.conversation_id=c.id
                WHERE {' AND '.join(filters)}
                  AND NOT EXISTS (SELECT 1 FROM phone_conversation_aliases a WHERE a.user_id=c.user_id AND a.old_id=c.id)
                ORDER BY c.updated_at DESC,c.id LIMIT ? OFFSET ?""",
                (*values, limit, offset)).fetchall()
        defaults = self.workspace_policy(user_id=user)["defaults"]
        result = []
        for row in rows:
            chat = dict(row)
            chat["allowed"] = chat["action"] == "allow" or (
                chat["action"] == "inherit" and defaults[chat["conversation_kind"]])
            chat["account_ai_disabled"] = self.account_ai_disabled(str(chat["account_ref"]), user_id=user)
            chat["allowed"] = bool(chat["allowed"] and not chat["account_ai_disabled"])
            chat["effective_action"] = "allow" if chat["allowed"] else "ignore"
            result.append(chat)
        return result

    def evaluate_workspace_chat(
        self, *, conversation_id: str, user_id: str | None = None,
    ) -> dict[str, object]:
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user_id)
        user = self._user(user_id)
        conversation_id = self._workspace_conversation_id(conversation_id)
        row = next((r for r in self.workspace_chat_rules(user_id=user, conversation_id=conversation_id)
                    if r["conversation_id"] == conversation_id), None)
        if row is None:
            raise KeyError("conversation not found")
        defaults = self.workspace_policy(user_id=user)
        action = row["action"]
        allowed = action == "allow" or (action == "inherit" and
            defaults["defaults"][row["conversation_kind"]])
        # Legacy exclusions remain hard denies even if a new rule was added.
        with self._lock:
            excluded = self._connection.execute("""SELECT 1 FROM workspace_exclusions
                WHERE user_id=? AND conversation_id=?""", (user, conversation_id)).fetchone() is not None
        account_disabled = self.account_ai_disabled(str(row["account_ref"]), user_id=user)
        return {**row, "allowed": bool(allowed and not excluded and not account_disabled),
                "account_ai_disabled": account_disabled,
                "policy_revision": defaults["revision"]}

    def evaluate_workspace_event(
        self, *, conversation_id: str, source: str, message_id: str,
        user_id: str | None = None,
    ) -> dict[str, object]:
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user_id)
        user = self._user(user_id)
        chat = self.evaluate_workspace_chat(conversation_id=conversation_id, user_id=user)
        with self._lock:
            row = self._connection.execute("""SELECT e.eligible FROM workspace_events e JOIN messages m
                ON m.user_id=e.user_id AND m.source=e.source AND m.message_id=e.message_id
                WHERE e.user_id=? AND e.conversation_id=? AND e.source=? AND e.message_id=?
                AND m.direction='incoming' AND m.deleted_at IS NULL""",
                (user, conversation_id, source, message_id)).fetchone()
        eligible = row is not None and bool(row["eligible"])
        return {**chat, "eligible_at_ingest": eligible,
                "allowed": bool(chat["allowed"] and eligible)}

    def set_workspace_chat_rule(
        self, *, conversation_id: str, action: str, reason: str = "",
        user_id: str | None = None,
    ) -> dict[str, object]:
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user_id)
        user = self._user(user_id)
        conversation_id = self._workspace_conversation_id(conversation_id)
        if action not in {"ignore", "allow", "inherit"}:
            raise ValueError("action must be ignore, allow or inherit")
        if not isinstance(reason, str) or len(reason) > 500:
            raise ValueError("reason must be at most 500 characters")
        with self._lock, self._connection:
            if self._connection.execute("SELECT 1 FROM conversations WHERE user_id=? AND id=?",
                    (user, conversation_id)).fetchone() is None:
                raise KeyError("conversation not found")
            if action == "inherit":
                self._connection.execute("DELETE FROM workspace_chat_rules WHERE user_id=? AND conversation_id=?",
                    (user, conversation_id))
                self._connection.execute("DELETE FROM workspace_exclusions WHERE user_id=? AND conversation_id=?",
                    (user, conversation_id))
            else:
                now = time.time()
                self._connection.execute("""INSERT INTO workspace_chat_rules
                    (user_id,conversation_id,action,reason,created_at,updated_at)
                    VALUES (?,?,?,?,?,?) ON CONFLICT(user_id,conversation_id) DO UPDATE SET
                    action=excluded.action,reason=excluded.reason,updated_at=excluded.updated_at""",
                    (user, conversation_id, action, reason.strip(), now, now))
                if action == "allow":
                    self._connection.execute("DELETE FROM workspace_exclusions WHERE user_id=? AND conversation_id=?",
                        (user, conversation_id))
                else:
                    source = self._connection.execute("SELECT source FROM conversations WHERE user_id=? AND id=?",
                        (user, conversation_id)).fetchone()["source"]
                    self._connection.execute("""INSERT INTO workspace_exclusions
                        (user_id,conversation_id,source,reason,created_at,updated_at)
                        VALUES (?,?,?,?,?,?) ON CONFLICT(user_id,conversation_id) DO UPDATE SET
                        reason=excluded.reason,updated_at=excluded.updated_at""",
                        (user, conversation_id, source, reason.strip(), now, now))
            if action == "ignore":
                self._invalidate_workspace_events_for_chat(user, conversation_id)
            elif action == "inherit":
                kind = str(self._connection.execute(
                    "SELECT conversation_kind FROM conversations WHERE user_id=? AND id=?",
                    (user, conversation_id),
                ).fetchone()["conversation_kind"])
                if not bool(self.workspace_policy(user_id=user)["defaults"][kind]):
                    self._invalidate_workspace_events_for_chat(user, conversation_id)
            self._bump_workspace_policy(user)
        return self.evaluate_workspace_chat(conversation_id=conversation_id, user_id=user)

    def ingest(
        self, message: InboxMessage, *, conversation_id: str, policy: ConversationPolicy,
        user_id: str | None = None, conversation_key: str | None = None,
        account_ref: str = "",
    ) -> bool:
        user_id, now = self._user(user_id), time.time()
        message_kind = self._message_kind(message)
        with self._lock, self._connection:
            conversation_inserted = self._connection.execute(
                """
                INSERT OR IGNORE INTO conversations
                (user_id,id,conversation_key,route_id,source,sender,identity_id,response_mode,updated_at,
                 conversation_kind,peer_id,account_ref)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    user_id, conversation_id, conversation_key or message.conversation_key, policy.route_id,
                    message.source, message.sender, policy.identity_id, policy.mode, now,
                    message_kind, message.peer_id or (
                        message.message_id.split(":", 1)[0] if message.source == "telegram" else ""),
                    account_ref,
                ),
            ).rowcount == 1
            if message_kind != "unknown":
                if message.source == 'yandex' and account_ref.startswith('yandex:'):
                    self._connection.execute("""UPDATE conversations SET conversation_kind=?
                        WHERE user_id=? AND id=? AND source='yandex' AND account_ref=?""",
                        (message_kind,user_id,conversation_id,account_ref))
                if message_kind == "telegram_bot":
                    changed_to_bot = self._connection.execute("""UPDATE conversations
                        SET conversation_kind='telegram_bot'
                        WHERE user_id=? AND id=? AND conversation_kind!='telegram_bot'""",
                        (user_id, conversation_id)).rowcount == 1
                    if changed_to_bot:
                        self._invalidate_workspace_events_for_chat(user_id, conversation_id)
                else:
                    self._connection.execute("""UPDATE conversations SET conversation_kind=?
                        WHERE user_id=? AND id=? AND conversation_kind='unknown'""",
                        (message_kind, user_id, conversation_id))
            # Old Telegram rows did not always record the account. Never let a
            # previously ignored peer become allowed merely because the first
            # account-aware arrival receives a new conversation identity. Copy
            # only a deny, only when creating that account-scoped chat; an
            # explicit later allow on the new chat remains authoritative.
            if (conversation_inserted and message.source == "telegram" and account_ref
                    and message.peer_id):
                legacy_deny = self._connection.execute("""SELECT
                        COALESCE(x.reason,r.reason,'legacy Telegram ignore') AS reason
                    FROM conversations c
                    LEFT JOIN workspace_exclusions x
                      ON x.user_id=c.user_id AND x.conversation_id=c.id
                    LEFT JOIN workspace_chat_rules r
                      ON r.user_id=c.user_id AND r.conversation_id=c.id
                    WHERE c.user_id=? AND c.source='telegram' AND c.account_ref=''
                      AND c.peer_id=? AND c.id!=?
                      AND (x.conversation_id IS NOT NULL OR r.action='ignore')
                    ORDER BY c.updated_at DESC LIMIT 1""",
                    (user_id, message.peer_id, conversation_id)).fetchone()
                if legacy_deny is not None:
                    reason = str(legacy_deny["reason"] or "legacy Telegram ignore")
                    self._connection.execute("""INSERT OR IGNORE INTO workspace_exclusions
                        (user_id,conversation_id,source,reason,created_at,updated_at)
                        VALUES (?,?,'telegram',?,?,?)""",
                        (user_id, conversation_id, reason, now, now))
                    self._connection.execute("""INSERT OR IGNORE INTO workspace_chat_rules
                        (user_id,conversation_id,action,reason,created_at,updated_at)
                        VALUES (?,?,'ignore',?,?,?)""",
                        (user_id, conversation_id, reason, now, now))
            # Replays hydrate the existing legacy identity in this exact chat;
            # no ID/history/receipt is renamed and no arrival is invented.
            if message.source == "telegram" and account_ref and message.message_id.startswith(f"{account_ref}|"):
                legacy_id = message.message_id[len(account_ref) + 1:]
                legacy = self._connection.execute("""SELECT 1 FROM messages
                    WHERE user_id=? AND source='telegram' AND message_id=? AND conversation_id=?""",
                    (user_id, legacy_id, conversation_id)).fetchone()
                if legacy is not None:
                    from dataclasses import replace
                    message = replace(message, message_id=legacy_id)
            if message.source == "telegram" and account_ref and message.peer_id:
                watermark = self._connection.execute("""SELECT inbox_max,outbox_max FROM telegram_read_state
                    WHERE user_id=? AND account_ref=? AND peer_id=?""",
                    (user_id,account_ref,message.peer_id)).fetchone()
                native_id = message.message_id.rsplit(":",1)[-1]
                if watermark is not None and native_id.isdigit() and int(native_id) <= int(
                        watermark["outbox_max" if message.direction == "outgoing" else "inbox_max"]):
                    from dataclasses import replace
                    message = replace(message,provider_read=True)
            previous = self._connection.execute("""SELECT body,direction,received_at,edited_at,provider_read
                FROM messages WHERE user_id=? AND source=? AND message_id=? AND conversation_id=?""",
                (user_id, message.source, message.message_id, conversation_id)).fetchone()
            if previous is not None:
                if (previous["direction"] != message.direction or previous["received_at"] != message.received_at
                        or previous["edited_at"] != (message.edited_at or None)
                        or (message.source=='sms' and message.reconciliation and previous['body']!=message.body)):
                    self._invalidate_message_summary(user_id, message.source, message.message_id)
                self._connection.execute("""UPDATE messages SET direction=?,received_at=?,
                    provider_read=CASE WHEN provider_read=1 THEN 1 ELSE COALESCE(?,provider_read) END,sender_is_bot=?,edited_at=COALESCE(?,edited_at),
                    provider_reply_to=CASE WHEN provider_reply_to='' THEN ? ELSE provider_reply_to END,
                    provider_thread_id=CASE WHEN provider_thread_id='' THEN ? ELSE provider_thread_id END
                    WHERE user_id=? AND source=? AND message_id=? AND conversation_id=?""",
                    (message.direction, message.received_at, message.provider_read, int(message.sender_is_bot),
                     message.edited_at or None, message.provider_reply_to, message.provider_thread_id,
                     user_id, message.source, message.message_id, conversation_id))
                if message.source=='sms' and message.reconciliation and message.message_id.startswith('sms-native:'):
                    self._connection.execute("UPDATE messages SET body=? WHERE user_id=? AND source='sms' AND message_id=? AND conversation_id=?", (message.body,user_id,message.message_id,conversation_id))
                if message.direction != "incoming" or (message.sender_is_bot
                        and not self.workspace_policy(user_id=user_id)["defaults"]["telegram_bot"]):
                    self._connection.execute("""UPDATE workspace_events SET eligible=0
                        WHERE user_id=? AND source=? AND message_id=?""",
                        (user_id, message.source, message.message_id))
            inserted = self._connection.execute(
                """
                INSERT OR IGNORE INTO messages
                (user_id,source,message_id,conversation_id,sender,body,direction,received_at,sender_is_bot,edited_at,provider_read,outbox_receipt,provider_reply_to,provider_thread_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    user_id, message.source, message.message_id, conversation_id,
                    message.sender, message.body, message.direction, message.received_at,
                    int(message.sender_is_bot), message.edited_at or None, message.provider_read, message.outbox_receipt,
                    message.provider_reply_to, message.provider_thread_id,
                ),
            ).rowcount == 1
            if message.source=='sms' and message.provider_read is True:
                self._connection.execute("UPDATE messages SET seen_at=COALESCE(seen_at,?) WHERE user_id=? AND source='sms' AND message_id=?", (message.received_at,user_id,message.message_id))
            if message.source=='sms' and message.outbox_receipt:
                self._connection.execute("UPDATE messages SET outbox_receipt=? WHERE user_id=? AND source='sms' AND message_id=? AND direction='outgoing' AND sender=? AND body=?", (message.outbox_receipt,user_id,message.message_id,message.sender,message.body))
            if inserted:
                # A late history item can precede the cached temporal frontier
                # even though its durable rowid is new. Rebuild that cache.
                self._connection.execute("""DELETE FROM conversation_summaries
                    WHERE user_id=? AND conversation_id=? AND EXISTS (
                        SELECT 1 FROM messages m WHERE m.user_id=? AND m.conversation_id=?
                        AND m.rowid<=conversation_summaries.through_message_rowid AND m.received_at>=?)""",
                    (user_id, conversation_id, user_id, conversation_id, message.received_at))
            if inserted and message.direction == "incoming":
                bot_enabled = bool(self.workspace_policy(user_id=user_id)["defaults"]["telegram_bot"])
                eligible = ((not message.reconciliation)
                    and (not message.sender_is_bot or bot_enabled)
                    and self.evaluate_workspace_chat(
                        conversation_id=conversation_id, user_id=user_id)["allowed"])
                self._connection.execute(
                    """
                    INSERT INTO workspace_events(user_id,source,message_id,conversation_id,eligible,reconciled,queued_at)
                    VALUES (?,?,?,?,?,?,?)
                    """,
                    (user_id, message.source, message.message_id, conversation_id,
                     int(eligible), int(message.reconciliation), time.time()),
                )
            elif not inserted and message.direction == "incoming" and not message.reconciliation:
                # The live listener can race startup reconciliation. Only a
                # record created by reconciliation may become a live event here.
                pending = self._connection.execute("""SELECT reconciled FROM workspace_events
                    WHERE user_id=? AND source=? AND message_id=? AND conversation_id=?""",
                    (user_id, message.source, message.message_id, conversation_id)).fetchone()
                if pending is not None and pending["reconciled"]:
                    bot_enabled = bool(self.workspace_policy(user_id=user_id)["defaults"]["telegram_bot"])
                    eligible = ((not message.sender_is_bot or bot_enabled)
                        and self.evaluate_workspace_chat(
                            conversation_id=conversation_id, user_id=user_id)["allowed"])
                    self._connection.execute("""UPDATE workspace_events
                        SET eligible=?,reconciled=0 WHERE user_id=? AND source=?
                          AND message_id=? AND conversation_id=? AND reconciled=1""",
                        (int(eligible), user_id, message.source, message.message_id, conversation_id))
            if message.peer_profile:
                from .search import save_profile
                save_profile(self, user_id, conversation_id, message.peer_profile)
            if getattr(message, "sender_name", ""):
                self._connection.execute(
                    """
                    INSERT INTO contact_names
                    (user_id,source,sender,name,updated_at,is_explicit) VALUES (?,?,?,?,?,0)
                    ON CONFLICT(user_id,source,sender) DO UPDATE SET
                      name=excluded.name,updated_at=excluded.updated_at
                    WHERE contact_names.is_explicit=0
                    """,
                    (user_id, message.source, message.sender, message.sender_name, now),
                )
            if message.source in {"max", "phone", "vk", "yandex"} and message.conversation_title and account_ref and message.peer_id:
                self._connection.execute(
                    "INSERT OR REPLACE INTO contact_names (user_id,source,sender,name,updated_at) VALUES (?,?,?,?,?)",
                    (user_id, message.source, f"chat:{account_ref}:{message.peer_id}", message.conversation_title, now),
                )
            if message.source == "max" and (inserted or previous is not None):
                # MAX projects the complete provider attachment snapshot. An
                # edited URL can replace or remove SHARE; upsert-only retained
                # the old preview after the native message no longer had it.
                self._connection.execute(
                    "DELETE FROM message_attachments WHERE user_id=? AND source='max' AND message_id=?",
                    (user_id, message.message_id),
                )
            for att in (message.attachments or ()):
                if (message.reconciliation and att.get("kind") in {"voice", "audio"}
                        and att.get("transcription_status") == "not_requested"):
                    # A metadata-only history fetch must not erase an existing
                    # transcript or replace the enriched body with its caption.
                    prior = self._connection.execute("""SELECT transcript,transcription_status,transcription_model
                        FROM message_attachments WHERE user_id=? AND source=? AND message_id=?
                        AND kind=? AND provider_ref=? AND COALESCE(transcript,'')!='' LIMIT 1""",
                        (user_id, message.source, message.message_id, att.get("kind"),
                         str(att.get("provider_ref") or ""))).fetchone()
                    if prior is not None:
                        from dataclasses import replace
                        att = {**att, **dict(prior)}
                        transcript = str(prior["transcript"])
                        caption = message.body
                        if caption.startswith("[Telegram ") and caption.endswith("]"):
                            caption = ""
                        enriched = caption if caption.endswith(transcript) else (
                            caption + "\n\n" + transcript if caption else transcript)
                        message = replace(message, body=enriched)
                self.upsert_attachment(
                    source=message.source, message_id=message.message_id, attachment=att,
                    user_id=user_id,
                )
            # A title is presentation metadata, never policy identity. Keep the
            # latest display label while the account+peer conversation key and
            # its rules remain stable across Telegram renames.
            self._connection.execute(
                "UPDATE conversations SET sender=? WHERE user_id=? AND id=?",
                (message.sender, user_id, conversation_id),
            )
            if inserted:
                self._connection.execute(
                    "UPDATE conversations SET updated_at=? WHERE user_id=? AND id=?",
                    (now, user_id, conversation_id),
                )
            else:
                # The same (user_id, source, message_id) arrived again. That is
                # either an unchanged replay or a provider-side edit (Telegram
                # bot status texts, Matrix m.replace, transcript enrichment).
                # A genuine edit must overwrite the stale mirror body without
                # becoming a new arrival: no workspace event, no triage.
                existing = self._connection.execute(
                    "SELECT body FROM messages WHERE user_id=? AND source=? AND message_id=?",
                    (user_id, message.source, message.message_id),
                ).fetchone()
                old_body = str(existing["body"] or "") if existing else ""
                new_body = str(message.body or "")
                if message.source not in {"telegram", "sms"}:
                    new_body = new_body.strip()
                old_placeholder = (not old_body.strip()) or (
                    old_body.strip().startswith("[") and old_body.strip().endswith("]")
                )
                new_is_text = bool(new_body) and not (new_body.startswith("[") and new_body.endswith("]"))
                if old_placeholder and new_is_text:
                    self._invalidate_message_summary(user_id, message.source, message.message_id)
                    self._connection.execute(
                        "UPDATE messages SET body=? WHERE user_id=? AND source=? AND message_id=?",
                        (new_body, user_id, message.source, message.message_id),
                    )
                    self._connection.execute(
                        "UPDATE conversations SET updated_at=? WHERE user_id=? AND id=?",
                        (now, user_id, conversation_id),
                    )
                elif new_is_text and new_body != old_body:
                    self._invalidate_message_summary(user_id, message.source, message.message_id)
                    edited_at = ((float(message.edited_at) if message.edited_at else None)
                        if message.source == 'yandex' else float(message.edited_at or 0.0) or now)
                    self._connection.execute(
                        """UPDATE messages SET body=?,edited_at=?
                           WHERE user_id=? AND source=? AND message_id=?""",
                        (new_body, edited_at, user_id, message.source, message.message_id),
                    )
                    self._connection.execute(
                        "UPDATE conversations SET updated_at=? WHERE user_id=? AND id=?",
                        (now, user_id, conversation_id),
                    )
        return inserted

    def telegram_mirror_state(
        self, *, account_ref: str, peer_id: str, user_id: str,
        read_inbox_max_id: int | None = None, read_outbox_max_id: int | None = None,
        unread_count: int | None = None, unread_observed_at: float | None = None,
        deleted_ids: list[int] | None = None, history_ids: list[int] | None = None,
        scan_before_id: int | None = None, scan_oldest_id: int | None = None,
        scan_has_more: bool | None = None, scan_fetched: int | None = None,
    ) -> dict[str, object]:
        """Apply native read/deletion facts; absence in a short history is only a probe candidate.

        Account ownership is checked at the HTTP boundary. Messages, event IDs,
        drafts and receipts are retained when a provider confirms deletion.
        """
        if not account_ref.startswith("telegram:") or not peer_id.lstrip("-").isdigit():
            raise ValueError("exact Telegram account and peer required")
        for value in (read_inbox_max_id, read_outbox_max_id):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("read watermark must be a nonnegative integer")
        if unread_count is not None and (type(unread_count) is not int or not 0 <= unread_count <= 2_147_483_647):
            raise ValueError("native unread count must be a nonnegative integer")
        if unread_observed_at is not None and (
            type(unread_observed_at) not in (int, float) or
            not 0 < float(unread_observed_at) < 10**11
        ):
            raise ValueError("unread observation timestamp is invalid")
        if unread_count is not None and unread_observed_at is None:
            unread_observed_at = time.time()
        for values in (deleted_ids, history_ids):
            if values is not None and (not isinstance(values, list) or len(values) > 200
                    or any(type(v) is not int or v <= 0 for v in values)):
                raise ValueError("provider IDs must contain at most 200 positive integers")
        if scan_before_id is not None:
            if (type(scan_before_id) is not int or not 0 <= scan_before_id <= 2_147_483_647
                    or type(scan_oldest_id) is not int or not 0 <= scan_oldest_id <= 2_147_483_647
                    or type(scan_has_more) is not bool or type(scan_fetched) is not int
                    or not 0 <= scan_fetched <= 200
                    or (scan_fetched == 0 and (scan_oldest_id != 0 or scan_has_more))
                    or (scan_fetched > 0 and scan_oldest_id == 0)
                    or (scan_before_id > 0 and scan_oldest_id >= scan_before_id)):
                raise ValueError("invalid Telegram history scan page")
        elif any(x is not None for x in (scan_oldest_id, scan_has_more, scan_fetched)):
            raise ValueError("history scan metadata requires scan_before_id")
        changed = 0
        missing: list[int] = []
        history_status: dict[str, object] | None = None
        with self._lock, self._connection:
            self._connection.execute("""INSERT INTO telegram_read_state
                (user_id,account_ref,peer_id,inbox_max,outbox_max,unread_count,unread_observed_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(user_id,account_ref,peer_id) DO UPDATE SET
                  inbox_max=MAX(inbox_max,excluded.inbox_max),
                  outbox_max=MAX(outbox_max,excluded.outbox_max),
                  unread_count=CASE
                    WHEN excluded.unread_count IS NOT NULL AND
                      (telegram_read_state.unread_observed_at IS NULL OR
                       excluded.unread_observed_at >= telegram_read_state.unread_observed_at)
                    THEN excluded.unread_count ELSE telegram_read_state.unread_count END,
                  unread_observed_at=CASE
                    WHEN excluded.unread_count IS NOT NULL AND
                      (telegram_read_state.unread_observed_at IS NULL OR
                       excluded.unread_observed_at >= telegram_read_state.unread_observed_at)
                    THEN excluded.unread_observed_at ELSE telegram_read_state.unread_observed_at END""",
                (user_id,account_ref,peer_id,read_inbox_max_id or 0,read_outbox_max_id or 0,
                 unread_count, unread_observed_at))
            conversation = self._connection.execute("""SELECT id FROM conversations
                WHERE user_id=? AND source='telegram' AND account_ref=? AND peer_id=?""",
                (user_id, account_ref, peer_id)).fetchone()
            if conversation is None:
                return {"changed": 0, "missing_ids": []}
            cid = str(conversation["id"])
            # Both historic and account-scoped identities remain unchanged.
            locator = "CAST(substr(message_id,length(rtrim(message_id,'0123456789'))+1) AS INTEGER)"
            scope = "user_id=? AND conversation_id=? AND source='telegram' AND (message_id LIKE ? OR message_id LIKE ?)"
            params = (user_id, cid, f"{peer_id}:%", f"{account_ref}|{peer_id}:%")
            for direction, watermark in (("incoming", read_inbox_max_id), ("outgoing", read_outbox_max_id)):
                if watermark is not None:
                    changed += self._connection.execute(f"""UPDATE messages SET provider_read=1
                        WHERE {scope} AND direction=? AND {locator}<=? AND {locator}>0
                        AND COALESCE(provider_read,0)=0""", (*params, direction, watermark)).rowcount
            if deleted_ids:
                placeholders = ','.join('?' for _ in deleted_ids)
                changed += self._connection.execute(f"""UPDATE messages SET deleted_at=?
                    WHERE {scope} AND {locator} IN ({placeholders}) AND deleted_at IS NULL""",
                    (time.time(), *params, *deleted_ids)).rowcount
                self._connection.execute("""UPDATE workspace_events SET eligible=0 WHERE user_id=?
                    AND conversation_id=? AND message_id IN (SELECT message_id FROM messages
                    WHERE user_id=? AND conversation_id=? AND deleted_at IS NOT NULL)""", (user_id,cid,user_id,cid))
                self._connection.execute("DELETE FROM conversation_summaries WHERE user_id=? AND conversation_id=?", (user_id,cid))
            if history_ids:
                rows = self._connection.execute(f"""SELECT {locator} AS provider_id FROM messages
                    WHERE {scope} AND deleted_at IS NULL AND {locator} BETWEEN ? AND ? LIMIT 200""",
                    (*params, min(history_ids), max(history_ids))).fetchall()
                known = set(history_ids)
                missing = [int(r["provider_id"]) for r in rows if int(r["provider_id"]) not in known]
            if scan_before_id is not None:
                existing_history = self._connection.execute("""SELECT oldest_id,complete,fetched_count
                    FROM telegram_history_state WHERE user_id=? AND account_ref=? AND peer_id=?""",
                    (user_id,account_ref,peer_id)).fetchone()
                sequential = (
                    existing_history is None and scan_before_id == 0 or
                    existing_history is not None and not existing_history["complete"] and
                    (scan_before_id == int(existing_history["oldest_id"]) or
                     scan_before_id == 0 and int(existing_history["fetched_count"]) == 0)
                )
                if sequential:
                    previous_count = int(existing_history["fetched_count"]) if existing_history else 0
                    next_oldest = scan_oldest_id or (
                        int(existing_history["oldest_id"]) if existing_history else 0)
                    self._connection.execute("""INSERT INTO telegram_history_state
                        (user_id,account_ref,peer_id,oldest_id,complete,fetched_count,updated_at)
                        VALUES (?,?,?,?,?,?,?)
                        ON CONFLICT(user_id,account_ref,peer_id) DO UPDATE SET
                        oldest_id=excluded.oldest_id, complete=excluded.complete,
                        fetched_count=excluded.fetched_count, updated_at=excluded.updated_at""",
                        (user_id,account_ref,peer_id,next_oldest,int(not scan_has_more),
                         previous_count + scan_fetched,time.time()))
                status = self._connection.execute("""SELECT oldest_id,complete,fetched_count,updated_at
                    FROM telegram_history_state WHERE user_id=? AND account_ref=? AND peer_id=?""",
                    (user_id,account_ref,peer_id)).fetchone()
                if status is not None:
                    history_status = {
                        "oldest_id":int(status["oldest_id"]), "complete":bool(status["complete"]),
                        "fetched_count":int(status["fetched_count"]),
                        "updated_at":float(status["updated_at"]),
                        "scan_advanced":bool(sequential),
                    }
        return {"changed": changed, "missing_ids": missing,
                **({"history": history_status} if history_status is not None else {})}

    def telegram_unread_snapshot(
        self, *, account_ref: str, dialogs: list[dict[str, object]],
        observed_at: float, user_id: str,
    ) -> dict[str, object]:
        """Refresh provider unread totals without fetching any message bodies.

        Validate the entire account-scoped batch before applying any changes.
        """
        if (not account_ref.startswith("telegram:") or not isinstance(dialogs, list)
                or len(dialogs) > 200 or type(observed_at) not in (int, float)
                or not 0 < float(observed_at) < 10**11):
            raise ValueError("invalid Telegram unread snapshot")
        for d in dialogs:
            if not isinstance(d, dict) or not str(d.get("peer_id", "")).lstrip("-").isdigit():
                raise ValueError("invalid Telegram unread peer")
            value = d.get("unread_count")
            if type(value) is not int or not 0 <= value <= 2_147_483_647:
                raise ValueError("invalid Telegram unread count")
            for key in ("read_inbox_max_id", "read_outbox_max_id"):
                mark = d.get(key)
                if mark is not None and (type(mark) is not int or mark < 0):
                    raise ValueError("invalid Telegram read watermark")
        for d in dialogs:
            self.telegram_mirror_state(
                account_ref=account_ref, peer_id=str(d["peer_id"]), user_id=user_id,
                unread_count=int(d["unread_count"]), unread_observed_at=float(observed_at),
                read_inbox_max_id=d.get("read_inbox_max_id"),
                read_outbox_max_id=d.get("read_outbox_max_id"),
            )
        return {"count": len(dialogs), "observed_at": float(observed_at)}

    def create_conversation(
        self, source: str, sender: str, *, user_id: str | None = None,
        account_ref: str = "", peer_id: str = "", conversation_kind: str = "unknown",
        route_id: str | None = None,
    ) -> dict[str, object]:
        """Create (or return the existing) empty conversation for source+sender.

        Lets the dashboard start a chat — e.g. a new SMS thread — before any
        inbound message exists.
        """
        user_id = self._user(user_id)
        source, sender = source.strip(), sender.strip()
        if not source or not sender:
            raise ValueError("source and sender are required")
        message_key = (f"telegram:{account_ref}:{peer_id}" if source == "telegram" and account_ref and peer_id
                       else "email:" + sender.casefold()
                       if source in {"mail", "email", "gmail"} or source.startswith("gmail:")
                       else f"{source}:{sender}")
        conversation_id = self.conversation_id_for_key(message_key, user_id=user_id)
        if conversation_id is None:
            conversation_id = "conv_" + hashlib.sha256(f"{user_id}\0{message_key}".encode()).hexdigest()[:24]
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO conversations
                (user_id,id,conversation_key,route_id,source,sender,identity_id,response_mode,updated_at)
                VALUES (?,?,?,?,?,?,NULL,'approve',?)
                """,
                (user_id, conversation_id, message_key, route_id or source, source, sender, time.time()),
            )
            if source == "telegram" and account_ref and peer_id:
                self._connection.execute(
                    "UPDATE conversations SET account_ref=?,peer_id=?,conversation_kind=?,route_id=? "
                    "WHERE user_id=? AND id=?",
                    (account_ref,peer_id,conversation_kind,route_id or source,user_id,conversation_id),
                )
        record = self.conversation(conversation_id, user_id=user_id)
        assert record is not None
        return record

    def create_task_contact_conversation(
        self, *, user_id: str, qualification: dict[str, object],
        route_id: str, display_name: str = "", authority_lease: object,
        authority_binding: dict[str, object],
    ) -> dict[str, object]:
        """Persist one catalog-qualified provider identity without sending.

        The caller must obtain ``qualification`` from the configured trusted
        catalog resolver and provider lookup. This store boundary rechecks the
        current account and atomically creates/reuses the canonical conversation
        while recording the immutable provenance binding.
        """
        user_id = self._user(user_id)
        required = (
            "id", "task_id", "actor_id", "project_id", "catalog_namespace",
            "catalog_record_id", "catalog_contact_id", "catalog_revision",
            "contact_version", "provider", "account_ref", "peer_id", "peer_kind",
            "endpoint_id", "binding_hash", "expires_at",
        )
        if not isinstance(qualification, dict) or any(
            not isinstance(qualification.get(key), str) or not str(qualification[key]).strip()
            for key in required if key not in {"expires_at"}
        ):
            raise ValueError("invalid_task_contact_qualification")
        if (isinstance(qualification.get("expires_at"), bool)
                or not isinstance(qualification.get("expires_at"), (int, float))):
            raise ValueError("invalid_task_contact_qualification_expiry")
        if (qualification["peer_kind"] != "direct"
                or not re.fullmatch(r"[0-9a-f]{64}", str(qualification["binding_hash"]))):
            raise ValueError("invalid_task_contact_identity")
        provider = str(qualification["provider"]).strip().lower()
        account_ref = str(qualification["account_ref"]).strip()
        peer_id = str(qualification["peer_id"]).strip()
        if float(qualification["expires_at"]) <= time.time():
            raise ValueError("task_contact_qualification_expired")
        if not route_id.strip():
            raise ValueError("qualified_contact_route_required")
        if route_id != str(qualification["endpoint_id"]):
            raise ValueError("qualified_contact_endpoint_conflict")
        assert_current = getattr(authority_lease, "assert_current", None)
        if not callable(assert_current) or not isinstance(authority_binding, dict):
            raise ValueError("catalog_authority_lease_required")
        key = f"{provider}:{account_ref}:{peer_id}"
        conversation_id = "conv_" + hashlib.sha256(
            f"{user_id}\0{key}".encode("utf-8")
        ).hexdigest()[:24]
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            account = self._connection.execute(
                "SELECT provider,enabled,can_read,can_reply FROM provider_accounts "
                "WHERE user_id=? AND id=?",
                (user_id, account_ref),
            ).fetchone()
            if (account is None or not bool(account["enabled"])
                    or not bool(account["can_read"]) or not bool(account["can_reply"])
                    or str(account["provider"]).lower() != provider):
                raise ValueError("qualified_contact_account_not_send_capable")
            assert_current(authority_binding)
            now = time.time()
            if float(qualification["expires_at"]) <= now:
                raise ValueError("task_contact_qualification_expired")

            matches = self._connection.execute(
                "SELECT id,conversation_key,route_id FROM conversations "
                "WHERE user_id=? AND source=? AND account_ref=? AND peer_id=?",
                (user_id, provider, account_ref, peer_id),
            ).fetchall()
            if len(matches) > 1:
                raise ValueError("qualified_contact_conversation_ambiguous")
            if matches:
                row = matches[0]
                conversation_id = str(row["id"])
                if str(row["route_id"]) != route_id:
                    raise ValueError("qualified_contact_route_conflict")
            else:
                key_row = self._connection.execute(
                    "SELECT id,source,account_ref,peer_id FROM conversations "
                    "WHERE user_id=? AND conversation_key=?",
                    (user_id, key),
                ).fetchone()
                if key_row is not None and (
                    str(key_row["source"]).lower(), str(key_row["account_ref"]),
                    str(key_row["peer_id"]),
                ) != (provider, account_ref, peer_id):
                    raise ValueError("qualified_contact_identity_key_conflict")
                self._connection.execute(
                    "INSERT OR IGNORE INTO conversations "
                    "(user_id,id,conversation_key,route_id,source,sender,identity_id,"
                    "response_mode,updated_at,account_ref,peer_id,conversation_kind) "
                    "VALUES (?,?,?,?,?,?,NULL,'approve',?,?,?,'direct')",
                    (user_id, conversation_id, key, route_id, provider,
                     display_name.strip() or peer_id, now, account_ref, peer_id),
                )
                row = self._connection.execute(
                    "SELECT id,source,account_ref,peer_id,route_id FROM conversations "
                    "WHERE user_id=? AND conversation_key=?",
                    (user_id, key),
                ).fetchone()
                if (row is None or (str(row["source"]).lower(), str(row["account_ref"]),
                                    str(row["peer_id"])) != (provider, account_ref, peer_id)
                        or str(row["route_id"]) != route_id):
                    raise ValueError("qualified_contact_conversation_conflict")
                conversation_id = str(row["id"])

            identity_binding_id = "identity_" + hashlib.sha256(
                "\0".join((user_id, str(qualification["task_id"]),
                           str(qualification["actor_id"]),
                           str(qualification["catalog_namespace"]),
                           str(qualification["catalog_record_id"]),
                           str(qualification["catalog_contact_id"]),
                           account_ref, provider)).encode("utf-8")
            ).hexdigest()[:32]
            identity_hash = hashlib.sha256(json.dumps({
                "user_id": user_id,
                "task_id": str(qualification["task_id"]),
                "actor_id": str(qualification["actor_id"]),
                "project_id": str(qualification["project_id"]),
                "catalog_namespace": str(qualification["catalog_namespace"]),
                "catalog_record_id": str(qualification["catalog_record_id"]),
                "catalog_contact_id": str(qualification["catalog_contact_id"]),
                "provider": provider, "account_ref": account_ref,
                "peer_id": peer_id, "peer_kind": "direct",
                "endpoint_id": route_id, "conversation_id": conversation_id,
            }, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
            self._connection.execute(
                "INSERT OR IGNORE INTO task_contact_identity_bindings "
                "(user_id,id,task_id,actor_id,project_id,catalog_namespace,catalog_record_id,"
                "catalog_contact_id,provider,account_ref,peer_id,peer_kind,conversation_id,"
                "endpoint_id,identity_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (user_id, identity_binding_id, str(qualification["task_id"]),
                 str(qualification["actor_id"]), str(qualification["project_id"]),
                 str(qualification["catalog_namespace"]), str(qualification["catalog_record_id"]),
                 str(qualification["catalog_contact_id"]), provider, account_ref,
                 peer_id, "direct", conversation_id, route_id, identity_hash, now),
            )
            identity_binding = self._connection.execute(
                "SELECT peer_id,conversation_id,endpoint_id,identity_hash FROM task_contact_identity_bindings "
                "WHERE user_id=? AND id=?",
                (user_id, identity_binding_id),
            ).fetchone()
            if (identity_binding is None or str(identity_binding["peer_id"]) != peer_id
                    or str(identity_binding["conversation_id"]) != conversation_id
                    or str(identity_binding["endpoint_id"]) != route_id
                    or str(identity_binding["identity_hash"]) != identity_hash):
                raise ValueError("catalog_contact_provider_identity_changed")

            # The lease and proof are checked again at the durable qualification
            # write. Any earlier conversation/identity inserts roll back with
            # this transaction if the proof expired during the transaction.
            assert_current(authority_binding)
            qualification_now = time.time()
            if float(qualification["expires_at"]) <= qualification_now:
                raise ValueError("task_contact_qualification_expired")
            values = {
                "id": str(qualification["id"]), "task_id": str(qualification["task_id"]),
                "actor_id": str(qualification["actor_id"]),
                "project_id": str(qualification["project_id"]),
                "catalog_namespace": str(qualification["catalog_namespace"]),
                "catalog_record_id": str(qualification["catalog_record_id"]),
                "catalog_contact_id": str(qualification["catalog_contact_id"]),
                "catalog_revision": str(qualification["catalog_revision"]),
                "contact_version": str(qualification["contact_version"]),
                "provider": provider, "account_ref": account_ref, "peer_id": peer_id,
                "peer_kind": "direct", "endpoint_id": route_id,
                "conversation_id": conversation_id,
                "identity_binding_id": identity_binding_id,
                "binding_hash": str(qualification["binding_hash"]),
                "expires_at": float(qualification["expires_at"]), "created_at": qualification_now,
            }
            self._connection.execute(
                "INSERT OR IGNORE INTO task_contact_qualifications "
                "(user_id,id,task_id,actor_id,project_id,catalog_namespace,catalog_record_id,"
                "catalog_contact_id,catalog_revision,contact_version,provider,account_ref,peer_id,"
                "peer_kind,endpoint_id,conversation_id,identity_binding_id,binding_hash,expires_at,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (user_id, values["id"], values["task_id"], values["actor_id"],
                 values["project_id"], values["catalog_namespace"], values["catalog_record_id"],
                 values["catalog_contact_id"], values["catalog_revision"], values["contact_version"],
                 values["provider"], values["account_ref"], values["peer_id"], values["peer_kind"],
                 values["endpoint_id"], values["conversation_id"], values["identity_binding_id"], values["binding_hash"],
                 values["expires_at"], values["created_at"]),
            )
            stored = self._connection.execute(
                "SELECT * FROM task_contact_qualifications WHERE user_id=? AND id=?",
                (user_id, values["id"]),
            ).fetchone()
            if (stored is None or str(stored["binding_hash"]) != values["binding_hash"]
                    or str(stored["conversation_id"]) != conversation_id):
                raise ValueError("qualified_contact_provenance_conflict")
        record = self.conversation(conversation_id, user_id=user_id)
        assert record is not None
        return record

    def task_contact_qualification(
        self, qualification_id: str, *, user_id: str | None = None,
    ) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM task_contact_qualifications WHERE user_id=? AND id=?",
                (self._user(user_id), qualification_id),
            ).fetchone()
        return dict(row) if row is not None else None

    # ---- attachments ---------------------------------------------------------
    # Stored separately from messages: a message may carry multiple attachments
    # and we need to fetch them by (source, message_id, idx) for the VK extension
    # bridge and by attachment_id when relaying the file back to the client.

    def upsert_attachment(
        self, *, source: str, message_id: str, attachment: dict[str, object],
        user_id: str | None = None,
    ) -> None:
        user_id = self._user(user_id)
        idx = int(attachment.get("idx") or 0)
        kind = str(attachment.get("kind") or "doc")
        content_type = str(attachment.get("content_type") or "application/octet-stream")
        filename = str(attachment.get("filename") or f"attachment-{idx}")
        size = attachment.get("size")
        try:
            size_int = int(size) if size is not None else None
        except (TypeError, ValueError):
            size_int = None
        src = attachment.get("src")
        attachment_id = attachment.get("attachment_id")
        provider_ref = attachment.get("provider_ref")
        transcript = attachment.get("transcript")
        transcription_status = attachment.get("transcription_status")
        transcription_model = attachment.get("transcription_model")
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT * FROM message_attachments WHERE user_id=? AND source=? AND message_id=? AND idx=?",
                (user_id, source, message_id, idx),
            ).fetchone()
            values = (kind, content_type, filename, size_int, str(src) if src else None,
                      str(attachment_id) if attachment_id else None,
                      str(provider_ref) if provider_ref else None,
                      str(transcript) if transcript is not None else None,
                      str(transcription_status) if transcription_status is not None else None,
                      str(transcription_model) if transcription_model is not None else None)
            keys = ("kind", "content_type", "filename", "size", "src", "attachment_id",
                    "provider_ref", "transcript", "transcription_status", "transcription_model")
            if existing is None or tuple(existing[key] for key in keys) != values:
                self._invalidate_message_summary(user_id, source, message_id)
            self._connection.execute(
                """
                INSERT OR REPLACE INTO message_attachments
                (user_id,source,message_id,idx,kind,content_type,filename,size,src,attachment_id,provider_ref,
                 transcript,transcription_status,transcription_model)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    user_id, source, message_id, idx, kind, content_type, filename,
                    size_int, str(src) if src else None,
                    str(attachment_id) if attachment_id else None,
                    str(provider_ref) if provider_ref else None,
                    str(transcript) if transcript is not None else None,
                    str(transcription_status) if transcription_status is not None else None,
                    str(transcription_model) if transcription_model is not None else None,
                ),
            )

    def attachments_for_message(
        self, *, source: str, message_id: str, user_id: str | None = None,
    ) -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT idx,kind,content_type,filename,size,src,attachment_id,provider_ref,
                       transcript,transcription_status,transcription_model
                FROM message_attachments
                WHERE user_id=? AND source=? AND message_id=?
                ORDER BY idx
                """,
                (self._user(user_id), source, message_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def attachment_by_id(
        self, attachment_id: str, *, user_id: str | None = None,
    ) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT source,message_id,idx,kind,content_type,filename,size,src,
                       attachment_id,provider_ref,transcript,transcription_status,transcription_model
                FROM message_attachments
                WHERE user_id=? AND attachment_id=? LIMIT 1
                """,
                (self._user(user_id), attachment_id),
            ).fetchone()
        return None if row is None else dict(row)

    def add_draft(self, draft: ReplyDraft, *, user_id: str | None = None) -> None:
        from dataclasses import replace
        draft = replace(draft, conversation_id=self.resolve_conversation_id(draft.conversation_id, user_id=user_id))
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO drafts(user_id,id,conversation_id,body,status,created_at) VALUES (?,?,?,?,?,?)",
                (self._user(user_id), draft.id, draft.conversation_id, draft.body, draft.status, time.time()),
            )

    def reply_context_snapshot(self, conversation_id: str, *, source: str,
                               message_id: str, user_id: str | None = None) -> dict:
        """Local canonical provenance for a draft, separate from relay send authority."""
        user = self._user(user_id)
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user)
        with self._lock:
            anchor = self._connection.execute("""SELECT source,message_id,body,direction,
                received_at,edited_at,deleted_at FROM messages WHERE user_id=?
                AND conversation_id=? AND source=? AND message_id=?""",
                (user, conversation_id, source, message_id)).fetchone()
            latest = self._connection.execute("""SELECT source,message_id,body,direction,
                received_at,edited_at FROM messages WHERE user_id=? AND conversation_id=?
                AND deleted_at IS NULL ORDER BY received_at DESC,rowid DESC LIMIT 1""",
                (user, conversation_id)).fetchone()
        if anchor is None or anchor["deleted_at"] is not None:
            raise KeyError("draft source not found in this conversation")
        fingerprint = hashlib.sha256(json.dumps(
            [dict(anchor), dict(latest) if latest else None], ensure_ascii=False,
            sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        return {"source": source, "message_id": message_id,
                "body": str(anchor["body"])[:1000], "received_at": anchor["received_at"],
                "fingerprint": fingerprint}

    def reply_generation_context(self, conversation_id: str, *, source: str,
                                 message_id: str, user_id: str | None = None) -> tuple[dict, dict]:
        """Read the model's source and its saved version under one store lock."""
        with self._lock:
            snapshot = self.reply_context_snapshot(conversation_id, source=source,
                message_id=message_id, user_id=user_id)
            message = self.message(message_id, source=source, user_id=user_id)
            if message is None or message["conversation_id"] != self.resolve_conversation_id(conversation_id, user_id=user_id):
                raise KeyError("draft source not found in this conversation")
            return snapshot, message

    def bind_draft_context(self, draft_id: str, *, conversation_id: str,
                           snapshot: dict, user_id: str | None = None) -> None:
        user = self._user(user_id)
        with self._lock, self._connection:
            draft = self._connection.execute("SELECT conversation_id FROM drafts WHERE user_id=? AND id=?",
                                             (user, draft_id)).fetchone()
            if draft is None or draft["conversation_id"] != conversation_id:
                raise KeyError("draft not found in this conversation")
            self._connection.execute("INSERT OR IGNORE INTO draft_context_snapshots VALUES (?,?,?,?)",
                (user, draft_id, conversation_id, json.dumps(snapshot, ensure_ascii=False)))

    def add_source_bound_draft(self, draft: ReplyDraft, *, account_ref: str,
                               peer_id: str, user_id: str | None = None,
                               source_message_id: str | None = None) -> None:
        user = self._user(user_id)
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute("""SELECT m.message_id,m.body
                FROM messages m JOIN conversations c ON c.user_id=m.user_id AND c.id=m.conversation_id
                WHERE m.user_id=? AND c.id=? AND c.source='telegram' AND c.account_ref=?
                AND c.peer_id=? AND m.source='telegram' AND m.direction='incoming'
                AND m.deleted_at IS NULL AND (? IS NULL OR m.message_id=?)
                ORDER BY m.received_at DESC,m.rowid DESC LIMIT 1""",
                (user, draft.conversation_id, account_ref, peer_id,
                 source_message_id, source_message_id)).fetchone()
            if row is None:
                raise ValueError("real relay inbound source is required")
            match = re.fullmatch(re.escape(f"{account_ref}|{peer_id}:") + r"([1-9][0-9]*)", str(row["message_id"]))
            if match is None:
                raise ValueError("relay provider origin is not account-bound")
            self._connection.execute("""INSERT INTO drafts
                (user_id,id,conversation_id,body,status,created_at) VALUES (?,?,?,?,?,?)""",
                (user, draft.id, draft.conversation_id, draft.body, draft.status, time.time()))
            self._connection.execute("INSERT INTO draft_reply_origins VALUES (?,?,?,?,?,?,?,?)",
                (user, draft.id, draft.conversation_id, account_ref, peer_id, str(row["message_id"]),
                 match[1], hashlib.sha256(str(row["body"]).encode()).hexdigest()))

    def draft_reply_origin(self, draft_id: str, *, user_id: str) -> dict[str, object]:
        with self._lock:
            row = self._connection.execute("""SELECT o.*,m.body AS current_source_body,
                m.direction,m.deleted_at,c.account_ref AS current_account,c.peer_id AS current_peer
                FROM draft_reply_origins o JOIN messages m ON m.user_id=o.user_id
                AND m.source='telegram' AND m.message_id=o.message_key
                JOIN conversations c ON c.user_id=o.user_id AND c.id=o.conversation_id
                WHERE o.user_id=? AND o.draft_id=?""", (user_id, draft_id)).fetchone()
        if (row is None or row["direction"] != "incoming" or row["deleted_at"] is not None
                or (row["account_ref"], row["peer_id"]) != (row["current_account"], row["current_peer"])
                or hashlib.sha256(str(row["current_source_body"]).encode()).hexdigest() != row["source_body_sha256"]):
            raise ValueError("immutable relay draft source changed or is missing")
        return {key: row[key] for key in ("conversation_id", "account_ref", "peer_id", "provider_message_id")}

    def update_draft(self, draft_id: str, *, body: str, user_id: str | None = None) -> ReplyDraft:
        text, user_id = body.strip(), self._user(user_id)
        if not text:
            raise ValueError("draft body is required")
        self.expire_proposed_drafts(user_id=user_id)
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT id,conversation_id,status FROM drafts WHERE user_id=? AND id=?",
                (user_id, draft_id),
            ).fetchone()
            if row is None:
                raise KeyError("draft not found")
            if row["status"] != "proposed":
                raise ValueError("only proposed drafts can be edited")
            changed = self._connection.execute(
                "UPDATE drafts SET body=? WHERE user_id=? AND id=? AND status='proposed'",
                (text, user_id, draft_id),
            ).rowcount
            if changed != 1:
                raise ValueError("only proposed drafts can be edited")
        return ReplyDraft(str(row["id"]), str(row["conversation_id"]), text, "proposed")

    def delete_draft(self, draft_id: str, *, user_id: str | None = None) -> bool:
        user_id = self._user(user_id)
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT status FROM drafts WHERE user_id=? AND id=?", (user_id, draft_id)
            ).fetchone()
            if row is None:
                return False
            if row["status"] in ("approved", "sending"):
                raise ValueError("approved drafts are immutable receipts")
            changed = self._connection.execute(
                "DELETE FROM drafts WHERE user_id=? AND id=? AND status NOT IN ('approved','sending')",
                (user_id, draft_id),
            ).rowcount
            if changed != 1:
                raise ValueError("draft changed during deletion")
            return True

    def delete_conversation(self, conversation_id: str, *, user_id: str | None = None) -> bool:
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user_id)
        user_id = self._user(user_id)
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            if self._connection.execute(
                "SELECT 1 FROM drafts WHERE user_id=? AND conversation_id=? AND status='sending'",
                (user_id, conversation_id),
            ).fetchone():
                raise ValueError("conversation has a delivery in progress")
            exists = self._connection.execute(
                "SELECT 1 FROM conversations WHERE user_id=? AND id=?", (user_id, conversation_id)
            ).fetchone()
            if exists is None:
                return False
            event_rows = self._connection.execute(
                "SELECT seq FROM workspace_events WHERE user_id=? AND conversation_id=?",
                (user_id, conversation_id),
            ).fetchall()
            event_seqs = [int(row["seq"]) for row in event_rows]
            if event_seqs:
                placeholders = ",".join("?" for _ in event_seqs)
                self._connection.execute(
                    f"DELETE FROM conversation_secretary_jobs WHERE user_id=? AND event_seq IN ({placeholders})",
                    (user_id, *event_seqs),
                )
                self._connection.execute(
                    f"DELETE FROM workspace_triage WHERE user_id=? AND event_seq IN ({placeholders})",
                    (user_id, *event_seqs),
                )
                self._connection.execute(
                    f"DELETE FROM workspace_claim_log WHERE user_id=? AND event_seq IN ({placeholders})",
                    (user_id, *event_seqs),
                )
                self._connection.execute(
                    f"DELETE FROM workspace_claims WHERE user_id=? AND event_seq IN ({placeholders})",
                    (user_id, *event_seqs),
                )
            self._connection.execute(
                "DELETE FROM workspace_events WHERE user_id=? AND conversation_id=?",
                (user_id, conversation_id),
            )
            self._connection.execute(
                "DELETE FROM drafts WHERE user_id=? AND conversation_id=?", (user_id, conversation_id)
            )
            self._connection.execute(
                "DELETE FROM messages WHERE user_id=? AND conversation_id=?", (user_id, conversation_id)
            )
            self._connection.execute(
                "DELETE FROM conversation_summaries WHERE user_id=? AND conversation_id=?",
                (user_id, conversation_id),
            )
            self._connection.execute(
                "DELETE FROM workspace_exclusions WHERE user_id=? AND conversation_id=?",
                (user_id, conversation_id),
            )
            self._connection.execute(
                "DELETE FROM workspace_chat_rules WHERE user_id=? AND conversation_id=?",
                (user_id, conversation_id),
            )
            self._connection.execute(
                "DELETE FROM conversations WHERE user_id=? AND id=?", (user_id, conversation_id)
            )
        return True

    def draft(self, draft_id: str, *, user_id: str | None = None) -> ReplyDraft:
        with self._lock:
            row = self._connection.execute(
                "SELECT id,conversation_id,body,status,outbox_receipt FROM drafts WHERE user_id=? AND id=?",
                (self._user(user_id), draft_id),
            ).fetchone()
        if row is None:
            raise KeyError("draft not found")
        return ReplyDraft(str(row["id"]), str(row["conversation_id"]), str(row["body"]), str(row["status"]), str(row["outbox_receipt"] or ""))

    def claim_draft_send(self, draft_id: str, *, user_id: str,
                         expected_snapshot: dict[str, object] | None = None) -> ReplyDraft:
        # Commit the sending claim before provider IO. Other connections and
        # processes see an immutable draft, not merely an in-process mutex.
        self.expire_proposed_drafts(user_id=user_id)
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            draft = self.draft(draft_id, user_id=user_id)
            if expected_snapshot is not None and expected_snapshot != {
                "expected_text": draft.body, "expected_chat_id": draft.conversation_id,
                "expected_attachments": [],
            }:
                raise ValueError("draft_snapshot_conflict")
            if draft.status == "approved":
                return draft
            if draft.status != "proposed":
                raise ValueError("draft is not approvable")
            self._connection.execute(
                "UPDATE drafts SET status='sending',sending_at=?,delivery_state='claimed' "
                "WHERE user_id=? AND id=?",
                (time.time(), user_id, draft_id),
            )
            return draft

    def release_draft_send(self, draft_id: str, *, user_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE drafts SET status='proposed',sending_at=NULL,delivery_state='not_submitted' "
                "WHERE user_id=? AND id=? AND status='sending'",
                (user_id, draft_id),
            )

    def draft_delivery_state(self, draft_id: str, *, user_id: str | None = None) -> dict[str, object]:
        """Read the durable delivery bookkeeping of one draft."""
        with self._lock:
            row = self._connection.execute(
                "SELECT status,sending_at,delivery_state,outbox_receipt,created_at FROM drafts "
                "WHERE user_id=? AND id=?",
                (self._user(user_id), draft_id),
            ).fetchone()
        if row is None:
            raise KeyError("draft not found")
        return {
            "status": str(row["status"]),
            "sending_at": float(row["sending_at"]) if row["sending_at"] is not None else None,
            "delivery_state": str(row["delivery_state"] or ""),
            "receipt": str(row["outbox_receipt"] or ""),
            "created_at": float(row["created_at"]) if row["created_at"] is not None else None,
        }

    def mark_draft_delivery_state(self, draft_id: str, state: str, *,
                                  user_id: str | None = None) -> None:
        """Record how an in-flight delivery attempt was resolved."""
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE drafts SET delivery_state=? WHERE user_id=? AND id=? AND status='sending'",
                (state, self._user(user_id), draft_id),
            )

    def approve(self, draft_id: str, receipt: str, *, user_id: str | None = None) -> ReplyDraft:
        user_id = self._user(user_id)
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM drafts WHERE user_id=? AND id=?", (user_id, draft_id)
            ).fetchone()
            if row is None:
                raise KeyError("draft not found")
            if row["status"] == "approved":
                return ReplyDraft(
                    row["id"], row["conversation_id"], row["body"], row["status"],
                    str(row["outbox_receipt"] or ""),
                )
            if row["status"] not in ("proposed", "sending"):
                raise ValueError("draft is not approvable")
            self._connection.execute(
                """
                UPDATE drafts SET status='approved',approved_at=?,outbox_receipt=?,
                    sending_at=NULL,delivery_state='delivered'
                WHERE user_id=? AND id=?
                """,
                (time.time(), receipt, user_id, draft_id),
            )
        return ReplyDraft(row["id"], row["conversation_id"], row["body"], "approved", receipt)

    def reject(self, draft_id: str, *, user_id: str | None = None) -> ReplyDraft:
        user_id = self._user(user_id)
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM drafts WHERE user_id=? AND id=?", (user_id, draft_id)
            ).fetchone()
            if row is None:
                raise KeyError("draft not found")
            if row["status"] == "proposed":
                changed = self._connection.execute(
                    "UPDATE drafts SET status='rejected' WHERE user_id=? AND id=? AND status='proposed'",
                    (user_id, draft_id),
                ).rowcount
                if changed != 1:
                    raise ValueError("draft changed during rejection")
                return ReplyDraft(row["id"], row["conversation_id"], row["body"], "rejected")
        return ReplyDraft(row["id"], row["conversation_id"], row["body"], row["status"])

    def mark_drafts_browser_notified(
        self, draft_ids: list[str], *, user_id: str | None = None
    ) -> int:
        """Record that the browser actually displayed these proposed drafts."""
        ids = list(dict.fromkeys(str(x).strip() for x in draft_ids if str(x).strip()))
        if not ids:
            return 0
        user_id = self._user(user_id)
        now = time.time()
        changed = 0
        with self._lock, self._connection:
            for draft_id in ids[:200]:
                changed += self._connection.execute(
                    """UPDATE drafts SET browser_notified_at=?
                       WHERE user_id=? AND id=? AND status='proposed'
                         AND browser_notified_at IS NULL""",
                    (now, user_id, draft_id),
                ).rowcount
        return changed

    def draft_browser_notified(self, draft_id: str, *, user_id: str | None = None) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT browser_notified_at FROM drafts WHERE user_id=? AND id=?",
                (self._user(user_id), str(draft_id)),
            ).fetchone()
        return bool(row is not None and row["browser_notified_at"] is not None)

    def pending_drafts(
        self, *, limit: int = 100, user_id: str | None = None
    ) -> list[dict[str, object]]:
        """List proposed drafts with just enough conversation context for operator notifications."""
        user_id = self._user(user_id)
        self.expire_proposed_drafts(user_id=user_id)
        limit = max(1, min(int(limit), 200))
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT d.id,d.conversation_id,d.body,d.status,d.created_at,
                       c.source,c.sender,c.identity_id,
                       COALESCE((SELECT name FROM contact_names n
                                 WHERE n.user_id=c.user_id AND n.source=c.source AND n.sender=
                                   CASE WHEN c.source IN ('max','phone','vk','yandex') AND c.account_ref!='' AND c.peer_id!='' THEN 'chat:'||c.account_ref||':'||c.peer_id ELSE c.sender END), '') AS display_name
                FROM drafts d JOIN conversations c
                  ON c.user_id=d.user_id AND c.id=d.conversation_id
                WHERE d.user_id=? AND d.status='proposed'
                ORDER BY d.created_at DESC LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def _expire_proposed_drafts_locked(self, *, user_id: str, now: float) -> int:
        """Delete expired proposals while preserving every claimed or sent action."""
        cutoff = now - DRAFT_RETENTION_SECONDS
        rows = self._connection.execute(
            """SELECT d.id FROM drafts d WHERE d.user_id=? AND d.status='proposed'
               AND d.created_at<=? AND NOT EXISTS (
                   SELECT 1 FROM workspace_triage t WHERE t.user_id=d.user_id
                     AND t.selected_draft_id=d.id AND t.send_state IN ('sending','sent'))""",
            (user_id, cutoff),
        ).fetchall()
        expired_ids = [str(row["id"]) for row in rows]
        if not expired_ids:
            return 0
        expired = set(expired_ids)
        triage_rows = self._connection.execute(
            """SELECT event_seq,draft_ids_json,selected_draft_id FROM workspace_triage
               WHERE user_id=? AND send_state='none'""",
            (user_id,),
        ).fetchall()
        for row in triage_rows:
            draft_ids = json.loads(str(row["draft_ids_json"] or "[]"))
            kept = [draft_id for draft_id in draft_ids if str(draft_id) not in expired]
            selected = str(row["selected_draft_id"] or "")
            if len(kept) != len(draft_ids) or selected in expired:
                self._connection.execute(
                    """UPDATE workspace_triage SET draft_ids_json=?,selected_draft_id=?
                       WHERE user_id=? AND event_seq=? AND send_state='none'""",
                    (json.dumps(kept, separators=(",", ":")), None if selected in expired else row["selected_draft_id"],
                     user_id, int(row["event_seq"])),
                )
        placeholders = ",".join("?" for _ in expired_ids)
        params = (user_id, *expired_ids)
        self._connection.execute(
            f"DELETE FROM draft_context_snapshots WHERE user_id=? AND draft_id IN ({placeholders})",
            params,
        )
        self._connection.execute(
            f"DELETE FROM draft_reply_origins WHERE user_id=? AND draft_id IN ({placeholders})",
            params,
        )
        return self._connection.execute(
            f"""DELETE FROM drafts WHERE user_id=? AND id IN ({placeholders})
                AND status='proposed' AND created_at<=?""",
            (user_id, *expired_ids, cutoff),
        ).rowcount

    def expire_proposed_drafts(
        self, *, now: float | None = None, user_id: str | None = None
    ) -> int:
        """Remove three-day-old proposals; uncertain/sent rows are durable receipts."""
        user = self._user(user_id)
        instant = time.time() if now is None else float(now)
        with self._lock:
            if self._connection.in_transaction:
                return self._expire_proposed_drafts_locked(user_id=user, now=instant)
            with self._connection:
                self._connection.execute("BEGIN IMMEDIATE")
                return self._expire_proposed_drafts_locked(user_id=user, now=instant)

    def conversation(
        self, conversation_id: str, *, user_id: str | None = None, text_limit: int | None = None
    ) -> dict[str, object] | None:
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user_id)
        user_id = self._user(user_id)
        self.expire_proposed_drafts(user_id=user_id)
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM conversations WHERE user_id=? AND id=?", (user_id, conversation_id)
            ).fetchone()
            if row is None:
                return None
            history_order = 'message_id' if row['source'] == 'phone' else 'rowid'
            messages = self._connection.execute(
                f"""
                SELECT * FROM (
                    SELECT source,message_id,sender,body,direction,received_at,seen_at,edited_at,provider_read,outbox_receipt
                    FROM messages
                    WHERE user_id=? AND conversation_id=? AND deleted_at IS NULL ORDER BY received_at DESC,{history_order} DESC LIMIT 200
                ) ORDER BY received_at,message_id
                """,
                (user_id, conversation_id),
            ).fetchall()
            drafts = self._connection.execute(
                """
                SELECT id,body,status,outbox_receipt,created_at FROM drafts
                WHERE user_id=? AND conversation_id=? ORDER BY created_at
                """,
                (user_id, conversation_id),
            ).fetchall()
            name_row = self._connection.execute(
                "SELECT name,is_explicit FROM contact_names WHERE user_id=? AND source=? AND sender=?",
                (user_id, row["source"], f"chat:{row['account_ref']}:{row['peer_id']}"
                 if row["source"] in {"max", "phone", "vk", "yandex"} and row["account_ref"] and row["peer_id"] else row["sender"]),
            ).fetchone()
            profile_row = self._connection.execute(
                "SELECT username,first_name,last_name,phone FROM search_profiles "
                "WHERE user_id=? AND conversation_id=?",
                (user_id, conversation_id),
            ).fetchone() if row["source"] == "telegram" else None
            profile_name = " ".join(
                str(profile_row[key] or "").strip()
                for key in ("first_name", "last_name")
                if profile_row is not None and str(profile_row[key] or "").strip()
            )
            if not profile_name and profile_row is not None:
                profile_name = str(profile_row["username"] or "").strip()
                if profile_name:
                    profile_name = "@" + profile_name.lstrip("@")
                else:
                    profile_name = str(profile_row["phone"] or "").strip()
            explicit_name = str(name_row["name"] or "") if name_row and name_row["is_explicit"] else ""
            display_name = explicit_name or profile_name or (str(name_row["name"]) if name_row else "")
            history_row = None
            if row["source"] == "telegram" and row["account_ref"] and row["peer_id"]:
                history_row = self._connection.execute("""SELECT oldest_id,complete,fetched_count
                    FROM telegram_history_state WHERE user_id=? AND account_ref=? AND peer_id=?""",
                    (user_id,row["account_ref"],row["peer_id"])).fetchone()
        message_records = [dict(item) for item in messages]
        draft_records = [dict(item) for item in drafts]
        with self._lock:
            snapshots = dict(self._connection.execute(
                "SELECT draft_id,context_json FROM draft_context_snapshots WHERE user_id=? AND conversation_id=?",
                (user_id, conversation_id)).fetchall())
        for draft in draft_records:
            saved = json.loads(snapshots[draft["id"]]) if draft["id"] in snapshots else None
            draft["reply_to"] = None
            draft["context_status"] = "unknown"
            if saved:
                draft["reply_to"] = {key: saved[key] for key in ("source", "message_id", "body", "received_at")}
                try:
                    current = self.reply_context_snapshot(conversation_id, source=saved["source"],
                        message_id=saved["message_id"], user_id=user_id)
                    draft["context_status"] = "current" if current["fingerprint"] == saved["fingerprint"] else "stale"
                except KeyError:
                    draft["context_status"] = "stale"
        if row['source'] in {'max','yandex'} and row['conversation_kind'] in {'group','channel'}:
            with self._lock:
                author_names = dict(self._connection.execute(
                    "SELECT sender,name FROM contact_names WHERE user_id=? AND source=?",
                    (user_id,row['source'])).fetchall())
            for item in message_records:
                item['sender_name'] = author_names.get(item['sender'], '')
        for item in message_records:
            if item.get('outbox_receipt'):
                item['outbox_receipt']=self.match_sms_receipt(str(item['outbox_receipt']),account_ref=str(row['account_ref']),sender=str(item['sender']),body=str(item['body']),user_id=user_id) if item['source']=='sms' else ''
            if not item.get('outbox_receipt'):
                item.pop('outbox_receipt',None)
            attachments = self.attachments_for_message(
                source=str(item["source"]), message_id=str(item["message_id"]), user_id=user_id
            )
            if attachments:
                item["attachments"] = attachments
        if text_limit is not None:
            for item in message_records:
                item["body"] = str(item["body"])[:text_limit]
        return {
            "id": row["id"], "route_id": row["route_id"], "response_mode": row["response_mode"],
            "identity_id": row["identity_id"], "source": row["source"], "sender": row["sender"],
            "account_ref": str(row["account_ref"] or ""),
            "peer_id": str(row["peer_id"] or ""),
            "conversation_kind": str(row['conversation_kind'] or 'unknown'),
            "display_name": display_name,
            "messages": message_records, "drafts": draft_records,
            **({'phone_history': self._phone_history_cursor(row['id'], message_records, user_id)} if row['source']=='phone' else {}),
            **({"history": {
                "status": "complete" if history_row["complete"] else "partial",
                "oldest_id": int(history_row["oldest_id"]),
                "fetched_count": int(history_row["fetched_count"]),
            } if history_row is not None else {"status": "unverified"}}
            if row["source"] == "telegram" else {}),
        }

    def _phone_history_cursor(self, conversation_id: str, messages: list[dict], user_id: str) -> dict:
        if not messages:
            return {'oldest_id': '', 'has_more': False}
        first = messages[0]
        with self._lock:
            more = self._connection.execute("SELECT 1 FROM messages WHERE user_id=? AND conversation_id=? AND deleted_at IS NULL AND (received_at<? OR (received_at=? AND message_id<?)) LIMIT 1",
                (user_id,conversation_id,first['received_at'],first['received_at'],first['message_id'])).fetchone()
        return {'oldest_id': first['message_id'], 'has_more': more is not None}

    def phone_history_page(self, conversation_id: str, *, before_message_id: str, limit: int = 100,
                           user_id: str | None = None) -> dict:
        """Read bounded older call entries from the same account/person only."""
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user_id)
        user_id = self._user(user_id)
        if type(limit) is not int or not 1 <= limit <= 100 or not before_message_id or len(before_message_id)>256:
            raise ValueError('invalid phone history cursor')
        with self._lock:
            chat = self._connection.execute("SELECT source FROM conversations WHERE user_id=? AND id=?",(user_id,conversation_id)).fetchone()
            if chat is None:
                raise KeyError('conversation not found')
            if chat['source'] != 'phone':
                raise ValueError('phone history requires a phone conversation')
            anchor = self._connection.execute("SELECT received_at FROM messages WHERE user_id=? AND conversation_id=? AND source='phone' AND message_id=? AND deleted_at IS NULL",(user_id,conversation_id,before_message_id)).fetchone()
            if anchor is None:
                raise ValueError('phone history cursor is not in this conversation')
            rows = self._connection.execute("SELECT source,message_id,sender,body,direction,received_at,seen_at,edited_at,provider_read FROM messages WHERE user_id=? AND conversation_id=? AND deleted_at IS NULL AND (received_at<? OR (received_at=? AND message_id<?)) ORDER BY received_at DESC,message_id DESC LIMIT ?",(user_id,conversation_id,anchor['received_at'],anchor['received_at'],before_message_id,limit)).fetchall()
        messages = [dict(row) for row in reversed(rows)]
        for message in messages:
            attachments = self.attachments_for_message(source=message['source'],message_id=message['message_id'],user_id=user_id)
            if attachments:
                message['attachments'] = attachments
        return {'conversation_id': conversation_id, 'messages': messages, **self._phone_history_cursor(conversation_id,messages,user_id)}

    def telegram_history_page(
        self, conversation_id: str, *, before_id: int, limit: int = 50,
        user_id: str | None = None,
    ) -> list[dict[str, object]]:
        """One provider-ID cursor page from an owned local Telegram mirror.

        Provider reconciliation precedes this query. Does not change receipts.
        """
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user_id)
        if (type(before_id) is not int or not 1 <= before_id <= 2_147_483_647 or
                type(limit) is not int or not 1 <= limit <= 100):
            raise ValueError("invalid Telegram history page")
        user_id = self._user(user_id)
        locator = "CAST(substr(message_id,length(rtrim(message_id,'0123456789'))+1) AS INTEGER)"
        with self._lock:
            chat = self._connection.execute("""SELECT id FROM conversations
                WHERE user_id=? AND id=? AND source='telegram'""",
                (user_id,conversation_id)).fetchone()
            if chat is None:
                raise KeyError("Telegram chat not found")
            rows = self._connection.execute(f"""SELECT source,message_id,sender,body,direction,
                received_at,seen_at,edited_at,provider_read FROM messages
                WHERE user_id=? AND conversation_id=? AND deleted_at IS NULL
                AND {locator}>0 AND {locator}<?
                ORDER BY {locator} DESC LIMIT ?""",
                (user_id,conversation_id,before_id,limit)).fetchall()
        results = [dict(row) for row in reversed(rows)]
        for result in results:
            attachments = self.attachments_for_message(
                source="telegram",message_id=str(result["message_id"]),user_id=user_id)
            if attachments:
                result["attachments"] = attachments
        return results

    def reconcile_vk_peer_kind(self, *, account_ref: str, peer_id: str, kind: str,
                               user_id: str | None = None) -> int:
        """Correct old numeric-ID guesses using a verified VK provider peer type.

        Called by the authenticated personal-account poller, including unchanged
        histories. Message IDs/cursors and explicit per-chat policy are preserved.
        """
        if kind not in {"direct", "group"} or not account_ref.startswith("vk:") or not peer_id:
            raise ValueError("invalid verified VK peer metadata")
        user = self._user(user_id)
        with self._lock, self._connection:
            rows = self._connection.execute(
                """SELECT id FROM conversations WHERE user_id=? AND source='vk'
                   AND account_ref=? AND peer_id=? AND conversation_kind!=?""",
                (user, account_ref, peer_id, kind),
            ).fetchall()
            for row in rows:
                self._connection.execute(
                    "UPDATE conversations SET conversation_kind=? WHERE user_id=? AND id=?",
                    (kind, user, row["id"]),
                )
                self._invalidate_workspace_events_for_chat(user, row["id"])
            return len(rows)

    def set_conversation_account(
        self, conversation_id: str, account_ref: str, *, user_id: str | None = None
    ) -> bool:
        """Pin the reply account; a pending approval pins its existing account."""
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user_id)
        user_id = self._user(user_id)
        with self._lock, self._connection:
            row = self._connection.execute('SELECT source,peer_id,account_ref FROM conversations WHERE user_id=? AND id=?', (user_id,conversation_id)).fetchone()
            if row is not None and row['source']=='phone' and row['peer_id'].startswith('phone:v1:') and row['account_ref'] != account_ref.strip():
                return False
            return self._connection.execute(
                """UPDATE conversations SET account_ref=? WHERE user_id=? AND id=?
                   AND (COALESCE(account_ref,'')=? OR NOT EXISTS (
                       SELECT 1 FROM drafts WHERE user_id=? AND conversation_id=?
                       AND status IN ('proposed','sending')))
                """,
                (account_ref.strip(), user_id, conversation_id, account_ref.strip(), user_id, conversation_id),
            ).rowcount == 1

    # --- per-user AI (BYOK) ----------------------------------------------------

    def user_preference(self, key: str, *, user_id: str | None = None, default: str | None = None) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM user_preferences WHERE user_id=? AND key=?",
                (self._user(user_id), key),
            ).fetchone()
        return default if row is None else str(row["value"])

    def set_user_preference(self, key: str, value: str, *, user_id: str | None = None) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO user_preferences(user_id,key,value,updated_at) VALUES (?,?,?,?)",
                (self._user(user_id), key, str(value), time.time()),
            )

    def webhook_settings(self, *, user_id: str | None = None) -> dict[str, object] | None:
        user = self._user(user_id)
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM user_webhook_settings WHERE user_id=?", (user,),
            ).fetchone()
        return None if row is None else dict(row)

    def webhook_users(self) -> list[str]:
        with self._lock:
            return [str(row[0]) for row in self._connection.execute(
                "SELECT user_id FROM user_webhook_settings WHERE enabled=1 ORDER BY updated_at,user_id"
            ).fetchall()]

    def webhook_event_head(self, *, user_id: str) -> int:
        with self._lock:
            return int(self._connection.execute(
                "SELECT COALESCE(MAX(seq),0) FROM workspace_events WHERE user_id=?",
                (self._user(user_id),),
            ).fetchone()[0])

    def save_webhook_settings(self, *, endpoint: str, secret_ref: str,
                              include_message_text: bool, enabled: bool,
                              user_id: str | None = None) -> dict[str, object]:
        user = self._user(user_id)
        now = time.time()
        with self._lock, self._connection:
            head = int(self._connection.execute(
                "SELECT COALESCE(MAX(seq),0) FROM workspace_events WHERE user_id=?", (user,),
            ).fetchone()[0])
            self._connection.execute(
                """INSERT INTO user_webhook_settings
                   (user_id,enabled,endpoint,secret_ref,include_message_text,cursor,updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled,
                     endpoint=excluded.endpoint,secret_ref=excluded.secret_ref,
                     include_message_text=excluded.include_message_text,cursor=excluded.cursor,
                     attempt_count=0,next_attempt_at=0,last_attempt_at=NULL,last_error='',updated_at=excluded.updated_at""",
                (user, int(enabled), endpoint, secret_ref, int(include_message_text), head, now),
            )
        return self.webhook_settings(user_id=user) or {}

    def advance_webhook_cursor(self, *, user_id: str, cursor: int, head: int,
                               delivered: bool = False) -> None:
        user = self._user(user_id)
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE user_webhook_settings SET cursor=MAX(cursor,?),attempt_count=0,
                   next_attempt_at=0,last_attempt_at=COALESCE(?,last_attempt_at),
                   last_success_at=COALESCE(?,last_success_at),last_error=''
                   WHERE user_id=?""",
                (max(0, min(int(cursor), int(head))), now if delivered else None,
                 now if delivered else None, user),
            )

    def record_webhook_failure(self, *, user_id: str, message: str, retry_at: float) -> None:
        user = self._user(user_id)
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT attempt_count FROM user_webhook_settings WHERE user_id=?", (user,),
            ).fetchone()
            if row is None:
                return
            attempts = min(int(row["attempt_count"]) + 1, 12)
            delay = min(5 * (2 ** min(attempts - 1, 9)), 1800)
            self._connection.execute(
                """UPDATE user_webhook_settings SET attempt_count=?,next_attempt_at=?,
                   last_attempt_at=?,last_error=? WHERE user_id=?""",
                (attempts, max(float(retry_at), time.time()) + delay, time.time(),
                 str(message)[:200], user),
            )

    def context_settings(self, *, user_id: str | None = None) -> dict[str, object]:
        user = self._user(user_id)

        def value(key: str, default: int, maximum: int) -> int:
            raw = self.user_preference(key, user_id=user, default=str(default))
            try:
                return max(0, min(int(str(raw)), maximum))
            except ValueError:
                return default

        def flags(key: str, defaults: dict[str, bool]) -> dict[str, bool]:
            raw = self.user_preference(
                key, user_id=user,
                default=json.dumps(defaults, ensure_ascii=False, separators=(",", ":")),
            )
            try:
                parsed = json.loads(str(raw))
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = {}
            if not isinstance(parsed, dict):
                parsed = {}
            result = dict(defaults)
            for name, enabled in parsed.items():
                if isinstance(name, str) and isinstance(enabled, bool):
                    result[name] = enabled
            return result

        message_count = value(
            "context_message_count", CONTEXT_MESSAGE_COUNT_DEFAULT,
            CONTEXT_MESSAGE_COUNT_MAX,
        )
        token_budget = value(
            "context_token_budget", CONTEXT_TOKEN_BUDGET_DEFAULT,
            CONTEXT_TOKEN_BUDGET_MAX,
        )
        max_message_count = max(
            message_count,
            value(
                "context_max_message_count", CONTEXT_MAX_MESSAGE_COUNT_DEFAULT,
                CONTEXT_MAX_MESSAGE_COUNT_MAX,
            ),
        )
        max_token_budget = max(
            64, token_budget,
            value(
                "context_max_token_budget", CONTEXT_MAX_TOKEN_BUDGET_DEFAULT,
                CONTEXT_MAX_TOKEN_BUDGET_MAX,
            ),
        )
        summary_token_budget = value(
            "summary_token_budget", SUMMARY_TOKEN_BUDGET_DEFAULT,
            SUMMARY_TOKEN_BUDGET_MAX,
        )
        if summary_token_budget < 64:
            summary_token_budget = SUMMARY_TOKEN_BUDGET_DEFAULT
        return {
            "message_count": message_count,
            "max_message_count": max_message_count,
            "token_budget": token_budget,
            "max_token_budget": max_token_budget,
            "summary_token_budget": summary_token_budget,
            "summary_retention_days": value(
                "summary_retention_days", SUMMARY_RETENTION_DAYS_DEFAULT,
                SUMMARY_RETENTION_DAYS_MAX,
            ),
            "cache_channels": flags(
                "summary_cache_channels", SUMMARY_CACHE_CHANNEL_DEFAULTS,
            ),
            "cache_conversation_kinds": flags(
                "summary_cache_conversation_kinds", SUMMARY_CACHE_KIND_DEFAULTS,
            ),
        }

    def set_context_settings(
        self, *, message_count: int | None = None, max_message_count: int | None = None,
        token_budget: int | None = None, max_token_budget: int | None = None,
        summary_token_budget: int | None = None,
        summary_retention_days: int | None = None,
        cache_channels: dict[str, bool] | None = None,
        cache_conversation_kinds: dict[str, bool] | None = None,
        user_id: str | None = None,
    ) -> dict[str, object]:
        user = self._user(user_id)
        current = self.context_settings(user_id=user)
        values = {
            "message_count": current["message_count"] if message_count is None else message_count,
            "max_message_count": (
                current["max_message_count"] if max_message_count is None else max_message_count
            ),
            "token_budget": current["token_budget"] if token_budget is None else token_budget,
            "max_token_budget": (
                current["max_token_budget"] if max_token_budget is None else max_token_budget
            ),
            "summary_token_budget": (
                current["summary_token_budget"]
                if summary_token_budget is None else summary_token_budget
            ),
            "summary_retention_days": (
                current["summary_retention_days"]
                if summary_retention_days is None else summary_retention_days
            ),
        }
        for name, minimum, maximum in (
            ("message_count", 0, CONTEXT_MESSAGE_COUNT_MAX),
            ("max_message_count", 1, CONTEXT_MAX_MESSAGE_COUNT_MAX),
            ("token_budget", 0, CONTEXT_TOKEN_BUDGET_MAX),
            ("max_token_budget", 64, CONTEXT_MAX_TOKEN_BUDGET_MAX),
            ("summary_token_budget", 64, SUMMARY_TOKEN_BUDGET_MAX),
            ("summary_retention_days", 0, SUMMARY_RETENTION_DAYS_MAX),
        ):
            setting = values[name]
            if type(setting) is not int or not 0 <= setting <= maximum:
                raise ValueError(f"{name} must be between {minimum} and {maximum}")
            if setting < minimum:
                raise ValueError(f"{name} must be between {minimum} and {maximum}")
        if int(values["max_message_count"]) < int(values["message_count"]):
            raise ValueError("max_message_count must be at least message_count")
        if int(values["max_token_budget"]) < int(values["token_budget"]):
            raise ValueError("max_token_budget must be at least token_budget")

        def validated_flags(
            name: str, supplied: dict[str, bool] | None, current_value: object,
            *, normalise_channels: bool = False,
        ) -> dict[str, bool]:
            if supplied is None:
                return dict(current_value) if isinstance(current_value, dict) else {}
            if not isinstance(supplied, dict) or len(supplied) > 64:
                raise ValueError(f"{name} must be an object with at most 64 entries")
            result: dict[str, bool] = {}
            for raw_key, enabled in supplied.items():
                key = _cache_channel(raw_key) if normalise_channels else str(raw_key).strip().lower()
                if not re.fullmatch(r"[a-z0-9_.:-]{1,64}", key) or type(enabled) is not bool:
                    raise ValueError(f"{name} must contain safe boolean entries")
                result[key] = enabled
            return result

        channels = validated_flags(
            "cache_channels", cache_channels, current["cache_channels"],
            normalise_channels=True,
        )
        kinds = validated_flags(
            "cache_conversation_kinds", cache_conversation_kinds,
            current["cache_conversation_kinds"],
        )
        now = time.time()
        with self._lock, self._connection:
            self._connection.executemany(
                "INSERT OR REPLACE INTO user_preferences(user_id,key,value,updated_at) VALUES (?,?,?,?)",
                (
                    (user, "context_message_count", str(values["message_count"]), now),
                    (user, "context_max_message_count", str(values["max_message_count"]), now),
                    (user, "context_token_budget", str(values["token_budget"]), now),
                    (user, "context_max_token_budget", str(values["max_token_budget"]), now),
                    (user, "summary_token_budget", str(values["summary_token_budget"]), now),
                    (user, "summary_retention_days", str(values["summary_retention_days"]), now),
                    (user, "summary_cache_channels", json.dumps(
                        channels, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                    ), now),
                    (user, "summary_cache_conversation_kinds", json.dumps(
                        kinds, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                    ), now),
                ),
            )
            self._purge_conversation_summaries(user_id=user)
        return self.context_settings(user_id=user)

    @staticmethod
    def _summary_cache_enabled_for(
        source: object, conversation_kind: object, settings: dict[str, object],
    ) -> bool:
        channels = settings.get("cache_channels")
        kinds = settings.get("cache_conversation_kinds")
        return bool(
            isinstance(channels, dict)
            and channels.get(_cache_channel(source), False) is True
            and isinstance(kinds, dict)
            and kinds.get(str(conversation_kind or "unknown").strip().lower(), False) is True
        )

    def _invalidate_message_summary(self, user_id: str, source: str, message_id: str) -> None:
        self._connection.execute(
            """DELETE FROM conversation_summaries WHERE user_id=? AND EXISTS (
                SELECT 1 FROM messages m WHERE m.user_id=? AND m.source=? AND m.message_id=?
                AND m.conversation_id=conversation_summaries.conversation_id
)""",
            (user_id, user_id, source, message_id),
        )

    def maintain_conversation_summaries(self, *, user_id: str | None = None) -> int:
        """Run at startup and periodically to enforce inactive cache retention."""
        with self._lock, self._connection:
            users = [self._user(user_id)] if user_id else [str(row[0]) for row in
                self._connection.execute("SELECT DISTINCT user_id FROM conversation_summaries")]
            return sum(self._purge_conversation_summaries(user_id=user) for user in users)

    def _message_anchor(self, user: str, conversation_id: str, message_id: str,
                        source: str = "", rowid: int | None = None) -> int | None:
        if message_id.startswith("rowid:") and rowid is None:
            try:
                rowid = int(message_id[6:])
            except ValueError:
                return None
        sql = "SELECT rowid FROM messages WHERE user_id=? AND conversation_id=?"
        parameters: list[object] = [user, conversation_id]
        if rowid is not None:
            sql += " AND rowid=?"
            parameters.append(rowid)
        else:
            sql += " AND message_id=?"
            parameters.append(message_id)
            if source:
                sql += " AND source=?"
                parameters.append(source)
        rows = self._connection.execute(sql + " LIMIT 2", parameters).fetchall()
        return int(rows[0][0]) if len(rows) == 1 else None

    def _purge_conversation_summaries(self, *, user_id: str) -> int:
        settings = self.context_settings(user_id=user_id)
        rows = self._connection.execute(
            """SELECT s.conversation_id,c.source,c.conversation_kind,s.updated_at
               FROM conversation_summaries s
               LEFT JOIN conversations c ON c.user_id=s.user_id AND c.id=s.conversation_id
               WHERE s.user_id=?""",
            (user_id,),
        ).fetchall()
        retention_days = int(settings["summary_retention_days"])
        cutoff = time.time() - retention_days * 86_400 if retention_days > 0 else None
        for summary in self._connection.execute(
            "SELECT conversation_id,summary FROM conversation_summaries WHERE user_id=?", (user_id,),
        ).fetchall():
            bounded = _prefix_for_tokens(str(summary["summary"]), int(settings["summary_token_budget"]))
            if bounded != summary["summary"]:
                self._connection.execute(
                    "UPDATE conversation_summaries SET summary=? WHERE user_id=? AND conversation_id=?",
                    (bounded, user_id, summary["conversation_id"]),
                )
        stale = [
            str(row["conversation_id"])
            for row in rows
            if row["source"] is None
            or not self._summary_cache_enabled_for(
                row["source"], row["conversation_kind"], settings,
            )
            or (cutoff is not None and float(row["updated_at"]) < cutoff)
        ]
        if not stale:
            return 0
        placeholders = ",".join("?" for _ in stale)
        return self._connection.execute(
            f"DELETE FROM conversation_summaries WHERE user_id=? AND conversation_id IN ({placeholders})",
            (user_id, *stale),
        ).rowcount

    def conversation_summary(
        self, conversation_id: str, *, through_message_id: str = "",
        through_message_source: str = "", through_message_rowid: int | None = None,
        user_id: str | None = None,
    ) -> dict[str, object] | None:
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user_id)
        user = self._user(user_id)
        with self._lock, self._connection:
            self._purge_conversation_summaries(user_id=user)
            row = self._connection.execute(
                """SELECT summary,through_message_rowid,through_message_id,
                          summarized_message_count,updated_at
                   FROM conversation_summaries
                   WHERE user_id=? AND conversation_id=?""",
                (user, conversation_id),
            ).fetchone()
            if row is not None and (through_message_id or through_message_rowid is not None):
                anchor = self._message_anchor(user, conversation_id, through_message_id,
                                              through_message_source, through_message_rowid)
                if anchor is None:
                    return None
                if self._connection.execute("""SELECT 1 FROM messages WHERE user_id=? AND conversation_id=?
                    AND rowid<=? AND deleted_at IS NULL AND (received_at,rowid)>
                    (SELECT received_at,rowid FROM messages WHERE user_id=? AND rowid=?) LIMIT 1""",
                    (user,conversation_id,int(row["through_message_rowid"]),user,anchor)).fetchone():
                    return None
        return None if row is None else dict(row)

    def conversation_summary_work(
        self, conversation_id: str, *, through_message_id: str = "",
        through_message_source: str = "", through_message_rowid: int | None = None,
        user_id: str | None = None,
    ) -> dict[str, object] | None:
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user_id)
        user = self._user(user_id)
        settings = self.context_settings(user_id=user)
        with self._lock, self._connection:
            self._purge_conversation_summaries(user_id=user)
            conversation = self._connection.execute(
                "SELECT source,conversation_kind,account_ref FROM conversations WHERE user_id=? AND id=?",
                (user, conversation_id),
            ).fetchone()
            if conversation is None:
                raise KeyError("conversation not found")
            if self.account_ai_disabled(str(conversation["account_ref"]), user_id=user):
                return None
            if not self._summary_cache_enabled_for(
                conversation["source"], conversation["conversation_kind"], settings,
            ):
                return None
            cached = self._connection.execute(
                """SELECT summary,through_message_rowid,through_message_id,
                          summarized_message_count,updated_at
                   FROM conversation_summaries
                   WHERE user_id=? AND conversation_id=?""",
                (user, conversation_id),
            ).fetchone()
            cursor = 0 if cached is None else int(cached["through_message_rowid"])
            anchor_rowid: int | None = None
            if through_message_id or through_message_rowid is not None:
                anchor = self._message_anchor(user, conversation_id, through_message_id,
                                              through_message_source, through_message_rowid)
                if anchor is None:
                    return None
                anchor_rowid = anchor
                # A cache cursor is an arrival frontier, not a timestamp.
                # Never jump across a newer native message excluded by an old
                # event anchor, even when late history has larger rowids.
                blocked = self._connection.execute("""SELECT MIN(rowid) FROM messages
                    WHERE user_id=? AND conversation_id=? AND rowid>? AND deleted_at IS NULL
                    AND (received_at,rowid)>(SELECT received_at,rowid FROM messages WHERE user_id=? AND rowid=?)""",
                    (user,conversation_id,cursor,user,anchor_rowid)).fetchone()[0]
                cached_for_anchor = self.conversation_summary(conversation_id,
                    through_message_rowid=anchor_rowid,user_id=user)
                if cached is not None and cached_for_anchor is None:
                    return None
            upper_sql = " AND (received_at,rowid)<=(SELECT received_at,rowid FROM messages WHERE user_id=? AND rowid=?)" if anchor_rowid is not None else ""
            parameters: list[object] = [user, conversation_id, cursor]
            if anchor_rowid is not None:
                parameters.extend([user, anchor_rowid])
            if anchor_rowid is not None and blocked is not None:
                upper_sql += " AND rowid<?"
                parameters.append(int(blocked))
            parameters.append(100)
            # Rows already present at snapshot time are not concurrent arrivals.
            # They can lie beyond this page's immutable event frontier after a
            # historical backfill; retain their arrival watermark separately.
            history_highwater = int(self._connection.execute(
                "SELECT COALESCE(MAX(rowid),0) FROM messages WHERE user_id=? AND conversation_id=?",
                (user, conversation_id),
            ).fetchone()[0])
            rows = self._connection.execute(
                f"""SELECT rowid AS message_rowid,source,message_id,sender,body,direction,received_at
                   FROM messages
                   WHERE user_id=? AND conversation_id=? AND deleted_at IS NULL AND rowid>?{upper_sql}
                   ORDER BY rowid LIMIT ?""",
                parameters,
            ).fetchall()
        messages = sorted([dict(row) for row in rows], key=lambda m: (m["received_at"], m["message_rowid"]))
        for message in messages:
            attachments = self.attachments_for_message(
                source=str(message["source"]), message_id=str(message["message_id"]),
                user_id=user,
            )
            if attachments:
                message["attachments"] = attachments
        return {
            "previous_summary": "" if cached is None else str(cached["summary"]),
            "expected_through_message_rowid": cursor,
            "summarized_message_count": (
                0 if cached is None else int(cached["summarized_message_count"])
            ),
            "new_messages": messages,
            "history_highwater": history_highwater,
            "summary_token_budget": int(settings["summary_token_budget"]),
        }

    def save_conversation_summary(
        self, conversation_id: str, *, summary: str,
        expected_through_message_rowid: int, through_message_rowid: int,
        through_message_id: str, summarized_message_count: int,
        expected_messages: list[dict[str, object]] | None = None,
        expected_history_highwater: int | None = None,
        user_id: str | None = None,
    ) -> bool:
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user_id)
        user = self._user(user_id)
        settings = self.context_settings(user_id=user)
        text = _prefix_for_tokens(summary, int(settings["summary_token_budget"]))
        if not text:
            raise ValueError("conversation summary must not be empty")
        if (
            type(expected_through_message_rowid) is not int
            or type(through_message_rowid) is not int
            or through_message_rowid <= expected_through_message_rowid
            or type(summarized_message_count) is not int
            or summarized_message_count <= 0
        ):
            raise ValueError("invalid conversation summary cursor")
        if expected_history_highwater is not None and (
                type(expected_history_highwater) is not int or expected_history_highwater < 0):
            raise ValueError("invalid conversation summary history watermark")
        now = time.time()
        with self._lock, self._connection:
            # A provider edit can arrive while the AI is running, including on
            # the first page when there is no existing cache to invalidate.
            if expected_messages is not None:
                newest_row = max(int(m["message_rowid"]) for m in expected_messages)
                snapshot_highwater = max(newest_row, expected_history_highwater or 0)
                latest_time = max(float(m["received_at"]) for m in expected_messages)
                if self._connection.execute("""SELECT 1 FROM messages WHERE user_id=? AND conversation_id=?
                    AND rowid>? AND received_at<=? AND deleted_at IS NULL LIMIT 1""",
                    (user, conversation_id, snapshot_highwater, latest_time)).fetchone():
                    return False
                for snapshot in expected_messages:
                    current = self._connection.execute(
                        "SELECT source,message_id,sender,body,direction,received_at FROM messages WHERE user_id=? AND conversation_id=? AND rowid=? AND deleted_at IS NULL",
                        (user, conversation_id, snapshot["message_rowid"]),
                    ).fetchone()
                    if current is None or any(current[key] != snapshot[key] for key in current.keys()):
                        return False
                    attachments = self.attachments_for_message(
                        source=str(snapshot["source"]), message_id=str(snapshot["message_id"]), user_id=user)
                    if attachments != snapshot.get("attachments", []):
                        return False
            conversation = self._connection.execute(
                "SELECT source,conversation_kind,account_ref FROM conversations WHERE user_id=? AND id=?",
                (user, conversation_id),
            ).fetchone()
            if conversation is None:
                raise KeyError("conversation not found")
            if self.account_ai_disabled(str(conversation["account_ref"]), user_id=user):
                return False
            if not self._summary_cache_enabled_for(
                conversation["source"], conversation["conversation_kind"], settings,
            ):
                self._connection.execute(
                    "DELETE FROM conversation_summaries WHERE user_id=? AND conversation_id=?",
                    (user, conversation_id),
                )
                return False
            if expected_through_message_rowid == 0:
                changed = self._connection.execute(
                    """INSERT OR IGNORE INTO conversation_summaries
                       (user_id,conversation_id,summary,through_message_rowid,
                        through_message_id,summarized_message_count,updated_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (user, conversation_id, text, through_message_rowid,
                     through_message_id[:256], summarized_message_count, now),
                ).rowcount
            else:
                changed = self._connection.execute(
                    """UPDATE conversation_summaries
                       SET summary=?,through_message_rowid=?,through_message_id=?,
                           summarized_message_count=?,updated_at=?
                       WHERE user_id=? AND conversation_id=? AND through_message_rowid=?""",
                    (text, through_message_rowid, through_message_id[:256],
                     summarized_message_count, now, user, conversation_id,
                     expected_through_message_rowid),
                ).rowcount
        return changed == 1

    def bounded_conversation_context(
        self, conversation_id: str, *, current_message_id: str = "",
        current_message_source: str = "", current_message_rowid: int | None = None,
        before_message_id: str = "", user_id: str | None = None,
        message_limit: int | None = None, token_limit: int | None = None,
    ) -> list[dict[str, object]]:
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user_id)
        if before_message_id:
            current_message_id = before_message_id
        settings = self.context_settings(user_id=user_id)
        message_limit = int(settings["message_count"]) if message_limit is None else message_limit
        token_limit = int(settings["token_budget"]) if token_limit is None else token_limit
        if type(message_limit) is not int or not 0 <= message_limit <= int(settings["max_message_count"]):
            raise ValueError("message_limit exceeds configured context maximum")
        if type(token_limit) is not int or not 0 <= token_limit <= int(settings["max_token_budget"]):
            raise ValueError("token_limit exceeds configured context maximum")
        if message_limit <= 0 or token_limit <= 0:
            return []
        user = self._user(user_id)
        with self._lock:
            if self._connection.execute("SELECT 1 FROM conversations WHERE user_id=? AND id=?",
                                        (user, conversation_id)).fetchone() is None:
                raise KeyError("conversation not found")
            upper = self._message_anchor(user, conversation_id, current_message_id,
                                         current_message_source, current_message_rowid) if (
                current_message_id or current_message_rowid is not None) else None
            if (current_message_id or current_message_rowid is not None) and upper is None:
                return []
            sql = "SELECT rowid AS message_rowid,source,message_id,sender,body,direction,received_at FROM messages WHERE user_id=? AND conversation_id=?"
            parameters: list[object] = [user, conversation_id]
            if upper is not None:
                sql += " AND (received_at,rowid)<(SELECT received_at,rowid FROM messages WHERE user_id=? AND rowid=?)"
                parameters.extend([user, upper])
            rows = self._connection.execute(sql + " AND deleted_at IS NULL ORDER BY received_at DESC,rowid DESC LIMIT ?",
                                            (*parameters, message_limit)).fetchall()
        records = [dict(row) for row in reversed(rows)]
        for record in records:
            record["attachments"] = self.attachments_for_message(
                source=str(record["source"]), message_id=str(record["message_id"]), user_id=user)
        candidates: list[dict[str, object]] = []
        for raw in records:
            if not isinstance(raw, dict):
                continue
            message_id = str(raw.get("message_id") or "").strip()[:256]
            body = str(raw.get("body") or "").strip()
            attachments = raw.get("attachments")
            if not body and not (isinstance(attachments, list) and attachments):
                continue
            candidate: dict[str, object] = {
                "message_rowid": int(raw["message_rowid"]),
                "anchor_id": f"rowid:{raw['message_rowid']}",
                "source": str(raw.get("source") or "").strip()[:128],
                "message_id": message_id,
                "sender": " ".join(str(raw.get("sender") or "").split())[:320],
                "direction": " ".join(str(raw.get("direction") or "").split())[:32],
                "received_at": raw.get("received_at"),
                "body": body,
            }
            if isinstance(attachments, list) and attachments:
                candidate["attachments"] = [
                    {
                        key: attachment[key]
                        for key in (
                            "kind", "content_type", "filename", "size",
                            "attachment_id",
                        )
                        if attachment.get(key) is not None
                    }
                    for attachment in attachments[:8]
                    if isinstance(attachment, dict)
                ]
            candidates.append(candidate)
        candidates = candidates[-message_limit:]
        metadata_tokens = [
            _estimated_tokens(json.dumps(
                item | {"body": "", "body_truncated": False},
                ensure_ascii=False, separators=(",", ":"),
            ))
            for item in candidates
        ]
        def list_tokens() -> int:
            return _estimated_tokens("[" + "," * max(0, len(candidates) - 1) + "]")

        while candidates and sum(metadata_tokens) + list_tokens() >= token_limit:
            candidates.pop(0)
            metadata_tokens.pop(0)
        if not candidates:
            return []
        available = token_limit - sum(metadata_tokens) - list_tokens()
        needs = [_estimated_tokens(str(item["body"])) for item in candidates]
        allocations = [0] * len(candidates)
        pending = set(range(len(candidates)))
        while pending and available > 0:
            share = max(1, available // len(pending))
            satisfied: list[int] = []
            for index in sorted(pending):
                grant = min(share, needs[index] - allocations[index], available)
                allocations[index] += grant
                available -= grant
                if allocations[index] >= needs[index]:
                    satisfied.append(index)
                if available <= 0:
                    break
            for index in satisfied:
                pending.discard(index)
            if not satisfied and available < len(pending):
                break
        return [
            item | {
                "body": _prefix_for_tokens(item["body"], allocation),
                "body_truncated": allocation < need,
            }
            for item, allocation, need in zip(candidates, allocations, needs)
        ]

    USER_CAPABILITIES = ("read", "subscribe", "download", "send")

    def capability_enabled(self, capability: str, *, user_id: str | None = None) -> bool:
        capability = str(capability).strip().lower()
        if capability not in self.USER_CAPABILITIES:
            raise ValueError(f"unknown user capability: {capability}")
        key = f"capability:{capability}"
        # Backward compatibility for the first per-user policy rollout.
        default = self.user_preference("send_enabled", user_id=user_id, default="1") if capability == "send" else "1"
        value = self.user_preference(key, user_id=user_id, default=default)
        return str(value).strip().lower() not in {"0", "false", "no", "off"}

    def user_capabilities(self, *, user_id: str | None = None) -> dict[str, bool]:
        return {name: self.capability_enabled(name, user_id=user_id) for name in self.USER_CAPABILITIES}

    def set_user_capability(self, capability: str, enabled: bool, *, user_id: str | None = None) -> None:
        capability = str(capability).strip().lower()
        if capability not in self.USER_CAPABILITIES:
            raise ValueError(f"unknown user capability: {capability}")
        self.set_user_preference(f"capability:{capability}", "1" if enabled else "0", user_id=user_id)
        if capability == "send":
            self.set_user_preference("send_enabled", "1" if enabled else "0", user_id=user_id)

    def send_enabled(self, *, user_id: str | None = None) -> bool:
        return self.capability_enabled("send", user_id=user_id)

    def user_search_settings(self, *, user_id: str | None = None) -> dict[str, str] | None:
        """This user's opt-in semantic search settings, or None when untouched.

        Secrets are returned only to the server-side search path; every caller
        facing surface uses :meth:`user_search_settings_public`.
        """
        row = self._connection.execute(
            "SELECT enabled,qdrant_url,qdrant_collection,qdrant_token,embedding_url,"
            "embedding_model,embedding_token,dimensions,priority_conversations"
            " FROM user_search_settings WHERE user_id=?",
            (self._user(user_id),),
        ).fetchone()
        if row is None:
            return None
        return {key: ("" if row[key] is None else str(row[key])) for key in row.keys()}

    def user_search_settings_public(self, *, user_id: str | None = None) -> dict[str, object]:
        """Owner-facing settings: presence of a key, never the key itself."""
        row = self._connection.execute(
            "SELECT enabled,qdrant_url,qdrant_collection,qdrant_token,embedding_url,embedding_model,"
            "embedding_token,dimensions,priority_conversations FROM user_search_settings"
            " WHERE user_id=?",
            (self._user(user_id),),
        ).fetchone()
        if row is None:
            return {"enabled": False, "qdrant_url": "", "qdrant_collection": "", "embedding_url": "",
                    "embedding_model": "", "dimensions": 0, "priority_conversations": [],
                    "has_qdrant_key": False, "has_embedding_key": False}
        return {
            "enabled": str(row["enabled"]) in {"1", "true", "True"},
            "qdrant_url": str(row["qdrant_url"] or ""),
            "qdrant_collection": str(row["qdrant_collection"] or ""),
            "embedding_url": str(row["embedding_url"] or ""),
            "embedding_model": str(row["embedding_model"] or ""),
            "dimensions": int(row["dimensions"] or 0),
            "priority_conversations": [value for value in
                                        str(row["priority_conversations"] or "").split(",") if value],
            "has_qdrant_key": bool(row["qdrant_token"]),
            "has_embedding_key": bool(row["embedding_token"]),
        }

    def set_user_search_settings(self, values: dict[str, Any], *,
                                 user_id: str | None = None) -> dict[str, object]:
        """Save opt-in settings. Blank values inherit the deployment defaults."""
        current = self.user_search_settings(user_id=user_id) or {}
        enabled = values.get("enabled")
        enabled = current.get("enabled", "") if enabled is None else str(enabled).strip()
        if enabled.strip().lower() not in {"1", "0", "true", "false", "yes", "no", "on", "off", ""}:
            raise ValueError("enabled must be a boolean")
        url = str(values.get("qdrant_url", current.get("qdrant_url", ""))).strip()
        collection = str(values.get("qdrant_collection", current.get("qdrant_collection", ""))).strip()
        embedding_url = str(values.get("embedding_url", current.get("embedding_url", ""))).strip()
        embedding_model = str(values.get("embedding_model", current.get("embedding_model", ""))).strip()
        for value, label in ((url, "Qdrant URL"), (embedding_url, "embeddings URL")):
            if value and not value.startswith(("http://", "https://")):
                raise ValueError(f"{label} must be an http(s) URL")
        if len(collection) > 128 or not all(item.isalnum() or item in "_-" for item in collection):
            raise ValueError("collection must be a short Qdrant collection name")
        if len(embedding_model) > 128:
            raise ValueError("embedding model name is too long")
        try:
            dimensions = int(values.get("dimensions", current.get("dimensions") or 2048))
        except (TypeError, ValueError):
            raise ValueError("dimensions must be an integer") from None
        if not 16 <= dimensions <= 8192:
            raise ValueError("dimensions must be between 16 and 8192")
        qdrant_token = values.get("qdrant_token")
        qdrant_token = current.get("qdrant_token", "") if qdrant_token is None else str(qdrant_token).strip()
        embedding_token = values.get("embedding_token")
        embedding_token = (current.get("embedding_token", "") if embedding_token is None
                           else str(embedding_token).strip())
        for token in (qdrant_token, embedding_token):
            if len(token) > 4096:
                raise ValueError("token is too long")
        priority = values.get("priority_conversations")
        if priority is None:
            priority = [item for item in str(current.get("priority_conversations", "")).split(",") if item]
        if isinstance(priority, str):
            priority = [item for item in priority.split(",") if item]
        if not isinstance(priority, (list, tuple)) or len(priority) > 100:
            raise ValueError("priority_conversations must be a list of at most 100 ids")
        clean: list[str] = []
        for item in priority:
            value = str(item).strip()
            if not value:
                continue
            if len(value) > 128 or not all(part.isalnum() or part in "_:-." for part in value):
                raise ValueError("invalid priority conversation id")
            if value not in clean:
                clean.append(value)
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO user_search_settings(user_id,enabled,qdrant_url,qdrant_collection,"
                "qdrant_token,embedding_url,embedding_model,embedding_token,dimensions,"
                "priority_conversations,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (self._user(user_id), "1" if enabled.strip().lower() in {"1", "true", "yes", "on"} else "0",
                 url, collection, qdrant_token, embedding_url, embedding_model, embedding_token,
                 dimensions, ",".join(clean), time.time()))
        return self.user_search_settings_public(user_id=user_id)

    def clear_user_search_settings(self, *, user_id: str | None = None) -> bool:
        with self._lock, self._connection:
            return self._connection.execute(
                "DELETE FROM user_search_settings WHERE user_id=?", (self._user(user_id),)
            ).rowcount == 1

    def ai_settings(self, *, user_id: str | None = None) -> dict[str, str] | None:
        """Return the user's own AI endpoint/model/token, or None for server default."""
        row = self._connection.execute(
            "SELECT endpoint,model,token FROM user_ai_settings WHERE user_id=?",
            (self._user(user_id),),
        ).fetchone()
        return None if row is None else {
            "endpoint": str(row["endpoint"]), "model": str(row["model"]), "token": str(row["token"]),
        }

    def set_ai_settings(
        self, *, endpoint: str, model: str, token: str, user_id: str | None = None
    ) -> None:
        endpoint, model, token = endpoint.strip(), model.strip(), token.strip()
        if not endpoint.startswith(("http://", "https://")) or not model or not token:
            raise ValueError("AI settings require an http(s) endpoint, model and token")
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO user_ai_settings (user_id,endpoint,model,token,updated_at) VALUES (?,?,?,?,?)",
                (self._user(user_id), endpoint, model, token, time.time()),
            )

    def clear_ai_settings(self, *, user_id: str | None = None) -> bool:
        with self._lock, self._connection:
            return self._connection.execute(
                "DELETE FROM user_ai_settings WHERE user_id=?", (self._user(user_id),)
            ).rowcount == 1

    def message(
        self, message_id: str, *, source: str | None = None, user_id: str | None = None,
        text_limit: int = 65_536,
    ) -> dict[str, object] | None:
        where, values = "user_id=? AND message_id=?", [self._user(user_id), message_id]
        if source in {"mail", "email", "gmail"}:
            where += " AND (source IN ('mail','email','gmail') OR source LIKE 'gmail:%' OR source LIKE 'email:%')"
        elif source:
            where += " AND source=?"
            values.append(source)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT source,message_id,conversation_id,sender,body,direction,received_at,seen_at,edited_at,provider_read,provider_reply_to,provider_thread_id
                FROM messages WHERE {where} AND deleted_at IS NULL ORDER BY received_at DESC LIMIT 2
                """,
                values,
            ).fetchall()
        if len(rows) > 1:
            raise ValueError("message_id is ambiguous; provide channel")
        if not rows:
            return None
        result = dict(rows[0])
        result["body"] = str(result["body"])[:text_limit]
        attachments = self.attachments_for_message(
            source=str(result["source"]), message_id=str(result["message_id"]), user_id=user_id
        )
        if attachments:
            result["attachments"] = attachments
        return result

    def latest_incoming_message(self, conversation_id: str, *,
                                user_id: str | None = None) -> dict[str, object] | None:
        """Select the reply source independently of the rendered history page."""
        user = self._user(user_id)
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user)
        with self._lock:
            row = self._connection.execute("""SELECT source,message_id FROM messages
                WHERE user_id=? AND conversation_id=? AND direction='incoming'
                AND deleted_at IS NULL ORDER BY received_at DESC,rowid DESC LIMIT 1""",
                (user, conversation_id)).fetchone()
        return self.message(str(row["message_id"]), source=str(row["source"]), user_id=user) if row else None

    def new_messages(
        self, *, source: str | None = None, limit: int = 50, user_id: str | None = None,
    ) -> list[dict[str, object]]:
        source_sql, values = self._source_filter(source)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT m.source,m.message_id,m.sender,m.body,m.received_at,
                       c.id AS conversation_id,c.identity_id
                FROM messages m JOIN conversations c
                  ON c.user_id=m.user_id AND c.id=m.conversation_id
                WHERE m.user_id=? AND m.direction='incoming' AND m.deleted_at IS NULL
                  AND COALESCE(m.provider_read,m.seen_at IS NOT NULL)=0 {source_sql}
                ORDER BY m.received_at DESC LIMIT ?
                """,
                [self._user(user_id), *values, limit],
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_message_ids(
        self, *, source: str, limit: int = 500, user_id: str | None = None,
    ) -> list[str]:
        if not 1 <= limit <= 1_000:
            raise ValueError("recent message limit must be between 1 and 1000")
        source_sql, values = self._source_filter(source)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT m.message_id FROM messages m JOIN conversations c
                  ON c.user_id=m.user_id AND c.id=m.conversation_id
                WHERE m.user_id=? {source_sql}
                ORDER BY m.received_at DESC LIMIT ?
                """,
                [self._user(user_id), *values, limit],
            ).fetchall()
        return [str(row["message_id"]) for row in rows]

    def workspace_events(
        self, *, after: int = 0, limit: int = 50, user_id: str | None = None,
    ) -> dict[str, object]:
        """Read one user's append-only inbound sequence without changing seen state."""
        if type(after) is not int or after < 0 or after >= 2**63:
            raise ValueError("workspace cursor must be a non-negative integer")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("workspace limit must be between 1 and 100")
        scoped_user = self._user(user_id)
        with self._lock:
            head = self._connection.execute(
                "SELECT COALESCE(MAX(seq),0) FROM workspace_events WHERE user_id=?",
                (scoped_user,),
            ).fetchone()[0]
            rows = self._connection.execute(
                """
                SELECT e.seq,e.source,e.message_id,e.conversation_id,
                       m.sender,m.body,m.received_at,m.direction,m.sender_is_bot,
                       c.route_id,c.account_ref,c.peer_id,c.conversation_kind
                FROM workspace_events AS e
                JOIN messages AS m ON m.user_id=e.user_id
                    AND m.source=e.source AND m.message_id=e.message_id
                JOIN conversations AS c ON c.user_id=e.user_id
                    AND c.id=e.conversation_id
                LEFT JOIN workspace_chat_rules AS wr ON wr.user_id=e.user_id
                    AND wr.conversation_id=e.conversation_id
                LEFT JOIN workspace_policy_defaults AS wd ON wd.user_id=e.user_id
                    AND wd.conversation_kind=c.conversation_kind
                WHERE e.user_id=? AND e.seq>? AND m.direction='incoming' AND e.eligible=1
                  AND NOT EXISTS (SELECT 1 FROM workspace_exclusions wx
                    WHERE wx.user_id=e.user_id AND wx.conversation_id=e.conversation_id)
                  AND (wr.action='allow' OR (wr.action IS NULL AND
                    COALESCE(wd.enabled,CASE WHEN c.conversation_kind='direct' THEN 1 ELSE 0 END)=1))
                ORDER BY e.seq LIMIT ?
                """,
                (scoped_user, after, limit),
            ).fetchall()
        events = [dict(row) for row in rows]
        return {
            "events": events,
            "cursor": events[-1]["seq"] if events else after,
            "head": head,
        }

    def workspace_event(
        self, event_seq: int, *, user_id: str | None = None,
    ) -> dict[str, object]:
        if type(event_seq) is not int or event_seq <= 0:
            raise ValueError("event_seq must be a positive integer")
        user = self._user(user_id)
        with self._lock:
            row = self._connection.execute(
                """SELECT e.seq,e.source,e.message_id,e.conversation_id,e.eligible,e.reconciled,
                          m.sender,m.body,m.received_at,m.direction,m.sender_is_bot,
                          c.route_id,c.account_ref,c.peer_id,c.conversation_kind
                   FROM workspace_events e
                   JOIN messages m ON m.user_id=e.user_id AND m.source=e.source
                     AND m.message_id=e.message_id
                   JOIN conversations c ON c.user_id=e.user_id AND c.id=e.conversation_id
                   WHERE e.user_id=? AND e.seq=?""",
                (user, event_seq),
            ).fetchone()
        if row is None:
            raise KeyError("workspace event not found")
        return dict(row)

    @staticmethod
    def _triage_request_id(value: object) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value.strip()):
            raise ValueError("request_id must be 1-128 safe characters")
        return value.strip()

    @staticmethod
    def _triage_actor(value: object) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 128:
            raise ValueError("actor must be 1-128 characters")
        return value.strip()

    def _workspace_triage_record(self, row: sqlite3.Row) -> dict[str, object]:
        result: dict[str, object] = {
            "event_seq": int(row["event_seq"]), "request_id": str(row["request_id"]),
            "status": str(row["status"]), "attempts": int(row["attempts"]),
            "drafts": [], "retryable": str(row["status"]) == "pending",
            "send_state": str(row["send_state"]),
        }
        if row["result_json"]:
            result["triage"] = json.loads(str(row["result_json"]))
        draft_ids = json.loads(str(row["draft_ids_json"] or "[]"))
        result["drafts"] = [
            {
                "id": draft.id, "body": draft.body, "status": draft.status,
                **({"receipt": draft.receipt} if draft.receipt else {}),
            }
            for draft_id in draft_ids
            for draft in [self.draft(str(draft_id), user_id=str(row["user_id"]))]
        ]
        if row["last_error"]:
            result["last_error"] = str(row["last_error"])
        if row["selected_draft_id"]:
            result["selected_draft_id"] = str(row["selected_draft_id"])
        if row["send_receipt"]:
            result["receipt"] = str(row["send_receipt"])
        if row["feedback_label"]:
            result["feedback"] = {
                "label": str(row["feedback_label"]),
                "actor": str(row["feedback_actor"] or ""),
                "at": float(row["feedback_at"]),
            }
        return result

    def workspace_triage(
        self, *, event_seq: int, request_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, object]:
        user = self._user(user_id)
        if type(event_seq) is not int or event_seq <= 0:
            raise ValueError("event_seq must be a positive integer")
        self.expire_proposed_drafts(user_id=user)
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM workspace_triage WHERE user_id=? AND event_seq=?",
                (user, event_seq),
            ).fetchone()
            if row is None:
                raise KeyError("workspace triage not found")
            if request_id is not None and str(row["request_id"]) != self._triage_request_id(request_id):
                raise ValueError("triage_request_conflict")
            return self._workspace_triage_record(row)

    def begin_workspace_triage(
        self, *, event_seq: int, request_id: str, user_id: str | None = None,
        retry_failed: bool = False,
    ) -> tuple[dict[str, object], bool]:
        user = self._user(user_id)
        request_id = self._triage_request_id(request_id)
        self.workspace_event(event_seq, user_id=user)
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            request_row = self._connection.execute(
                "SELECT event_seq FROM workspace_triage WHERE user_id=? AND request_id=?",
                (user, request_id),
            ).fetchone()
            if request_row is not None and int(request_row["event_seq"]) != event_seq:
                raise ValueError("triage_request_conflict")
            row = self._connection.execute(
                "SELECT * FROM workspace_triage WHERE user_id=? AND event_seq=?",
                (user, event_seq),
            ).fetchone()
            if row is None:
                self._connection.execute(
                    """INSERT INTO workspace_triage
                       (user_id,event_seq,request_id,status,attempts,created_at,updated_at)
                       VALUES (?,?,?,'running',1,?,?)""",
                    (user, event_seq, request_id, now, now),
                )
                active = True
            else:
                if str(row["request_id"]) != request_id:
                    raise ValueError("triage_request_conflict")
                status = str(row["status"])
                if status == "completed" or (
                    status == "review" and not (retry_failed and row["last_error"])
                ):
                    return self._workspace_triage_record(row), False
                if status == "running" and now - float(row["updated_at"]) < 120:
                    return self._workspace_triage_record(row), False
                self._connection.execute(
                    """UPDATE workspace_triage SET status='running',attempts=attempts+1,
                       last_error=NULL,updated_at=? WHERE user_id=? AND event_seq=?""",
                    (now, user, event_seq),
                )
                active = True
            row = self._connection.execute(
                "SELECT * FROM workspace_triage WHERE user_id=? AND event_seq=?", (user, event_seq),
            ).fetchone()
        assert row is not None
        return self._workspace_triage_record(row), active

    def complete_workspace_triage(
        self, *, event_seq: int, request_id: str, result: dict[str, object],
        draft_bodies: list[str], user_id: str | None = None,
        context_snapshot: dict | None = None,
    ) -> dict[str, object]:
        user = self._user(user_id)
        request_id = self._triage_request_id(request_id)
        event = self.workspace_event(event_seq, user_id=user)
        if len(draft_bodies) > 2 or any(not body.strip() or len(body.strip()) > 2000 for body in draft_bodies):
            raise ValueError("triage drafts must contain at most two bounded bodies")
        now = time.time()
        draft_ids = [
            "draft_triage_" + hashlib.sha256(
                f"{user}\0{event_seq}\0{request_id}\0{index}\0{body.strip()}".encode()
            ).hexdigest()[:24]
            for index, body in enumerate(draft_bodies)
        ]
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT request_id,status FROM workspace_triage WHERE user_id=? AND event_seq=?",
                (user, event_seq),
            ).fetchone()
            if row is None or str(row["request_id"]) != request_id:
                raise ValueError("triage_request_conflict")
            if str(row["status"]) == "completed":
                return self.workspace_triage(event_seq=event_seq, request_id=request_id, user_id=user)
            for draft_id, body in zip(draft_ids, draft_bodies):
                self._connection.execute(
                    """INSERT OR IGNORE INTO drafts
                       (user_id,id,conversation_id,body,status,created_at)
                       VALUES (?,?,?,?, 'proposed', ?)""",
                    (user, draft_id, str(event["conversation_id"]), body.strip(), now),
                )
                if context_snapshot is not None:
                    self._connection.execute("INSERT OR IGNORE INTO draft_context_snapshots VALUES (?,?,?,?)",
                        (user, draft_id, str(event["conversation_id"]), json.dumps(context_snapshot, ensure_ascii=False)))
            self._connection.execute(
                """UPDATE workspace_triage SET status='completed',result_json=?,draft_ids_json=?,
                   last_error=NULL,updated_at=? WHERE user_id=? AND event_seq=?""",
                (
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(draft_ids, separators=(",", ":")), now, user, event_seq,
                ),
            )
        return self.workspace_triage(event_seq=event_seq, request_id=request_id, user_id=user)

    def fail_workspace_triage(
        self, *, event_seq: int, request_id: str, error: str,
        user_id: str | None = None,
    ) -> dict[str, object]:
        user = self._user(user_id)
        request_id = self._triage_request_id(request_id)
        with self._lock, self._connection:
            row = self._connection.execute(
                """SELECT attempts FROM workspace_triage
                   WHERE user_id=? AND event_seq=? AND request_id=? AND status='running'""",
                (user, event_seq, request_id),
            ).fetchone()
            if row is None:
                raise ValueError("triage is not running")
            status = "pending" if int(row["attempts"]) < 3 else "review"
            changed = self._connection.execute(
                """UPDATE workspace_triage SET status=?,last_error=?,updated_at=?
                   WHERE user_id=? AND event_seq=? AND request_id=? AND status='running'""",
                (status, str(error)[:500], time.time(), user, event_seq, request_id),
            ).rowcount
        if changed != 1:
            raise ValueError("triage is not running")
        return self.workspace_triage(event_seq=event_seq, request_id=request_id, user_id=user)

    def claim_workspace_triage_send(
        self, *, event_seq: int, request_id: str, draft_id: str, actor: str,
        user_id: str | None = None,
    ) -> dict[str, object]:
        user = self._user(user_id)
        request_id, actor = self._triage_request_id(request_id), self._triage_actor(actor)
        self.expire_proposed_drafts(user_id=user)
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT * FROM workspace_triage WHERE user_id=? AND event_seq=?",
                (user, event_seq),
            ).fetchone()
            if row is None or str(row["request_id"]) != request_id:
                raise ValueError("triage_request_conflict")
            if str(row["status"]) != "completed":
                raise ValueError("triage is not completed")
            result = json.loads(str(row["result_json"] or "{}"))
            if result.get("decision") not in {"notify", "review"}:
                raise ValueError("triage is not notifyable")
            if draft_id not in json.loads(str(row["draft_ids_json"] or "[]")):
                raise ValueError("triage_draft_mismatch")
            selected = str(row["selected_draft_id"] or "")
            if selected and selected != draft_id:
                raise ValueError("triage_choice_conflict")
            state = str(row["send_state"])
            if state == "sent":
                return self._workspace_triage_record(row)
            if state == "sending":
                raise ValueError("delivery_outcome_uncertain")
            self._connection.execute(
                """UPDATE workspace_triage SET send_state='sending',selected_draft_id=?,
                   send_actor=?,updated_at=? WHERE user_id=? AND event_seq=?""",
                (draft_id, actor, time.time(), user, event_seq),
            )
            row = self._connection.execute(
                "SELECT * FROM workspace_triage WHERE user_id=? AND event_seq=?", (user, event_seq),
            ).fetchone()
        assert row is not None
        return self._workspace_triage_record(row)

    def release_workspace_triage_send(
        self, *, event_seq: int, request_id: str, draft_id: str,
        user_id: str | None = None,
    ) -> dict[str, object]:
        """Give a claimed triage send back after a pre-submission refusal.

        Only a claim that is still in flight and still names this exact draft
        may be released, so a concurrent real send is never unmarked.
        """
        user = self._user(user_id)
        request_id = self._triage_request_id(request_id)
        with self._lock, self._connection:
            changed = self._connection.execute(
                """UPDATE workspace_triage SET send_state='none',updated_at=?
                   WHERE user_id=? AND event_seq=? AND request_id=?
                     AND selected_draft_id=? AND send_state='sending'""",
                (time.time(), user, event_seq, request_id, draft_id),
            ).rowcount
        if changed != 1:
            raise ValueError("triage send claim changed during release")
        return self.workspace_triage(event_seq=event_seq, request_id=request_id, user_id=user)

    def complete_workspace_triage_send(
        self, *, event_seq: int, request_id: str, draft_id: str, receipt: str,
        user_id: str | None = None,
    ) -> dict[str, object]:
        user = self._user(user_id)
        request_id = self._triage_request_id(request_id)
        with self._lock, self._connection:
            changed = self._connection.execute(
                """UPDATE workspace_triage SET send_state='sent',send_receipt=?,updated_at=?
                   WHERE user_id=? AND event_seq=? AND request_id=?
                     AND selected_draft_id=? AND send_state='sending'""",
                (str(receipt)[:2000], time.time(), user, event_seq, request_id, draft_id),
            ).rowcount
        if changed != 1:
            current = self.workspace_triage(event_seq=event_seq, request_id=request_id, user_id=user)
            if current.get("send_state") != "sent":
                raise ValueError("triage send state conflict")
        return self.workspace_triage(event_seq=event_seq, request_id=request_id, user_id=user)

    def mark_workspace_triage_deep(
        self, *, event_seq: int, request_id: str, actor: str,
        user_id: str | None = None,
    ) -> dict[str, object]:
        user = self._user(user_id)
        request_id, actor = self._triage_request_id(request_id), self._triage_actor(actor)
        with self._lock, self._connection:
            changed = self._connection.execute(
                """UPDATE workspace_triage SET deep_actor=?,deep_requested_at=COALESCE(deep_requested_at,?),
                   updated_at=? WHERE user_id=? AND event_seq=? AND request_id=?
                     AND status IN ('completed','review')""",
                (actor, time.time(), time.time(), user, event_seq, request_id),
            ).rowcount
        if changed != 1:
            raise ValueError("completed triage not found")
        return self.workspace_triage(event_seq=event_seq, request_id=request_id, user_id=user)

    def record_workspace_triage_feedback(
        self, *, event_seq: int, request_id: str, label: str, actor: str,
        user_id: str | None = None,
    ) -> dict[str, object]:
        if label not in {"important", "not_important", "ignore_chat"}:
            raise ValueError("unsupported triage feedback")
        user = self._user(user_id)
        request_id, actor = self._triage_request_id(request_id), self._triage_actor(actor)
        with self._lock, self._connection:
            changed = self._connection.execute(
                """UPDATE workspace_triage SET feedback_label=?,feedback_actor=?,feedback_at=?,updated_at=?
                   WHERE user_id=? AND event_seq=? AND request_id=? AND status='completed'""",
                (label, actor, time.time(), time.time(), user, event_seq, request_id),
            ).rowcount
        if changed != 1:
            raise ValueError("completed triage not found")
        return self.workspace_triage(event_seq=event_seq, request_id=request_id, user_id=user)

    @staticmethod
    def _secretary_session_url(value: object) -> str:
        url = str(value or "").strip()
        if not url:
            return ""
        if len(url) > 1024 or any(ord(char) <= 32 for char in url):
            raise ValueError("invalid Agent Herder session URL")
        parsed = urlsplit(url)
        session_key = parsed.fragment.removeprefix("/session/")
        if (
            parsed.scheme != "https"
            or parsed.netloc != "agent.bezrabotnyi.com"
            or parsed.path not in {"", "/"}
            or parsed.query
            or not parsed.fragment.startswith("/session/")
            or not re.fullmatch(r"(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2}){1,768}", session_key)
        ):
            raise ValueError("invalid Agent Herder session URL")
        return url

    def conversation_secretary(
        self, conversation_id: str, *, user_id: str | None = None,
    ) -> dict[str, object]:
        """Return owner-facing triage and deep-analysis state for one exact chat."""
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user_id)
        user = self._user(user_id)
        with self._lock:
            conversation = self._connection.execute(
                """SELECT id,source,account_ref,peer_id,conversation_kind
                   FROM conversations WHERE user_id=? AND id=?""",
                (user, conversation_id),
            ).fetchone()
            if conversation is None:
                raise KeyError("conversation not found")
            event = self._connection.execute(
                """SELECT e.seq,e.message_id,e.source,m.received_at
                   FROM workspace_events e
                   JOIN messages m ON m.user_id=e.user_id AND m.source=e.source
                     AND m.message_id=e.message_id
                   WHERE e.user_id=? AND e.conversation_id=? AND m.direction='incoming' AND m.deleted_at IS NULL
                   ORDER BY m.received_at DESC,m.rowid DESC LIMIT 1""",
                (user, conversation_id),
            ).fetchone()
            triage_row = None
            job_row = None
            if event is not None:
                triage_row = self._connection.execute(
                    """SELECT wt.* FROM workspace_triage wt
                       JOIN workspace_events we ON we.user_id=wt.user_id
                         AND we.seq=wt.event_seq
                       WHERE wt.user_id=? AND we.conversation_id=?
                       ORDER BY wt.event_seq DESC LIMIT 1""",
                    (user, conversation_id),
                ).fetchone()
                job_row = self._connection.execute(
                    """SELECT * FROM conversation_secretary_jobs
                       WHERE user_id=? AND conversation_id=?
                       ORDER BY created_at DESC,event_seq DESC,attempt DESC LIMIT 1""",
                    (user, conversation_id),
                ).fetchone()
        summary = None
        if event is not None:
            summary = self.conversation_summary(
                conversation_id, through_message_id=str(event["message_id"]),
                through_message_source=str(event["source"]), user_id=user,
            )
        triage = self._workspace_triage_record(triage_row) if triage_row is not None else None
        job = self._secretary_job_record(job_row) if job_row is not None else None
        return {
            "conversation": {
                "id": str(conversation["id"]),
                "source": str(conversation["source"]),
                "account_ref": str(conversation["account_ref"] or ""),
                "peer_id": str(conversation["peer_id"] or ""),
                "conversation_kind": str(conversation["conversation_kind"] or "unknown"),
                **({
                    "event_seq": int(event["seq"]),
                    "message_id": str(event["message_id"]),
                    "message_source": str(event["source"]),
                    "received_at": float(event["received_at"]),
                } if event is not None else {}),
            },
            "triage": triage,
            "summary": str((summary or {}).get("summary") or ""),
            "deep": job,
            "owner_messages_after_current": self.owner_messages_after(
                conversation_id, str(event["message_id"]), user_id=user,
            ) if event is not None else [],
        }

    def owner_messages_after(self, conversation_id: str, message_id: str, *,
                             user_id: str | None = None) -> list[dict[str, object]]:
        """A bounded snapshot of replies the owner already sent, never read authority."""
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user_id)
        user = self._user(user_id)
        with self._lock:
            anchor = self._message_anchor(user, conversation_id, message_id)
            if anchor is None:
                return []
            rows = self._connection.execute("""SELECT message_id,body,received_at,direction FROM messages
                WHERE user_id=? AND conversation_id=? AND direction='outgoing' AND deleted_at IS NULL
                AND (received_at,rowid)>(SELECT received_at,rowid FROM messages WHERE user_id=? AND rowid=?)
                ORDER BY received_at DESC,rowid DESC LIMIT 5""", (user,conversation_id,user,anchor)).fetchall()
        return [dict(row) | {"body": str(row["body"])[:2000]} for row in reversed(rows)]

    @staticmethod
    def _secretary_job_record(row: sqlite3.Row) -> dict[str, object]:
        result: dict[str, object] = {
            "job_id": str(row["job_id"]),
            "conversation_id": str(row["conversation_id"]),
            "event_seq": int(row["event_seq"]),
            "triage_request_id": str(row["triage_request_id"]),
            "actor": str(row["actor"]),
            "source": str(row["source"]),
            "account_ref": str(row["account_ref"]),
            "peer_id": str(row["peer_id"]),
            "message_id": str(row["message_id"]),
            "attempt": int(row["attempt"]),
            "status": str(row["status"]),
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
        }
        for source, target in (
            ("parent_job_id", "parent_job_id"),
            ("session_id", "session_id"), ("session_url", "session_url"),
            ("result_text", "result"), ("last_error", "error"),
            ("worker_id", "worker_id"),
        ):
            value = row[source]
            if value:
                result[target] = str(value)
        return result

    def create_conversation_secretary_job(
        self, *, conversation_id: str, event_seq: int, triage_request_id: str,
        actor: str, identity: dict[str, object] | None = None,
        user_id: str | None = None,
    ) -> dict[str, object]:
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user_id)
        user = self._user(user_id)
        request_id = self._triage_request_id(triage_request_id)
        actor = self._triage_actor(actor)
        event = self.workspace_event(event_seq, user_id=user)
        if str(event["conversation_id"]) != conversation_id:
            raise ValueError("conversation event mismatch")
        snapshot = identity if identity is not None else event
        if str(snapshot.get("source") or "") != str(event["source"]):
            raise ValueError("conversation source mismatch")
        self.workspace_triage(event_seq=event_seq, request_id=request_id, user_id=user)
        now = time.time()
        job_id = "secretary-" + uuid.uuid4().hex
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                """INSERT OR IGNORE INTO conversation_secretary_jobs
                   (user_id,job_id,conversation_id,event_seq,triage_request_id,actor,
                    source,account_ref,peer_id,message_id,status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,'accepted',?,?)""",
                (
                    user, job_id, conversation_id, event_seq, request_id, actor,
                    str(snapshot["source"]), str(snapshot["account_ref"] or ""),
                    str(snapshot["peer_id"] or ""), str(snapshot["message_id"]), now, now,
                ),
            )
            row = self._connection.execute(
                """SELECT * FROM conversation_secretary_jobs
                   WHERE user_id=? AND conversation_id=? AND event_seq=?
                     AND parent_job_id IS NULL""",
                (user, conversation_id, event_seq),
            ).fetchone()
        assert row is not None
        return self._secretary_job_record(row)

    def conversation_secretary_job(
        self, job_id: str, *, user_id: str | None = None,
    ) -> dict[str, object]:
        user = self._user(user_id)
        if not isinstance(job_id, str) or not re.fullmatch(r"secretary-[a-f0-9]{32}", job_id):
            raise ValueError("invalid secretary job id")
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM conversation_secretary_jobs WHERE user_id=? AND job_id=?",
                (user, job_id),
            ).fetchone()
        if row is None:
            raise KeyError("secretary job not found")
        return self._secretary_job_record(row)

    def retry_conversation_secretary_job(
        self, *, job_id: str, actor: str, conversation_id: str,
        user_id: str | None = None,
    ) -> dict[str, object]:
        """Create or reuse one immutable successor for a failed attempt."""
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user_id)
        user = self._user(user_id)
        if not isinstance(job_id, str) or not re.fullmatch(r"secretary-[a-f0-9]{32}", job_id):
            raise ValueError("invalid secretary retry job id")
        actor = self._triage_actor(actor)
        now = time.time()
        successor_id = "secretary-" + uuid.uuid4().hex
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            parent = self._connection.execute(
                """SELECT * FROM conversation_secretary_jobs
                   WHERE user_id=? AND job_id=?""",
                (user, job_id),
            ).fetchone()
            if parent is None:
                raise KeyError("secretary retry job not found")
            if str(parent["conversation_id"]) != conversation_id:
                raise ValueError("secretary retry conversation mismatch")
            if str(parent["status"]) != "failed":
                raise ValueError("only a failed secretary job can be retried")
            self._connection.execute(
                """INSERT OR IGNORE INTO conversation_secretary_jobs
                   (user_id,job_id,conversation_id,event_seq,triage_request_id,actor,
                    source,account_ref,peer_id,message_id,parent_job_id,attempt,
                    status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'accepted',?,?)""",
                (
                    user, successor_id, conversation_id, int(parent["event_seq"]),
                    str(parent["triage_request_id"]), actor, str(parent["source"]),
                    str(parent["account_ref"]), str(parent["peer_id"]),
                    str(parent["message_id"]), job_id, int(parent["attempt"]) + 1,
                    now, now,
                ),
            )
            successor = self._connection.execute(
                """SELECT * FROM conversation_secretary_jobs
                   WHERE user_id=? AND parent_job_id=?""",
                (user, job_id),
            ).fetchone()
        assert successor is not None
        return self._secretary_job_record(successor)

    def conversation_secretary_jobs(
        self, *, status: str = "accepted", limit: int = 20,
        user_id: str | None = None,
    ) -> list[dict[str, object]]:
        user = self._user(user_id)
        if status not in {"accepted", "running", "completed", "failed"}:
            raise ValueError("unsupported secretary job status")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("secretary job limit must be between 1 and 100")
        with self._lock:
            rows = self._connection.execute(
                """SELECT * FROM conversation_secretary_jobs
                   WHERE user_id=? AND status=? ORDER BY created_at LIMIT ?""",
                (user, status, limit),
            ).fetchall()
        return [self._secretary_job_record(row) for row in rows]

    def update_conversation_secretary_job(
        self, *, job_id: str, phase: str, worker_id: str = "",
        session_id: str = "", session_url: str = "", result: str = "",
        error: str = "", user_id: str | None = None,
    ) -> dict[str, object]:
        user = self._user(user_id)
        if not isinstance(job_id, str) or not re.fullmatch(r"secretary-[a-f0-9]{32}", job_id):
            raise ValueError("invalid secretary job id")
        if phase not in {"accepted", "running", "completed", "failed"}:
            raise ValueError("unsupported secretary job phase")
        worker = str(worker_id or "").strip()
        if not worker or len(worker) > 128:
            raise ValueError("worker_id must be 1-128 characters")
        clean_session_id = str(session_id or "").strip()
        if len(clean_session_id) > 512 or any(ord(char) < 32 for char in clean_session_id):
            raise ValueError("invalid Agent Herder session id")
        clean_url = self._secretary_session_url(session_url)
        clean_result = str(result or "").strip()
        clean_error = str(error or "").strip()
        if len(clean_result) > 20_000 or len(clean_error) > 2_000:
            raise ValueError("secretary job result is too large")
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT * FROM conversation_secretary_jobs WHERE user_id=? AND job_id=?",
                (user, job_id),
            ).fetchone()
            if row is None:
                raise KeyError("secretary job not found")
            current = str(row["status"])
            allowed = {
                "accepted": {"accepted", "running", "completed", "failed"},
                "running": {"running", "completed", "failed"},
                "completed": {"completed"},
                "failed": {"failed"},
            }
            if phase not in allowed[current]:
                raise ValueError("secretary job phase regression")
            previous_worker = str(row["worker_id"] or "")
            if not previous_worker and phase != "accepted":
                raise ValueError("secretary job must be claimed before progress")
            if previous_worker and worker and previous_worker != worker:
                raise ValueError("secretary job worker binding is immutable")
            previous_session_id = str(row["session_id"] or "")
            previous_url = str(row["session_url"] or "")
            if previous_session_id and clean_session_id and previous_session_id != clean_session_id:
                raise ValueError("Agent Herder session binding is immutable")
            if previous_url and clean_url and previous_url != clean_url:
                raise ValueError("Agent Herder session URL is immutable")
            final_session_id = previous_session_id or clean_session_id
            final_url = previous_url or clean_url
            previous_result = str(row["result_text"] or "")
            previous_error = str(row["last_error"] or "")
            if previous_result and clean_result and previous_result != clean_result:
                raise ValueError("secretary job result is immutable")
            if previous_error and clean_error and previous_error != clean_error:
                raise ValueError("secretary job error is immutable")
            final_result = previous_result or clean_result
            final_error = previous_error or clean_error
            if phase == "accepted":
                if clean_session_id or clean_url or clean_result or clean_error:
                    raise ValueError("secretary job claim accepts only worker_id")
            elif phase == "running":
                if not clean_session_id or not clean_url:
                    raise ValueError("running secretary job requires session_id and session_url")
                if clean_result or clean_error:
                    raise ValueError("running secretary job cannot include a terminal result")
            elif phase == "completed":
                if not previous_session_id or not previous_url:
                    raise ValueError("completed secretary job requires an earlier running session")
                if not final_result or clean_error:
                    raise ValueError("completed secretary job requires a result only")
            elif not final_error or clean_result:
                raise ValueError("failed secretary job requires an error only")
            if phase == "failed" and bool(clean_session_id) != bool(clean_url):
                raise ValueError("failed secretary job session binding must include id and URL")
            self._connection.execute(
                """UPDATE conversation_secretary_jobs
                   SET status=?,worker_id=?,
                       session_id=?,session_url=?,result_text=?,last_error=?,updated_at=?
                   WHERE user_id=? AND job_id=?""",
                (phase, previous_worker or worker, final_session_id, final_url, final_result, final_error,
                 time.time(), user, job_id),
            )
            updated = self._connection.execute(
                "SELECT * FROM conversation_secretary_jobs WHERE user_id=? AND job_id=?",
                (user, job_id),
            ).fetchone()
        assert updated is not None
        return self._secretary_job_record(updated)

    @staticmethod
    def _workspace_worker(worker_id: object) -> str:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id is required")
        worker = worker_id.strip()
        if len(worker) > 128:
            raise ValueError("worker_id must be at most 128 characters")
        return worker

    @staticmethod
    def _workspace_lease_seconds(value: object) -> int:
        if type(value) is not int or not 30 <= value <= 3600:
            raise ValueError("lease_seconds must be between 30 and 3600")
        return value

    @staticmethod
    def _workspace_token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _workspace_conversation_id(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("conversation_id is required")
        conversation_id = value.strip()
        if len(conversation_id) > 128:
            raise ValueError("conversation_id must be at most 128 characters")
        return conversation_id

    def workspace_exclusions(self, *, user_id: str | None = None) -> list[dict[str, object]]:
        """List durable chat exclusions used by all workspace claim workers."""
        scoped_user = self._user(user_id)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT x.conversation_id,x.source,c.sender AS chat_name,x.reason,
                       x.created_at,x.updated_at
                FROM workspace_exclusions AS x
                LEFT JOIN conversations AS c ON c.user_id=x.user_id
                    AND c.id=x.conversation_id
                WHERE x.user_id=?
                ORDER BY x.updated_at DESC,x.conversation_id
                """,
                (scoped_user,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_workspace_exclusion(
        self, *, conversation_id: str, reason: str = "", user_id: str | None = None,
    ) -> dict[str, object]:
        """Exclude a known chat from future workspace claims without deleting it."""
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user_id)
        scoped_user = self._user(user_id)
        conversation_id = self._workspace_conversation_id(conversation_id)
        if not isinstance(reason, str):
            raise ValueError("reason must be a string")
        reason = reason.strip()
        if len(reason) > 500:
            raise ValueError("reason must be at most 500 characters")
        now = time.time()
        with self._lock, self._connection:
            conversation = self._connection.execute(
                "SELECT source FROM conversations WHERE user_id=? AND id=?",
                (scoped_user, conversation_id),
            ).fetchone()
            if conversation is None:
                raise KeyError("conversation not found")
            self._connection.execute(
                """
                INSERT INTO workspace_exclusions
                    (user_id,conversation_id,source,reason,created_at,updated_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(user_id,conversation_id) DO UPDATE SET
                    source=excluded.source,reason=excluded.reason,
                    updated_at=excluded.updated_at
                """,
                (scoped_user, conversation_id, str(conversation["source"]), reason, now, now),
            )
            self._connection.execute("""INSERT INTO workspace_chat_rules
                (user_id,conversation_id,action,reason,created_at,updated_at)
                VALUES (?,?,'ignore',?,?,?) ON CONFLICT(user_id,conversation_id) DO UPDATE SET
                action='ignore',reason=excluded.reason,updated_at=excluded.updated_at""",
                (scoped_user, conversation_id, reason, now, now))
            self._invalidate_workspace_events_for_chat(scoped_user, conversation_id)
            self._bump_workspace_policy(scoped_user)
        return next(
            item for item in self.workspace_exclusions(user_id=scoped_user)
            if item["conversation_id"] == conversation_id
        )

    def remove_workspace_exclusion(
        self, *, conversation_id: str, user_id: str | None = None,
    ) -> bool:
        """Remove the hard deny and return a chat to its category default."""
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user_id)
        scoped_user = self._user(user_id)
        conversation_id = self._workspace_conversation_id(conversation_id)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM workspace_exclusions WHERE user_id=? AND conversation_id=?",
                (scoped_user, conversation_id),
            )
            self._connection.execute("""DELETE FROM workspace_chat_rules
                WHERE user_id=? AND conversation_id=? AND action='ignore'""",
                (scoped_user, conversation_id))
            if cursor.rowcount:
                self._bump_workspace_policy(scoped_user)
        return cursor.rowcount > 0

    def automatic_ai_paused(self, *, user_id: str | None = None) -> bool:
        return self.user_preference('automatic_ai_paused', user_id=user_id, default='0') == '1'

    def set_automatic_ai_paused(self, paused: bool, *, user_id: str | None = None) -> dict[str, object]:
        if type(paused) is not bool:
            raise ValueError('paused must be a boolean')
        self.set_user_preference('automatic_ai_paused', '1' if paused else '0', user_id=user_id)
        return {'paused': self.automatic_ai_paused(user_id=user_id)}

    def automatic_event_check(self, event_seq: int, *, user_id: str | None = None) -> dict[str, object]:
        """Cheap live facts only. Never refresh summaries, mark read, or invoke a model."""
        user = self._user(user_id)
        event = self.workspace_event(event_seq, user_id=user)
        policy = self.evaluate_workspace_event(
            conversation_id=str(event["conversation_id"]), source=str(event["source"]),
            message_id=str(event["message_id"]), user_id=user)
        with self._lock:
            message = self._connection.execute("""SELECT seen_at,provider_read,deleted_at
                FROM messages WHERE user_id=? AND source=? AND message_id=?""",
                (user,event["source"],event["message_id"])).fetchone()
        reason = ""
        if self.automatic_ai_paused(user_id=user):
            return {"needed": False, "reason": "automatic_ai_paused", "event_seq": event_seq}
        # Trusted provider metadata and stable peers, never keywords in message bodies.
        automated_peers = {"8810909089", "8612415098", "8619283086", "8910872784"}
        if event["sender_is_bot"] or event["source"].split(":",1)[0] == "chatgpt" or (
            event["source"] == "telegram" and str(event["peer_id"]) in automated_peers):
            reason = "automated_source"
        elif message is None or message["deleted_at"] is not None:
            reason = "source_deleted"
        elif message["seen_at"] is not None or message["provider_read"] == 1:
            reason = "already_read"
        elif self.owner_messages_after(str(event["conversation_id"]), str(event["message_id"]), user_id=user):
            reason = "owner_already_replied"
        elif not policy["allowed"]:
            reason = "workspace_policy_denied"
        return {"needed": not reason, "reason": reason, "event_seq": event_seq}

    def automatic_batch_events(self, event_seq: int, *, user_id: str | None = None) -> list[dict[str, object]]:
        user = self._user(user_id)
        with self._lock:
            rows = self._connection.execute("SELECT event_seq FROM workspace_batches WHERE user_id=? AND leader_seq=? ORDER BY event_seq",
                (user,event_seq)).fetchall()
        sequences = [int(r["event_seq"]) for r in rows] or [event_seq]
        return [self.workspace_event(seq, user_id=user) for seq in sequences
                if self.automatic_event_check(seq, user_id=user)["needed"]]

    def _prepare_automatic_batch(self, *, user: str, after: int, now: float, direct_only: bool) -> int | None:
        if self.automatic_ai_paused(user_id=user):
            return None
        # Called inside the same BEGIN IMMEDIATE as the lease. Active/terminal
        # claims never join a new batch. Failed leaders retain their exact membership.
        rows = self._connection.execute("""SELECT e.seq,e.conversation_id,e.queued_at,
                wc.status,wc.lease_expires_at
            FROM workspace_events e LEFT JOIN workspace_claims wc
              ON wc.user_id=e.user_id AND wc.event_seq=e.seq
            JOIN conversations c ON c.user_id=e.user_id AND c.id=e.conversation_id
            WHERE e.user_id=? AND e.seq>? AND e.eligible=1
              AND (?=0 OR e.source!='telegram' OR c.conversation_kind='direct')
              AND (wc.event_seq IS NULL OR wc.status='failed' OR
                (wc.status='claimed' AND wc.lease_expires_at<=?))
            ORDER BY e.seq LIMIT 1000""", (user,after,int(direct_only),now)).fetchall()
        pending = []
        for row in rows:
            check = self.automatic_event_check(int(row["seq"]), user_id=user)
            if not check["needed"]:
                self._connection.execute("UPDATE workspace_events SET eligible=0 WHERE user_id=? AND seq=?", (user,row["seq"]))
                continue
            pending.append(row)
        first = next((r for r in pending if float(r["queued_at"]) <= now-300), None)
        if first is None:
            return None
        old = self._connection.execute("SELECT 1 FROM workspace_batches WHERE user_id=? AND leader_seq=?", (user,first["seq"])).fetchone()
        if old or first["status"] is not None:
            return int(first["seq"])
        # Fixed five minutes from the first arrival; newer traffic cannot postpone it.
        batch = [r for r in pending if r["conversation_id"] == first["conversation_id"] and r["status"] is None][:100]
        if not batch:
            return int(first["seq"])
        leader = int(batch[-1]["seq"])
        for member in batch:
            seq = int(member["seq"])
            self._connection.execute("INSERT OR IGNORE INTO workspace_batches VALUES (?,?,?)", (user,leader,seq))
            if seq != leader:
                self._connection.execute("""INSERT INTO workspace_claims
                    (user_id,event_seq,worker_id,lease_token_hash,status,lease_expires_at,attempts,claimed_at,updated_at,completed_at,last_error)
                    VALUES (?,?,'batch','', 'completed',?,0,?,?,?,?)""",
                    (user,seq,now,now,now,now,f"coalesced into event {leader}"))
        return leader

    def claim_workspace_event(
        self, *, worker_id: str, lease_seconds: int = 600,
        after: int = 0, telegram_direct_only: bool = False, automatic: bool = False,
        user_id: str | None = None,
    ) -> dict[str, object] | None:
        """Atomically lease the oldest available inbound event to one worker."""
        worker = self._workspace_worker(worker_id)
        lease_seconds = self._workspace_lease_seconds(lease_seconds)
        if type(after) is not int or after < 0 or after >= 2**63:
            raise ValueError("workspace cursor must be a non-negative integer")
        if type(telegram_direct_only) is not bool:
            raise ValueError("telegram_direct_only must be a boolean")
        scoped_user = self._user(user_id)
        now = time.time()
        token = "ucl_" + secrets.token_urlsafe(32)
        token_hash = self._workspace_token_hash(token)
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            selected = None
            if automatic:
                selected = self._prepare_automatic_batch(user=scoped_user, after=after, now=now, direct_only=telegram_direct_only)
                if selected is None:
                    return None
            row = self._connection.execute(
                """
                SELECT e.seq,e.source,e.message_id,e.conversation_id,
                       m.sender,m.body,m.received_at,m.direction,m.sender_is_bot,
                       c.route_id,c.account_ref,c.peer_id,c.conversation_kind,
                       wc.status AS claim_status,wc.lease_expires_at,
                       COALESCE(wc.attempts,0) AS prior_attempts
                FROM workspace_events AS e
                JOIN messages AS m ON m.user_id=e.user_id
                    AND m.source=e.source AND m.message_id=e.message_id
                JOIN conversations AS c ON c.user_id=e.user_id
                    AND c.id=e.conversation_id
                LEFT JOIN workspace_claims AS wc ON wc.user_id=e.user_id
                    AND wc.event_seq=e.seq
                LEFT JOIN workspace_chat_rules AS wr ON wr.user_id=e.user_id
                    AND wr.conversation_id=e.conversation_id
                LEFT JOIN workspace_policy_defaults AS wd ON wd.user_id=e.user_id
                    AND wd.conversation_kind=c.conversation_kind
                WHERE e.user_id=? AND e.seq>? AND (? IS NULL OR e.seq=?) AND m.direction='incoming' AND e.eligible=1
                  AND (?=0 OR e.source!='telegram' OR c.conversation_kind='direct')
                  AND NOT EXISTS (
                      SELECT 1 FROM workspace_exclusions AS wx
                      WHERE wx.user_id=e.user_id AND wx.conversation_id=e.conversation_id
                  )
                  AND (wr.action='allow' OR (wr.action IS NULL AND
                    COALESCE(wd.enabled,CASE WHEN c.conversation_kind='direct' THEN 1 ELSE 0 END)=1))
                  AND (wc.event_seq IS NULL OR wc.status='failed'
                       OR (wc.status='claimed' AND wc.lease_expires_at<=?))
                ORDER BY e.seq LIMIT 1
                """,
                (scoped_user, after, selected, selected, int(telegram_direct_only), now),
            ).fetchone()
            if row is None:
                return None
            event = dict(row)
            prior_status = event.pop("claim_status")
            event.pop("lease_expires_at")
            attempts = int(event.pop("prior_attempts")) + 1
            expires_at = now + lease_seconds
            self._connection.execute(
                """
                INSERT INTO workspace_claims
                    (user_id,event_seq,worker_id,lease_token_hash,status,
                     lease_expires_at,attempts,claimed_at,updated_at,
                     completed_at,last_error)
                VALUES (?,?,?,?, 'claimed', ?,?,?,?,NULL,NULL)
                ON CONFLICT(user_id,event_seq) DO UPDATE SET
                    worker_id=excluded.worker_id,
                    lease_token_hash=excluded.lease_token_hash,
                    status='claimed',lease_expires_at=excluded.lease_expires_at,
                    attempts=excluded.attempts,claimed_at=excluded.claimed_at,
                    updated_at=excluded.updated_at,completed_at=NULL,last_error=NULL
                """,
                (scoped_user, event["seq"], worker, token_hash, expires_at,
                 attempts, now, now),
            )
            action = "claimed" if prior_status is None else "reclaimed"
            self._connection.execute(
                """INSERT INTO workspace_claim_log
                   (user_id,event_seq,worker_id,action,at,detail)
                   VALUES (?,?,?,?,?,?)""",
                (scoped_user, event["seq"], worker, action, now,
                 None if prior_status is None else str(prior_status)),
            )
        return {
            "event": event | {
                "batch_messages": self.automatic_batch_events(int(event["seq"]), user_id=scoped_user) if automatic else [],
                "recent_context": self.bounded_conversation_context(
                    str(event["conversation_id"]),
                    current_message_id=str(event["message_id"]),
                    user_id=scoped_user,
                ),
                "cached_summary": str((self.conversation_summary(
                    str(event["conversation_id"]),
                    through_message_id=str(event["message_id"]), user_id=scoped_user,
                ) or {}).get("summary") or ""),
                "context_policy": self.context_settings(user_id=scoped_user),
            },
            "claim": {
                "event_seq": event["seq"], "worker_id": worker,
                "lease_token": token, "status": "claimed",
                "lease_expires_at": expires_at, "attempts": attempts,
            },
        }

    def _workspace_claim_transition(
        self, *, event_seq: int, worker_id: str, lease_token: str,
        action: str, lease_seconds: int | None = None,
        detail: str | None = None, user_id: str | None = None,
    ) -> dict[str, object]:
        if type(event_seq) is not int or event_seq <= 0:
            raise ValueError("event_seq must be a positive integer")
        worker = self._workspace_worker(worker_id)
        if not isinstance(lease_token, str) or not lease_token.startswith("ucl_"):
            raise ValueError("valid lease_token is required")
        if action not in {"renewed", "completed", "failed"}:
            raise ValueError("unsupported workspace claim action")
        if action == "renewed":
            lease_seconds = self._workspace_lease_seconds(
                600 if lease_seconds is None else lease_seconds
            )
        if detail is not None:
            if not isinstance(detail, str):
                raise ValueError("detail must be a string")
            detail = detail.strip()[:2000] or None
        scoped_user = self._user(user_id)
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT * FROM workspace_claims WHERE user_id=? AND event_seq=?",
                (scoped_user, event_seq),
            ).fetchone()
            if row is None:
                raise KeyError("workspace claim not found")
            if str(row["worker_id"]) != worker or not hmac.compare_digest(
                str(row["lease_token_hash"]), self._workspace_token_hash(lease_token)
            ):
                raise PermissionError("workspace claim ownership mismatch")
            if str(row["status"]) != "claimed":
                raise ValueError("workspace claim is not active")
            if float(row["lease_expires_at"]) <= now:
                raise ValueError("workspace claim lease expired")
            status = {
                "renewed": "claimed", "completed": "done", "failed": "failed",
            }[action]
            expires_at = now + int(lease_seconds or 0) if action == "renewed" else now
            completed_at = now if action in {"completed", "failed"} else None
            self._connection.execute(
                """
                UPDATE workspace_claims
                SET status=?,lease_expires_at=?,updated_at=?,completed_at=?,last_error=?
                WHERE user_id=? AND event_seq=?
                """,
                (status, expires_at, now, completed_at,
                 detail if action == "failed" else None, scoped_user, event_seq),
            )
            self._connection.execute(
                """INSERT INTO workspace_claim_log
                   (user_id,event_seq,worker_id,action,at,detail)
                   VALUES (?,?,?,?,?,?)""",
                (scoped_user, event_seq, worker, action, now, detail),
            )
            attempts = int(row["attempts"])
        return {
            "event_seq": event_seq, "worker_id": worker, "status": status,
            "lease_expires_at": expires_at, "attempts": attempts,
            "completed_at": completed_at, "detail": detail,
        }

    def renew_workspace_claim(self, **kwargs: object) -> dict[str, object]:
        return self._workspace_claim_transition(action="renewed", **kwargs)

    def complete_workspace_claim(self, **kwargs: object) -> dict[str, object]:
        return self._workspace_claim_transition(action="completed", **kwargs)

    def fail_workspace_claim(self, **kwargs: object) -> dict[str, object]:
        return self._workspace_claim_transition(action="failed", **kwargs)

    def workspace_claim_log(
        self, *, event_seq: int, user_id: str | None = None,
    ) -> dict[str, object]:
        if type(event_seq) is not int or event_seq <= 0:
            raise ValueError("event_seq must be a positive integer")
        scoped_user = self._user(user_id)
        with self._lock:
            claim = self._connection.execute(
                """SELECT event_seq,worker_id,status,lease_expires_at,attempts,
                          claimed_at,updated_at,completed_at,last_error
                   FROM workspace_claims WHERE user_id=? AND event_seq=?""",
                (scoped_user, event_seq),
            ).fetchone()
            rows = self._connection.execute(
                """SELECT id,event_seq,worker_id,action,at,detail
                   FROM workspace_claim_log WHERE user_id=? AND event_seq=?
                   ORDER BY id""",
                (scoped_user, event_seq),
            ).fetchall()
        if claim is None:
            raise KeyError("workspace claim not found")
        return {"claim": dict(claim), "log": [dict(row) for row in rows]}

    @staticmethod
    def _source_filter(source: str | None) -> tuple[str, list[object]]:
        if not source:
            return "", []
        if source in {"mail", "email", "gmail"}:
            return " AND (c.source IN ('mail','email','gmail') OR c.source LIKE 'gmail:%' OR c.source LIKE 'email:%')", []
        if source == "matrix":
            return " AND (c.source='matrix' OR c.source LIKE 'matrix:%')", []
        return " AND c.source=?", [source]

    def conversations(
        self, *, source: str | None = None, exact_source: bool = False,
        limit: int = 100, user_id: str | None = None,
        account_ref: str | None = None,
        conversation_ids: list[str] | None = None,
    ) -> list[dict[str, object]]:
        if conversation_ids is not None and not conversation_ids:
            return []
        if conversation_ids is not None and len(conversation_ids) > 100:
            raise ValueError('at most 100 conversation IDs')
        if source and exact_source:
            source_sql, values = " AND c.source=?", [source]
        else:
            source_sql, values = self._source_filter(source)
        if account_ref:
            # A Telegram identity shares source='telegram' with every other
            # connected identity. Scope the sidebar selection by account.
            source_sql += " AND c.account_ref=?"
            values.append(account_ref)
        if conversation_ids is not None:
            source_sql += ' AND c.id IN (' + ','.join('?' for _ in conversation_ids) + ')'
            values.extend(conversation_ids)
        # Sort strictly by the latest message timestamp so the UI's "fresh on top"
        # rule never drifts because of bookkeeping fields like updated_at.
        # `preview` is the latest body; if that happens to be an attachment
        # placeholder ([image], [document] …) the service layer swaps in the
        # newest text body via `last_text_bodies_for` so the search and the
        # list item show real content.
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT c.id,c.source,c.sender,c.identity_id,c.updated_at,c.account_ref,c.peer_id,
                       (SELECT body FROM messages WHERE user_id=c.user_id AND conversation_id=c.id AND deleted_at IS NULL
                        ORDER BY received_at DESC,rowid DESC LIMIT 1) AS preview,
                       (SELECT received_at FROM messages WHERE user_id=c.user_id AND conversation_id=c.id AND deleted_at IS NULL
                        ORDER BY received_at DESC,rowid DESC LIMIT 1) AS last_at,
                       COALESCE(
                        CASE WHEN c.source='telegram' THEN trs.unread_count END,
                        (SELECT COUNT(*) FROM messages WHERE user_id=c.user_id
                         AND conversation_id=c.id AND direction='incoming' AND deleted_at IS NULL
                         AND COALESCE(provider_read,seen_at IS NOT NULL)=0)) AS unread_count,
                       CASE WHEN c.source='telegram' AND trs.unread_count IS NOT NULL
                         THEN 'telegram' ELSE 'local_mirror' END AS unread_source,
                       trs.unread_observed_at AS unread_observed_at,
                       CASE WHEN c.source='telegram' AND c.account_ref LIKE 'telegram:%'
                                  AND c.peer_id=substr(c.account_ref,10) THEN 'Избранное'
                         WHEN c.source='telegram' THEN COALESCE(
                           NULLIF((SELECT name FROM contact_names WHERE user_id=c.user_id
                            AND source=c.source AND sender=c.sender AND is_explicit=1),''),
                           NULLIF(trim(COALESCE(sp.first_name,'')||' '||COALESCE(sp.last_name,'')),''),
                           NULLIF('@'||sp.username,'@'), NULLIF(sp.phone,''),
                           NULLIF((SELECT name FROM contact_names WHERE user_id=c.user_id
                            AND source=c.source AND sender=c.sender),''),
                           NULLIF(linked.display_name,''), NULLIF(c.sender,''))
                         ELSE COALESCE(
                           NULLIF((SELECT name FROM contact_names WHERE user_id=c.user_id
                            AND source=c.source AND sender=
                              CASE WHEN c.source IN ('max','phone','vk','yandex') AND c.account_ref!='' AND c.peer_id!='' THEN 'chat:'||c.account_ref||':'||c.peer_id ELSE c.sender END),''),
                           NULLIF(trim(COALESCE(sp.first_name,'')||' '||COALESCE(sp.last_name,'')),''),
                           NULLIF('@'||sp.username,'@'), NULLIF(sp.phone,''),
                           NULLIF(c.sender,'')) END AS display_name,
                       activity.account_last_at AS account_last_at
                FROM conversations c LEFT JOIN telegram_read_state trs
                  ON trs.user_id=c.user_id AND trs.account_ref=c.account_ref
                  AND trs.peer_id=c.peer_id AND c.source='telegram'
                LEFT JOIN search_profiles sp ON sp.user_id=c.user_id AND sp.conversation_id=c.id
                LEFT JOIN provider_accounts linked ON linked.user_id=c.user_id
                  AND linked.provider='telegram' AND linked.id='telegram:'||c.peer_id
                -- Aggregate once per account source. The old scalar MAX() was
                -- executed for every chat row (many full source scans) and
                -- made switching accounts take several seconds.
                LEFT JOIN (
                    SELECT user_id, source, MAX(received_at) AS account_last_at
                    FROM messages WHERE user_id=?
                    GROUP BY user_id, source
                ) AS activity ON activity.user_id=c.user_id AND activity.source=c.source
                WHERE c.user_id=? {source_sql}
                  AND NOT EXISTS (SELECT 1 FROM phone_conversation_aliases a WHERE a.user_id=c.user_id AND a.old_id=c.id)
                ORDER BY last_at DESC, c.updated_at DESC LIMIT ?
                """,
                [self._user(user_id), self._user(user_id), *values, limit],
            ).fetchall()
        return [dict(row) for row in rows]

    def last_text_bodies_for(
        self, conversation_ids: list[str], *, user_id: str | None = None,
    ) -> dict[str, str]:
        """For each conversation_id return the newest non-placeholder body, or ''.

        Walks at most 30 most recent messages per conversation. Attachment
        placeholders ([image], [video], [document] and their Russian siblings)
        never count as text, so callers can substitute them for the placeholder
        that `conversations()` returns in the `preview` column.
        """
        if not conversation_ids:
            return {}
        placeholders = ",".join("?" for _ in conversation_ids)
        params: list[object] = [self._user(user_id), *conversation_ids]
        query = f"""
            SELECT conversation_id, body, received_at
            FROM (
              SELECT conversation_id, body, received_at,
                     ROW_NUMBER() OVER (
                       PARTITION BY conversation_id ORDER BY received_at DESC
                     ) AS rn
              FROM messages
              WHERE user_id=?
                AND conversation_id IN ({placeholders}) AND deleted_at IS NULL
            ) WHERE rn <= 30
            ORDER BY conversation_id, received_at DESC
        """
        out: dict[str, str] = {}
        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
        for row in rows:
            cid = str(row["conversation_id"])
            body = str(row["body"])
            # Placeholder bodies like "[image]" still match `body NOT LIKE '[[]%]'`
            # because of how SQLite expands the wildcard; skip them explicitly.
            if body.startswith("[") and body.endswith("]"):
                continue
            out.setdefault(cid, body)
        return out

    def read_cursor(self, conversation_id: str, message_id: str, *, user_id: str | None = None) -> dict | None:
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user_id)
        with self._lock:
            row = self._connection.execute("SELECT rowid,received_at,direction FROM messages WHERE user_id=? AND conversation_id=? AND message_id=? AND deleted_at IS NULL", (self._user(user_id), conversation_id, message_id)).fetchone()
            return dict(row) if row is not None else None

    def mark_read_through(self, conversation_id: str, message_id: str, *, user_id: str | None = None) -> int:
        conversation_id = self.resolve_conversation_id(conversation_id, user_id=user_id)
        cursor = self.read_cursor(conversation_id, message_id, user_id=user_id)
        if cursor is None: raise ValueError("message does not belong to this conversation")
        with self._lock, self._connection:
            return self._connection.execute("""UPDATE messages SET seen_at=COALESCE(seen_at,?)
                WHERE user_id=? AND conversation_id=? AND deleted_at IS NULL AND direction!='outgoing'
                AND (received_at<? OR (received_at=? AND rowid<=?))
                AND seen_at IS NULL""", (time.time(),self._user(user_id),conversation_id,cursor['received_at'],cursor['received_at'],cursor['rowid'])).rowcount

    def mark_seen(
        self, *, source: str, message_id: str, user_id: str | None = None
    ) -> bool:
        with self._lock, self._connection:
            return self._connection.execute(
                """
                UPDATE messages SET seen_at=?
                WHERE user_id=? AND source=? AND message_id=? AND seen_at IS NULL
                """,
                (time.time(), self._user(user_id), source, message_id),
            ).rowcount == 1
