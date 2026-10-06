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

from .contracts import ConversationPolicy, InboxMessage, ReplyDraft, UserPrincipal


_ITERATIONS = 310_000
_USERNAME = re.compile(r"[A-Za-z0-9_.@+-]{3,64}")
_DATA_TABLES = ("conversations", "messages", "drafts", "identities", "reply_rules", "provider_accounts")
CONTEXT_MESSAGE_COUNT_DEFAULT = 3
CONTEXT_MESSAGE_COUNT_MAX = 20
CONTEXT_TOKEN_BUDGET_DEFAULT = 1000
CONTEXT_TOKEN_BUDGET_MAX = 8_000


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


class SQLiteUserIOStore:
    def __init__(self, path: str | Path) -> None:
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock, self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._auth_schema()
            self._bootstrap_owner()
            if self._is_legacy() or self._table_exists("legacy_conversations"):
                self._migrate_legacy()
            self._data_schema()
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
            self._backfill_workspace_events()

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
                updated_at REAL NOT NULL
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
            """
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
        ):
            if name not in columns:
                self._connection.execute(f"ALTER TABLE api_tokens ADD COLUMN {name} {definition}")

    def _bootstrap_owner(self) -> None:
        row = self._connection.execute(
            "SELECT id FROM users WHERE role='owner' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            salt, digest = self._digest(secrets.token_urlsafe(32))
            owner_id, now = "user_owner", time.time()
            self._connection.execute(
                "INSERT INTO users VALUES (?,?,?,?,?,?,?,?)",
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
                name TEXT NOT NULL,updated_at REAL NOT NULL,
                PRIMARY KEY(user_id,source,sender)
            );
            CREATE TABLE IF NOT EXISTS drafts (
                user_id TEXT NOT NULL,id TEXT NOT NULL,conversation_id TEXT NOT NULL,
                body TEXT NOT NULL,status TEXT NOT NULL,created_at REAL NOT NULL,
                approved_at REAL,outbox_receipt TEXT,browser_notified_at REAL,PRIMARY KEY(user_id,id)
            );
            CREATE INDEX IF NOT EXISTS drafts_conversation_idx
                ON drafts(user_id,conversation_id,created_at);
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
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(user_id,key)
            );
        """
        for statement in script.split(";"):
            if statement.strip():
                self._connection.execute(statement)
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
        try:
            self._connection.execute("ALTER TABLE drafts ADD COLUMN browser_notified_at REAL")
        except sqlite3.OperationalError:  # column already exists
            pass

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
                    "INSERT INTO users VALUES (?,?,?,?,?,?,?,?)",
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
        return UserPrincipal(str(row["id"]), str(row["username"]), str(row["role"]))

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
                SELECT u.id,u.username,u.role FROM api_tokens t JOIN users u ON u.id=t.user_id
                WHERE t.token_hash=? AND t.revoked_at IS NULL AND t.token_type='access'
                  AND (t.expires_at IS NULL OR t.expires_at>?)
                """,
                (self._token_hash(token), time.time()),
            ).fetchone()
        return None if row is None else UserPrincipal(
            str(row["id"]), str(row["username"]), str(row["role"])
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
        public_source = "mail" if source in {"mail", "email", "gmail"} or source.startswith("gmail:") else source
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

    def latest_message_at(self, source: str, *, user_id: str | None = None) -> float | None:
        """Return message freshness for one exact provider source."""
        with self._lock:
            row = self._connection.execute(
                "SELECT MAX(received_at) AS latest FROM messages WHERE user_id=? AND source=?",
                (self._user(user_id), source),
            ).fetchone()
        return None if row is None or row["latest"] is None else float(row["latest"])

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

    def conversation_id_for_key(self, conversation_key: str, *, user_id: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT id FROM conversations WHERE user_id=? AND conversation_key=?",
                (user_id, conversation_key),
            ).fetchone()
        return None if row is None else str(row["id"])

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
            message.source.startswith("gmail:") or message.source.startswith("chatgpt:")):
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
                WHERE {' AND '.join(filters)} ORDER BY c.updated_at DESC,c.id LIMIT ? OFFSET ?""",
                (*values, limit, offset)).fetchall()
        defaults = self.workspace_policy(user_id=user)["defaults"]
        result = []
        for row in rows:
            chat = dict(row)
            chat["allowed"] = chat["action"] == "allow" or (
                chat["action"] == "inherit" and defaults[chat["conversation_kind"]])
            chat["effective_action"] = "allow" if chat["allowed"] else "ignore"
            result.append(chat)
        return result

    def evaluate_workspace_chat(
        self, *, conversation_id: str, user_id: str | None = None,
    ) -> dict[str, object]:
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
        return {**row, "allowed": bool(allowed and not excluded),
                "policy_revision": defaults["revision"]}

    def evaluate_workspace_event(
        self, *, conversation_id: str, source: str, message_id: str,
        user_id: str | None = None,
    ) -> dict[str, object]:
        user = self._user(user_id)
        chat = self.evaluate_workspace_chat(conversation_id=conversation_id, user_id=user)
        with self._lock:
            row = self._connection.execute("""SELECT eligible FROM workspace_events
                WHERE user_id=? AND conversation_id=? AND source=? AND message_id=?""",
                (user, conversation_id, source, message_id)).fetchone()
        eligible = row is not None and bool(row["eligible"])
        return {**chat, "eligible_at_ingest": eligible,
                "allowed": bool(chat["allowed"] and eligible)}

    def set_workspace_chat_rule(
        self, *, conversation_id: str, action: str, reason: str = "",
        user_id: str | None = None,
    ) -> dict[str, object]:
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
            # The connector's account-scoped ID replaces the old peer:message
            # format. A reconciliation replay of an existing legacy message in
            # this exact conversation is the same message, not a new arrival.
            if message.source == "telegram" and account_ref and message.message_id.startswith(
                f"{account_ref}|"
            ):
                legacy_id = message.message_id[len(account_ref) + 1:]
                legacy = self._connection.execute("""SELECT 1 FROM messages
                    WHERE user_id=? AND source='telegram' AND message_id=?
                      AND conversation_id=?""",
                    (user_id, legacy_id, conversation_id)).fetchone()
                if legacy is not None:
                    if message.sender_is_bot:
                        self._connection.execute("""UPDATE messages SET sender_is_bot=1
                            WHERE user_id=? AND source='telegram' AND message_id=?""",
                            (user_id, legacy_id))
                        self._connection.execute("""UPDATE workspace_events SET eligible=0
                            WHERE user_id=? AND source='telegram' AND message_id=?""",
                            (user_id, legacy_id))
                    return False
            inserted = self._connection.execute(
                """
                INSERT OR IGNORE INTO messages
                (user_id,source,message_id,conversation_id,sender,body,direction,received_at,sender_is_bot)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    user_id, message.source, message.message_id, conversation_id,
                    message.sender, message.body, message.direction, message.received_at,
                    int(message.sender_is_bot),
                ),
            ).rowcount == 1
            if inserted and message.direction == "incoming":
                bot_enabled = bool(self.workspace_policy(user_id=user_id)["defaults"]["telegram_bot"])
                eligible = ((not message.reconciliation)
                    and (not message.sender_is_bot or bot_enabled)
                    and self.evaluate_workspace_chat(
                        conversation_id=conversation_id, user_id=user_id)["allowed"])
                self._connection.execute(
                    """
                    INSERT INTO workspace_events(user_id,source,message_id,conversation_id,eligible,reconciled)
                    VALUES (?,?,?,?,?,?)
                    """,
                    (user_id, message.source, message.message_id, conversation_id,
                     int(eligible), int(message.reconciliation)),
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
            if getattr(message, "sender_name", ""):
                self._connection.execute(
                    """
                    INSERT OR REPLACE INTO contact_names
                    (user_id,source,sender,name,updated_at) VALUES (?,?,?,?,?)
                    """,
                    (user_id, message.source, message.sender, message.sender_name, now),
                )
            for att in (message.attachments or ()):
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
                new_body = str(message.body or "").strip()
                old_placeholder = (not old_body.strip()) or (
                    old_body.strip().startswith("[") and old_body.strip().endswith("]")
                )
                new_is_text = bool(new_body) and not (new_body.startswith("[") and new_body.endswith("]"))
                if old_placeholder and new_is_text:
                    self._connection.execute(
                        "UPDATE messages SET body=? WHERE user_id=? AND source=? AND message_id=?",
                        (new_body, user_id, message.source, message.message_id),
                    )
                    self._connection.execute(
                        "UPDATE conversations SET updated_at=? WHERE user_id=? AND id=?",
                        (now, user_id, conversation_id),
                    )
                elif new_is_text and new_body != old_body:
                    edited_at = float(message.edited_at or 0.0) or now
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

    def create_conversation(
        self, source: str, sender: str, *, user_id: str | None = None
    ) -> dict[str, object]:
        """Create (or return the existing) empty conversation for source+sender.

        Lets the dashboard start a chat — e.g. a new SMS thread — before any
        inbound message exists.
        """
        user_id = self._user(user_id)
        source, sender = source.strip(), sender.strip()
        if not source or not sender:
            raise ValueError("source and sender are required")
        message_key = "email:" + sender.casefold() if source in {"mail", "email", "gmail"} or source.startswith("gmail:") else f"{source}:{sender}"
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
                (user_id, conversation_id, message_key, source, source, sender, time.time()),
            )
        record = self.conversation(conversation_id, user_id=user_id)
        assert record is not None
        return record

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
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO drafts(user_id,id,conversation_id,body,status,created_at) VALUES (?,?,?,?,?,?)",
                (self._user(user_id), draft.id, draft.conversation_id, draft.body, draft.status, time.time()),
            )

    def update_draft(self, draft_id: str, *, body: str, user_id: str | None = None) -> ReplyDraft:
        text, user_id = body.strip(), self._user(user_id)
        if not text:
            raise ValueError("draft body is required")
        with self._lock, self._connection:
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
                "UPDATE drafts SET status='sending' WHERE user_id=? AND id=?",
                (user_id, draft_id),
            )
            return draft

    def release_draft_send(self, draft_id: str, *, user_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE drafts SET status='proposed' WHERE user_id=? AND id=? AND status='sending'",
                (user_id, draft_id),
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
                UPDATE drafts SET status='approved',approved_at=?,outbox_receipt=?
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
        limit = max(1, min(int(limit), 200))
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT d.id,d.conversation_id,d.body,d.status,d.created_at,
                       c.source,c.sender,c.identity_id,
                       COALESCE((SELECT name FROM contact_names n
                                 WHERE n.user_id=c.user_id AND n.source=c.source AND n.sender=c.sender), '') AS display_name
                FROM drafts d JOIN conversations c
                  ON c.user_id=d.user_id AND c.id=d.conversation_id
                WHERE d.user_id=? AND d.status='proposed'
                ORDER BY d.created_at DESC LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def conversation(
        self, conversation_id: str, *, user_id: str | None = None, text_limit: int | None = None
    ) -> dict[str, object] | None:
        user_id = self._user(user_id)
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM conversations WHERE user_id=? AND id=?", (user_id, conversation_id)
            ).fetchone()
            if row is None:
                return None
            messages = self._connection.execute(
                """
                SELECT * FROM (
                    SELECT source,message_id,sender,body,direction,received_at,seen_at,edited_at
                    FROM messages
                    WHERE user_id=? AND conversation_id=? ORDER BY received_at DESC LIMIT 200
                ) ORDER BY received_at
                """,
                (user_id, conversation_id),
            ).fetchall()
            drafts = self._connection.execute(
                """
                SELECT id,body,status,outbox_receipt FROM drafts
                WHERE user_id=? AND conversation_id=? ORDER BY created_at
                """,
                (user_id, conversation_id),
            ).fetchall()
            name_row = self._connection.execute(
                "SELECT name FROM contact_names WHERE user_id=? AND source=? AND sender=?",
                (user_id, row["source"], row["sender"]),
            ).fetchone()
        message_records = [dict(item) for item in messages]
        for item in message_records:
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
            "display_name": str(name_row["name"]) if name_row else "",
            "messages": message_records, "drafts": [dict(item) for item in drafts],
        }

    def set_conversation_account(
        self, conversation_id: str, account_ref: str, *, user_id: str | None = None
    ) -> bool:
        """Pin the reply account; a pending approval pins its existing account."""
        user_id = self._user(user_id)
        with self._lock, self._connection:
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

    def context_settings(self, *, user_id: str | None = None) -> dict[str, int]:
        user = self._user(user_id)

        def value(key: str, default: int, maximum: int) -> int:
            raw = self.user_preference(key, user_id=user, default=str(default))
            try:
                return max(0, min(int(str(raw)), maximum))
            except ValueError:
                return default

        return {
            "message_count": value(
                "context_message_count", CONTEXT_MESSAGE_COUNT_DEFAULT,
                CONTEXT_MESSAGE_COUNT_MAX,
            ),
            "token_budget": value(
                "context_token_budget", CONTEXT_TOKEN_BUDGET_DEFAULT,
                CONTEXT_TOKEN_BUDGET_MAX,
            ),
        }

    def set_context_settings(
        self, *, message_count: int, token_budget: int,
        user_id: str | None = None,
    ) -> dict[str, int]:
        for name, setting, maximum in (
            ("message_count", message_count, CONTEXT_MESSAGE_COUNT_MAX),
            ("token_budget", token_budget, CONTEXT_TOKEN_BUDGET_MAX),
        ):
            if type(setting) is not int or not 0 <= setting <= maximum:
                raise ValueError(f"{name} must be between 0 and {maximum}")
        user = self._user(user_id)
        now = time.time()
        with self._lock, self._connection:
            self._connection.executemany(
                "INSERT OR REPLACE INTO user_preferences(user_id,key,value,updated_at) VALUES (?,?,?,?)",
                (
                    (user, "context_message_count", str(message_count), now),
                    (user, "context_token_budget", str(token_budget), now),
                ),
            )
        return self.context_settings(user_id=user)

    def bounded_conversation_context(
        self, conversation_id: str, *, current_message_id: str = "",
        user_id: str | None = None,
    ) -> list[dict[str, object]]:
        settings = self.context_settings(user_id=user_id)
        message_limit = settings["message_count"]
        token_limit = settings["token_budget"]
        if message_limit <= 0 or token_limit <= 0:
            return []
        conversation = self.conversation(conversation_id, user_id=user_id)
        if conversation is None:
            raise KeyError("conversation not found")
        candidates: list[dict[str, object]] = []
        for raw in conversation.get("messages") or []:
            if not isinstance(raw, dict):
                continue
            message_id = str(raw.get("message_id") or "").strip()[:256]
            if current_message_id and message_id == current_message_id:
                break
            body = str(raw.get("body") or "").strip()
            if not body:
                continue
            candidates.append({
                "source": str(raw.get("source") or "").strip()[:128],
                "message_id": message_id,
                "sender": " ".join(str(raw.get("sender") or "").split())[:320],
                "direction": " ".join(str(raw.get("direction") or "").split())[:32],
                "received_at": raw.get("received_at"),
                "body": body,
            })
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
            where += " AND (source IN ('mail','email','gmail') OR source LIKE 'gmail:%')"
        elif source:
            where += " AND source=?"
            values.append(source)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT source,message_id,conversation_id,sender,body,received_at,seen_at
                FROM messages WHERE {where} ORDER BY received_at DESC LIMIT 2
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
                WHERE m.user_id=? AND m.seen_at IS NULL {source_sql}
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
                if status in {"completed", "review"}:
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
            if result.get("decision") != "notify":
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

    def claim_workspace_event(
        self, *, worker_id: str, lease_seconds: int = 600,
        after: int = 0, telegram_direct_only: bool = False,
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
                WHERE e.user_id=? AND e.seq>? AND m.direction='incoming' AND e.eligible=1
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
                (scoped_user, after, int(telegram_direct_only), now),
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
                "recent_context": self.bounded_conversation_context(
                    str(event["conversation_id"]),
                    current_message_id=str(event["message_id"]),
                    user_id=scoped_user,
                ),
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
            return " AND (c.source IN ('mail','email','gmail') OR c.source LIKE 'gmail:%')", []
        return " AND c.source=?", [source]

    def conversations(
        self, *, source: str | None = None, exact_source: bool = False,
        limit: int = 100, user_id: str | None = None,
    ) -> list[dict[str, object]]:
        if source and exact_source:
            source_sql, values = " AND c.source=?", [source]
        else:
            source_sql, values = self._source_filter(source)
        # Sort strictly by the latest message timestamp so the UI's "fresh on top"
        # rule never drifts because of bookkeeping fields like updated_at.
        # `preview` is the latest body; if that happens to be an attachment
        # placeholder ([image], [document] …) the service layer swaps in the
        # newest text body via `last_text_bodies_for` so the search and the
        # list item show real content.
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT c.id,c.source,c.sender,c.identity_id,c.updated_at,c.account_ref,
                       (SELECT body FROM messages WHERE user_id=c.user_id AND conversation_id=c.id
                        ORDER BY received_at DESC LIMIT 1) AS preview,
                       (SELECT received_at FROM messages WHERE user_id=c.user_id AND conversation_id=c.id
                        ORDER BY received_at DESC LIMIT 1) AS last_at,
                       (SELECT COUNT(*) FROM messages WHERE user_id=c.user_id
                        AND conversation_id=c.id AND seen_at IS NULL) AS unread_count,
                       (SELECT name FROM contact_names WHERE user_id=c.user_id
                        AND source=c.source AND sender=c.sender) AS display_name,
                       (SELECT MAX(received_at) FROM messages
                        WHERE user_id=c.user_id AND source=c.source) AS account_last_at
                FROM conversations c WHERE c.user_id=? {source_sql}
                ORDER BY last_at DESC, c.updated_at DESC LIMIT ?
                """,
                [self._user(user_id), *values, limit],
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
                AND conversation_id IN ({placeholders})
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
