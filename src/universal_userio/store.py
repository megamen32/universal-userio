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
                received_at REAL NOT NULL,seen_at REAL,
                PRIMARY KEY(user_id,source,message_id)
            );
            CREATE INDEX IF NOT EXISTS messages_conversation_idx
                ON messages(user_id,conversation_id,received_at);
            CREATE TABLE IF NOT EXISTS contact_names (
                user_id TEXT NOT NULL,source TEXT NOT NULL,sender TEXT NOT NULL,
                name TEXT NOT NULL,updated_at REAL NOT NULL,
                PRIMARY KEY(user_id,source,sender)
            );
            CREATE TABLE IF NOT EXISTS drafts (
                user_id TEXT NOT NULL,id TEXT NOT NULL,conversation_id TEXT NOT NULL,
                body TEXT NOT NULL,status TEXT NOT NULL,created_at REAL NOT NULL,
                approved_at REAL,outbox_receipt TEXT,PRIMARY KEY(user_id,id)
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
        """
        for statement in script.split(";"):
            if statement.strip():
                self._connection.execute(statement)

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
            clauses.append("id=?")
            values.append(account_id)
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

    def ingest(
        self, message: InboxMessage, *, conversation_id: str, policy: ConversationPolicy,
        user_id: str | None = None,
    ) -> bool:
        user_id, now = self._user(user_id), time.time()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO conversations
                (user_id,id,conversation_key,route_id,source,sender,identity_id,response_mode,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    user_id, conversation_id, message.conversation_key, policy.route_id,
                    message.source, message.sender, policy.identity_id, policy.mode, now,
                ),
            )
            inserted = self._connection.execute(
                """
                INSERT OR IGNORE INTO messages
                (user_id,source,message_id,conversation_id,sender,body,received_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    user_id, message.source, message.message_id, conversation_id,
                    message.sender, message.body, message.received_at,
                ),
            ).rowcount == 1
            if getattr(message, "sender_name", ""):
                self._connection.execute(
                    """
                    INSERT OR REPLACE INTO contact_names
                    (user_id,source,sender,name,updated_at) VALUES (?,?,?,?,?)
                    """,
                    (user_id, message.source, message.sender, message.sender_name, now),
                )
            if inserted:
                self._connection.execute(
                    "UPDATE conversations SET updated_at=? WHERE user_id=? AND id=?",
                    (now, user_id, conversation_id),
                )
        return inserted

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
            self._connection.execute(
                "UPDATE drafts SET body=? WHERE user_id=? AND id=?", (text, user_id, draft_id)
            )
        return ReplyDraft(str(row["id"]), str(row["conversation_id"]), text, "proposed")

    def delete_draft(self, draft_id: str, *, user_id: str | None = None) -> bool:
        user_id = self._user(user_id)
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT status FROM drafts WHERE user_id=? AND id=?", (user_id, draft_id)
            ).fetchone()
            if row is None:
                return False
            if row["status"] == "approved":
                raise ValueError("approved drafts are immutable receipts")
            return self._connection.execute(
                "DELETE FROM drafts WHERE user_id=? AND id=?", (user_id, draft_id)
            ).rowcount == 1

    def delete_conversation(self, conversation_id: str, *, user_id: str | None = None) -> bool:
        user_id = self._user(user_id)
        with self._lock, self._connection:
            exists = self._connection.execute(
                "SELECT 1 FROM conversations WHERE user_id=? AND id=?", (user_id, conversation_id)
            ).fetchone()
            if exists is None:
                return False
            self._connection.execute(
                "DELETE FROM drafts WHERE user_id=? AND conversation_id=?", (user_id, conversation_id)
            )
            self._connection.execute(
                "DELETE FROM messages WHERE user_id=? AND conversation_id=?", (user_id, conversation_id)
            )
            self._connection.execute(
                "DELETE FROM conversations WHERE user_id=? AND id=?", (user_id, conversation_id)
            )
        return True

    def draft(self, draft_id: str, *, user_id: str | None = None) -> ReplyDraft:
        with self._lock:
            row = self._connection.execute(
                "SELECT id,conversation_id,body,status FROM drafts WHERE user_id=? AND id=?",
                (self._user(user_id), draft_id),
            ).fetchone()
        if row is None:
            raise KeyError("draft not found")
        return ReplyDraft(str(row["id"]), str(row["conversation_id"]), str(row["body"]), str(row["status"]))

    def approve(self, draft_id: str, receipt: str, *, user_id: str | None = None) -> ReplyDraft:
        user_id = self._user(user_id)
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM drafts WHERE user_id=? AND id=?", (user_id, draft_id)
            ).fetchone()
            if row is None:
                raise KeyError("draft not found")
            if row["status"] == "approved":
                return ReplyDraft(row["id"], row["conversation_id"], row["body"], row["status"])
            if row["status"] != "proposed":
                raise ValueError("draft is not approvable")
            self._connection.execute(
                """
                UPDATE drafts SET status='approved',approved_at=?,outbox_receipt=?
                WHERE user_id=? AND id=?
                """,
                (time.time(), receipt, user_id, draft_id),
            )
        return ReplyDraft(row["id"], row["conversation_id"], row["body"], "approved")

    def reject(self, draft_id: str, *, user_id: str | None = None) -> ReplyDraft:
        user_id = self._user(user_id)
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM drafts WHERE user_id=? AND id=?", (user_id, draft_id)
            ).fetchone()
            if row is None:
                raise KeyError("draft not found")
            if row["status"] == "proposed":
                self._connection.execute(
                    "UPDATE drafts SET status='rejected' WHERE user_id=? AND id=?", (user_id, draft_id)
                )
                return ReplyDraft(row["id"], row["conversation_id"], row["body"], "rejected")
        return ReplyDraft(row["id"], row["conversation_id"], row["body"], row["status"])

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
                    SELECT source,message_id,sender,body,received_at,seen_at FROM messages
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
        if text_limit is not None:
            for item in message_records:
                item["body"] = str(item["body"])[:text_limit]
        return {
            "id": row["id"], "route_id": row["route_id"], "response_mode": row["response_mode"],
            "identity_id": row["identity_id"], "source": row["source"], "sender": row["sender"],
            "display_name": str(name_row["name"]) if name_row else "",
            "messages": message_records, "drafts": [dict(item) for item in drafts],
        }

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
        return result

    def new_messages(self, *, limit: int = 50, user_id: str | None = None) -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT m.source,m.message_id,m.sender,m.body,m.received_at,
                       c.id AS conversation_id,c.identity_id
                FROM messages m JOIN conversations c
                  ON c.user_id=m.user_id AND c.id=m.conversation_id
                WHERE m.user_id=? AND m.seen_at IS NULL
                ORDER BY m.received_at DESC LIMIT ?
                """,
                (self._user(user_id), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _source_filter(source: str | None) -> tuple[str, list[object]]:
        if not source:
            return "", []
        if source in {"mail", "email", "gmail"}:
            return " AND (c.source IN ('mail','email','gmail') OR c.source LIKE 'gmail:%')", []
        return " AND c.source=?", [source]

    def conversations(
        self, *, source: str | None = None, limit: int = 100, user_id: str | None = None
    ) -> list[dict[str, object]]:
        source_sql, values = self._source_filter(source)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT c.id,c.source,c.sender,c.identity_id,c.updated_at,
                       (SELECT body FROM messages WHERE user_id=c.user_id AND conversation_id=c.id
                        ORDER BY received_at DESC LIMIT 1) AS preview,
                       (SELECT received_at FROM messages WHERE user_id=c.user_id AND conversation_id=c.id
                        ORDER BY received_at DESC LIMIT 1) AS last_at,
                       (SELECT COUNT(*) FROM messages WHERE user_id=c.user_id
                        AND conversation_id=c.id AND seen_at IS NULL) AS unread_count,
                       (SELECT name FROM contact_names WHERE user_id=c.user_id
                        AND source=c.source AND sender=c.sender) AS display_name
                FROM conversations c WHERE c.user_id=? {source_sql}
                ORDER BY c.updated_at DESC LIMIT ?
                """,
                [self._user(user_id), *values, limit],
            ).fetchall()
        return [dict(row) for row in rows]

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
