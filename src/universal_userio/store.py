"""Small durable store for business conversations and approval state."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from .contracts import InboxMessage, ReplyDraft
from .contracts import ConversationPolicy


class SQLiteUserIOStore:
    def __init__(self, path: str | Path) -> None:
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock, self._connection:
            self._connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    conversation_key TEXT UNIQUE NOT NULL,
                    route_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    identity_id TEXT,
                    response_mode TEXT NOT NULL DEFAULT 'approve',
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    source TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    body TEXT NOT NULL,
                    received_at REAL NOT NULL,
                    seen_at REAL,
                    PRIMARY KEY(source, message_id)
                );
                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    approved_at REAL,
                    outbox_receipt TEXT
                );
                CREATE TABLE IF NOT EXISTS identities (
                    source TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    identity_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    PRIMARY KEY(source, external_id)
                );
                CREATE TABLE IF NOT EXISTS reply_rules (
                    identity_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    route_id TEXT NOT NULL,
                    response_mode TEXT NOT NULL,
                    PRIMARY KEY(identity_id, source)
                );
                CREATE TABLE IF NOT EXISTS provider_accounts (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    can_read INTEGER NOT NULL,
                    can_reply INTEGER NOT NULL,
                    credential_ref TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1
                );
                """
            )
            self._add_column_if_missing("conversations", "identity_id", "TEXT")
            self._add_column_if_missing("conversations", "response_mode", "TEXT NOT NULL DEFAULT 'approve'")
            self._add_column_if_missing("messages", "seen_at", "REAL")

    def _add_column_if_missing(self, table: str, column: str, declaration: str) -> None:
        columns = {str(row["name"]) for row in self._connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self._connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def register_identity(self, *, source: str, external_id: str, identity_id: str, display_name: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO identities(source,external_id,identity_id,display_name) VALUES (?,?,?,?)",
                (source, external_id, identity_id, display_name),
            )

    def register_account(
        self, *, account_id: str, provider: str, display_name: str, can_read: bool, can_reply: bool, credential_ref: str, enabled: bool = True
    ) -> None:
        if not account_id.strip() or not provider.strip() or not credential_ref.strip():
            raise ValueError("account id, provider and credential reference are required")
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO provider_accounts(id,provider,display_name,can_read,can_reply,credential_ref,enabled) VALUES (?,?,?,?,?,?,?)",
                (account_id, provider.lower(), display_name or account_id, int(can_read), int(can_reply), credential_ref, int(enabled)),
            )

    def accounts(self) -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT id,provider,display_name,can_read,can_reply,credential_ref,enabled FROM provider_accounts ORDER BY id"
            ).fetchall()
        return [
            {"id": row["id"], "provider": row["provider"], "display_name": row["display_name"], "capabilities": [name for name, value in (("read", row["can_read"]), ("reply", row["can_reply"])) if value], "credential_ref": row["credential_ref"], "enabled": bool(row["enabled"])}
            for row in rows
        ]

    def set_rule(self, *, identity_id: str, source: str, route_id: str, mode: str) -> None:
        if mode not in {"suggest", "approve", "auto_send"}:
            raise ValueError("unsupported reply mode")
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO reply_rules(identity_id,source,route_id,response_mode) VALUES (?,?,?,?)",
                (identity_id, source, route_id, mode),
            )

    def policy_for(self, message: InboxMessage, *, fallback_route_id: str) -> ConversationPolicy:
        with self._lock:
            identity = self._connection.execute(
                "SELECT identity_id FROM identities WHERE source=? AND external_id=?", (message.source, message.sender)
            ).fetchone()
            identity_id = str(identity["identity_id"]) if identity else None
            rule = None if identity_id is None else self._connection.execute(
                "SELECT route_id,response_mode FROM reply_rules WHERE identity_id=? AND source=?", (identity_id, message.source)
            ).fetchone()
        if rule:
            return ConversationPolicy(str(rule["route_id"]), str(rule["response_mode"]), identity_id)
        return ConversationPolicy(fallback_route_id, "approve", identity_id)

    def ingest(self, message: InboxMessage, *, conversation_id: str, policy: ConversationPolicy) -> bool:
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR IGNORE INTO conversations(id, conversation_key, route_id, source, sender, identity_id, response_mode, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (conversation_id, message.conversation_key, policy.route_id, message.source, message.sender, policy.identity_id, policy.mode, now),
            )
            inserted = self._connection.execute(
                "INSERT OR IGNORE INTO messages(source, message_id, conversation_id, sender, body, received_at) VALUES (?, ?, ?, ?, ?, ?)",
                (message.source, message.message_id, conversation_id, message.sender, message.body, message.received_at),
            ).rowcount == 1
            if inserted:
                self._connection.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conversation_id))
        return inserted

    def add_draft(self, draft: ReplyDraft) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO drafts(id, conversation_id, body, status, created_at) VALUES (?, ?, ?, ?, ?)",
                (draft.id, draft.conversation_id, draft.body, draft.status, time.time()),
            )

    def update_draft(self, draft_id: str, *, body: str) -> ReplyDraft:
        text = body.strip()
        if not text:
            raise ValueError("draft body is required")
        with self._lock, self._connection:
            row = self._connection.execute("SELECT id,conversation_id,status FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if row is None:
                raise KeyError("draft not found")
            if row["status"] != "proposed":
                raise ValueError("only proposed drafts can be edited")
            self._connection.execute("UPDATE drafts SET body=? WHERE id=?", (text, draft_id))
        return ReplyDraft(str(row["id"]), str(row["conversation_id"]), text, "proposed")

    def delete_draft(self, draft_id: str) -> bool:
        with self._lock, self._connection:
            row = self._connection.execute("SELECT status FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if row is None:
                return False
            if row["status"] == "approved":
                raise ValueError("approved drafts are immutable receipts")
            return self._connection.execute("DELETE FROM drafts WHERE id=?", (draft_id,)).rowcount == 1

    def delete_conversation(self, conversation_id: str) -> bool:
        """Delete UserIO's local business copy only; source-provider data is untouched."""
        with self._lock, self._connection:
            exists = self._connection.execute("SELECT 1 FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            if exists is None:
                return False
            self._connection.execute("DELETE FROM drafts WHERE conversation_id=?", (conversation_id,))
            self._connection.execute("DELETE FROM messages WHERE conversation_id=?", (conversation_id,))
            self._connection.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
        return True

    def draft(self, draft_id: str) -> ReplyDraft:
        with self._lock:
            row = self._connection.execute("SELECT id,conversation_id,body,status FROM drafts WHERE id=?", (draft_id,)).fetchone()
        if row is None:
            raise KeyError("draft not found")
        return ReplyDraft(str(row["id"]), str(row["conversation_id"]), str(row["body"]), str(row["status"]))

    def approve(self, draft_id: str, receipt: str) -> ReplyDraft:
        with self._lock, self._connection:
            row = self._connection.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if row is None:
                raise KeyError("draft not found")
            if row["status"] == "approved":
                return ReplyDraft(row["id"], row["conversation_id"], row["body"], row["status"])
            if row["status"] != "proposed":
                raise ValueError("draft is not approvable")
            self._connection.execute(
                "UPDATE drafts SET status='approved', approved_at=?, outbox_receipt=? WHERE id=?",
                (time.time(), receipt, draft_id),
            )
        return ReplyDraft(row["id"], row["conversation_id"], row["body"], "approved")

    def reject(self, draft_id: str) -> ReplyDraft:
        with self._lock, self._connection:
            row = self._connection.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if row is None:
                raise KeyError("draft not found")
            if row["status"] == "proposed":
                self._connection.execute("UPDATE drafts SET status='rejected' WHERE id=?", (draft_id,))
                return ReplyDraft(row["id"], row["conversation_id"], row["body"], "rejected")
        return ReplyDraft(row["id"], row["conversation_id"], row["body"], row["status"])

    def conversation(self, conversation_id: str) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            if row is None:
                return None
            messages = self._connection.execute(
            "SELECT source,message_id,sender,body,received_at,seen_at FROM messages WHERE conversation_id=? ORDER BY received_at", (conversation_id,)
            ).fetchall()
            drafts = self._connection.execute(
                "SELECT id,body,status,outbox_receipt FROM drafts WHERE conversation_id=? ORDER BY created_at", (conversation_id,)
            ).fetchall()
        return {"id": row["id"], "route_id": row["route_id"], "response_mode": row["response_mode"], "identity_id": row["identity_id"], "source": row["source"], "sender": row["sender"], "messages": [dict(item) for item in messages], "drafts": [dict(item) for item in drafts]}

    def new_messages(self, *, limit: int = 50) -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT m.source,m.message_id,m.sender,m.body,m.received_at,c.id AS conversation_id,c.identity_id FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE m.seen_at IS NULL ORDER BY m.received_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def conversations(self, *, source: str | None = None, limit: int = 100) -> list[dict[str, object]]:
        """Compact chat-list records for the operator UI."""
        clauses = ""
        values: list[object] = [limit]
        if source:
            clauses = "WHERE c.source=?"
            values.insert(0, source)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT c.id,c.source,c.sender,c.identity_id,c.updated_at,
                       (SELECT body FROM messages WHERE conversation_id=c.id ORDER BY received_at DESC LIMIT 1) AS preview,
                       (SELECT COUNT(*) FROM messages WHERE conversation_id=c.id AND seen_at IS NULL) AS unread_count
                FROM conversations c
                {clauses}
                ORDER BY c.updated_at DESC
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_seen(self, *, source: str, message_id: str) -> bool:
        with self._lock, self._connection:
            return self._connection.execute(
                "UPDATE messages SET seen_at=? WHERE source=? AND message_id=? AND seen_at IS NULL", (time.time(), source, message_id)
            ).rowcount == 1
