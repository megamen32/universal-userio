"""Small durable store for business conversations and approval state."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from .contracts import InboxMessage, ReplyDraft


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
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    source TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    body TEXT NOT NULL,
                    received_at REAL NOT NULL,
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
                """
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def ingest(self, message: InboxMessage, *, conversation_id: str, route_id: str) -> bool:
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR IGNORE INTO conversations(id, conversation_key, route_id, source, sender, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (conversation_id, message.conversation_key, route_id, message.source, message.sender, now),
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
                "SELECT source,message_id,sender,body,received_at FROM messages WHERE conversation_id=? ORDER BY received_at", (conversation_id,)
            ).fetchall()
            drafts = self._connection.execute(
                "SELECT id,body,status,outbox_receipt FROM drafts WHERE conversation_id=? ORDER BY created_at", (conversation_id,)
            ).fetchall()
        return {"id": row["id"], "route_id": row["route_id"], "source": row["source"], "sender": row["sender"], "messages": [dict(item) for item in messages], "drafts": [dict(item) for item in drafts]}
