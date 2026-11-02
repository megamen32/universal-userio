"""User-configured, tenant-scoped, single-send replies to fresh inbound messages.

ChatGPT is an explicit per-rule opt-in. Until the trusted provider delivers typed
non-approval provenance, runtime fails closed on those events (not on all rules).
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
import uuid
from typing import Any

_LOG = logging.getLogger(__name__)
_MAX_PATTERN = 256
_MAX_BODY = 2000
_MAX_SAMPLE = 4096
_MAX_RULES = 100
_FRESH_SECONDS = 300


class AutoReplyError(ValueError):
    """Invalid rule, unauthorized conversation, or unavailable safe matcher."""


class AutoReplyRules:
    """Uses the existing UserIO SQLite connection and delivery/receipt pipeline."""

    def __init__(self, service: Any) -> None:
        self.service = service
        self.store = service._store
        with self.store._lock, self.store._connection:
            self.store._connection.execute("""
                CREATE TABLE IF NOT EXISTS auto_reply_rules (
                    user_id TEXT NOT NULL, id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL, match_type TEXT NOT NULL,
                    pattern TEXT NOT NULL, flags TEXT NOT NULL, reply_text TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    chatgpt_opt_in INTEGER NOT NULL DEFAULT 0,
                    position INTEGER NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    PRIMARY KEY (user_id, id)
                )
            """)
            self.store._connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_auto_reply_rule_order
                ON auto_reply_rules (user_id, conversation_id, position)
            """)
            self.store._connection.execute("""
                CREATE TABLE IF NOT EXISTS auto_reply_deliveries (
                    user_id TEXT NOT NULL, source TEXT NOT NULL,
                    account_ref TEXT NOT NULL, message_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL, rule_id TEXT NOT NULL,
                    request_id TEXT NOT NULL, draft_id TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL, created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (user_id, source, account_ref, message_id)
                )
            """)

    def chatgpt_live_ready(self) -> bool:
        """True only after a trusted ChatGPT ingress adapter is actually registered.

        The current ChatGPT web adapter reads conversations but does not ingest
        verified ordinary/approval event types. No UI setting can enable it.
        This internal capability must never be set from a user-supplied envelope.
        """
        return getattr(self.service, "_verified_chatgpt_inbound_ready", False) is True

    def capabilities(self) -> dict[str, bool]:
        return {"chatgpt_live": self.chatgpt_live_ready()}

    def _require_live_chatgpt_if_enabled(self, chat: dict[str, str], enabled: bool) -> None:
        if chat["source"].startswith("chatgpt") and enabled and not self.chatgpt_live_ready():
            raise AutoReplyError("chatgpt_verified_ingress_unavailable")

    def _conversation(self, user_id: str, conversation_id: str,
                      *, chatgpt_opt_in: bool = False) -> dict[str, str]:
        if not isinstance(conversation_id, str) or not 1 <= len(conversation_id) <= 128:
            raise AutoReplyError("invalid_conversation_id")
        with self.store._lock:
            row = self.store._connection.execute(
                "SELECT id,source,account_ref,route_id FROM conversations WHERE user_id=? AND id=?",
                (user_id, conversation_id),
            ).fetchone()
        if row is None:
            raise KeyError("conversation_not_found")
        info = {key: str(row[key] or "") for key in ("id", "source", "account_ref", "route_id")}
        if info["source"] == "diagnostic":
            raise AutoReplyError("auto_replies_not_supported_for_source")
        if info["source"].startswith("chatgpt") and not chatgpt_opt_in:
            raise AutoReplyError("chatgpt_requires_explicit_opt_in")
        if not info["account_ref"]:
            raise AutoReplyError("conversation_requires_owned_account")
        account = next(
            (item for item in self.store.accounts(user_id=user_id)
             if item["id"] == info["account_ref"] and item["enabled"]
             and "reply" in item["capabilities"]),
            None,
        )
        if account is None:
            raise AutoReplyError("account_not_owned_or_reply_disabled")
        return info

    @staticmethod
    def _fields(payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise AutoReplyError("invalid_rule")
        pattern = payload.get("pattern")
        body = payload.get("reply_text")
        kind = payload.get("match_type")
        flags = payload.get("flags", "i")
        enabled = payload.get("enabled", False)
        chatgpt_opt_in = payload.get("chatgpt_opt_in", False)
        if type(chatgpt_opt_in) is not bool:
            raise AutoReplyError("chatgpt_opt_in_must_be_boolean")
        if kind not in ("keyword", "regex"):
            raise AutoReplyError("match_type_must_be_keyword_or_regex")
        if not isinstance(pattern, str) or not 1 <= len(pattern) <= _MAX_PATTERN or not pattern.strip():
            raise AutoReplyError("pattern_required_max_256")
        if not isinstance(body, str) or not 1 <= len(body.strip()) <= _MAX_BODY:
            raise AutoReplyError("reply_text_required_max_2000")
        if not isinstance(flags, str) or len(flags) > 3 or len(set(flags)) != len(flags) or set(flags) - set("ims"):
            raise AutoReplyError("invalid_regex_flags")
        if not isinstance(enabled, bool):
            raise AutoReplyError("enabled_must_be_boolean")
        if kind == "regex":
            AutoReplyRules._compile(pattern, flags)
        return dict(match_type=kind, pattern=pattern, flags=flags,
                    reply_text=body.strip(), enabled=enabled, chatgpt_opt_in=chatgpt_opt_in)

    @staticmethod
    def _compile(pattern: str, flags: str):
        try:
            import regex
        except ImportError as exc:
            raise AutoReplyError("safe_regex_backend_unavailable") from exc
        flag_bits = (regex.I if "i" in flags else 0) | (regex.M if "m" in flags else 0) | (regex.S if "s" in flags else 0)
        try:
            return regex.compile(pattern, flag_bits)
        except regex.error as exc:
            raise AutoReplyError("invalid_regex") from exc

    @staticmethod
    def _matches(rule: dict[str, Any], sample: str) -> bool:
        sample = sample[:_MAX_SAMPLE]
        if rule["match_type"] == "keyword":
            needle = rule["pattern"]
            return needle.casefold() in sample.casefold() if "i" in rule["flags"] else needle in sample
        try:
            return bool(AutoReplyRules._compile(rule["pattern"], rule["flags"]).search(sample, timeout=0.02))
        except TimeoutError as exc:
            # Do not execute a pathological expression again on this event.
            raise AutoReplyError("regex_match_timeout") from exc

    @staticmethod
    def _public(row: Any) -> dict[str, Any]:
        return {name: row[name] for name in
                ("id", "conversation_id", "match_type", "pattern", "flags", "reply_text", "position")} | {
                    "enabled": bool(row["enabled"]),
                    "chatgpt_opt_in": bool(row["chatgpt_opt_in"]),
                }

    def list(self, user_id: str) -> list[dict[str, Any]]:
        with self.store._lock:
            rows = self.store._connection.execute(
                "SELECT * FROM auto_reply_rules WHERE user_id=? ORDER BY position,id",
                (user_id,),
            ).fetchall()
        return [self._public(row) for row in rows]

    def create(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        fields = self._fields(payload)
        chat = self._conversation(user_id, payload.get("conversation_id"),
                                  chatgpt_opt_in=fields["chatgpt_opt_in"] or not fields["enabled"])
        self._require_live_chatgpt_if_enabled(chat, fields["enabled"])
        now = time.time()
        rule_id = "ar_" + uuid.uuid4().hex
        with self.store._lock, self.store._connection:
            count, position = self.store._connection.execute(
                "SELECT count(*),coalesce(max(position),0)+1 FROM auto_reply_rules WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if count >= _MAX_RULES:
                raise AutoReplyError("max_100_rules")
            self.store._connection.execute("""
                INSERT INTO auto_reply_rules
                (user_id,id,conversation_id,match_type,pattern,flags,reply_text,enabled,chatgpt_opt_in,position,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (user_id, rule_id, chat["id"], fields["match_type"], fields["pattern"],
                  fields["flags"], fields["reply_text"], int(fields["enabled"]),
                  int(fields["chatgpt_opt_in"]), position, now, now))
        return self.get(user_id, rule_id)

    def get(self, user_id: str, rule_id: str) -> dict[str, Any]:
        with self.store._lock:
            row = self.store._connection.execute(
                "SELECT * FROM auto_reply_rules WHERE user_id=? AND id=?", (user_id, rule_id)
            ).fetchone()
        if row is None:
            raise KeyError("rule_not_found")
        return self._public(row)

    def update(self, user_id: str, rule_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.get(user_id, rule_id)
        fields = self._fields(payload)
        chat = self._conversation(user_id, payload.get("conversation_id"),
                                  chatgpt_opt_in=fields["chatgpt_opt_in"] or not fields["enabled"])
        self._require_live_chatgpt_if_enabled(chat, fields["enabled"])
        with self.store._lock, self.store._connection:
            cursor = self.store._connection.execute("""
                UPDATE auto_reply_rules SET conversation_id=?, match_type=?, pattern=?, flags=?,
                    reply_text=?, enabled=?, chatgpt_opt_in=?, updated_at=? WHERE user_id=? AND id=?
            """, (chat["id"], fields["match_type"], fields["pattern"], fields["flags"],
                  fields["reply_text"], int(fields["enabled"]),
                  int(fields["chatgpt_opt_in"]), time.time(), user_id, rule_id))
            if cursor.rowcount != 1:
                raise KeyError("rule_not_found")
        return self.get(user_id, rule_id)

    def toggle(self, user_id: str, rule_id: str, enabled: Any) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise AutoReplyError("enabled_must_be_boolean")
        rule = self.get(user_id, rule_id)
        # Revoking an existing rule remains possible, but enabling ChatGPT
        # requires its own opt-in even when the trusted adapter is available.
        chat = self._conversation(user_id, rule["conversation_id"],
                                  chatgpt_opt_in=rule["chatgpt_opt_in"] or not enabled)
        self._require_live_chatgpt_if_enabled(chat, enabled)
        with self.store._lock, self.store._connection:
            cursor = self.store._connection.execute(
                "UPDATE auto_reply_rules SET enabled=?, updated_at=? WHERE user_id=? AND id=?",
                (int(enabled), time.time(), user_id, rule_id),
            )
            if cursor.rowcount != 1:
                raise KeyError("rule_not_found")
        return self.get(user_id, rule_id)

    def delete(self, user_id: str, rule_id: str) -> None:
        with self.store._lock, self.store._connection:
            cursor = self.store._connection.execute(
                "DELETE FROM auto_reply_rules WHERE user_id=? AND id=?", (user_id, rule_id)
            )
            if cursor.rowcount != 1:
                raise KeyError("rule_not_found")
            self._reposition(user_id)

    def _reposition(self, user_id: str) -> None:
        ids = self.store._connection.execute(
            "SELECT id FROM auto_reply_rules WHERE user_id=? ORDER BY position,id", (user_id,)
        ).fetchall()
        for position, row in enumerate(ids, 1):
            self.store._connection.execute(
                "UPDATE auto_reply_rules SET position=? WHERE user_id=? AND id=?",
                (position, user_id, row["id"]),
            )

    def move(self, user_id: str, rule_id: str, direction: str) -> list[dict[str, Any]]:
        if direction not in ("up", "down"):
            raise AutoReplyError("direction_must_be_up_or_down")
        with self.store._lock, self.store._connection:
            ids = [row["id"] for row in self.store._connection.execute(
                "SELECT id FROM auto_reply_rules WHERE user_id=? ORDER BY position,id", (user_id,)
            )]
            if rule_id not in ids:
                raise KeyError("rule_not_found")
            index = ids.index(rule_id)
            adjacent = index + (-1 if direction == "up" else 1)
            if 0 <= adjacent < len(ids):
                ids[index], ids[adjacent] = ids[adjacent], ids[index]
                for position, item_id in enumerate(ids, 1):
                    self.store._connection.execute(
                        "UPDATE auto_reply_rules SET position=?, updated_at=? WHERE user_id=? AND id=?",
                        (position, time.time(), user_id, item_id),
                    )
        return self.list(user_id)

    def preview(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Pure read; no draft or receipt is written and no provider is called."""
        sample = payload.get("sample_text")
        if not isinstance(sample, str) or len(sample) > _MAX_SAMPLE:
            raise AutoReplyError("sample_text_must_be_string_max_4096")
        conversation_id = payload.get("conversation_id")
        # Reading the selected chat is allowed; ChatGPT preview additionally needs
        # an explicitly opted-in stored rule or the proposed rule opt-in below.
        self._conversation(user_id, conversation_id, chatgpt_opt_in=True)
        if "rule" in payload:
            draft = payload["rule"]
            if not isinstance(draft, dict):
                raise AutoReplyError("invalid_rule")
            fields = self._fields(draft)
            self._conversation(user_id, conversation_id, chatgpt_opt_in=fields["chatgpt_opt_in"])
            rules = [{"id": None, "conversation_id": conversation_id, **fields}]
        else:
            rules = [row for row in self.list(user_id)
                     if row["enabled"] and row["conversation_id"] == conversation_id]
        for rule in rules:
            if not rule["enabled"]:
                continue
            if not rule.get("chatgpt_opt_in") and str(self._conversation(
                    user_id, conversation_id, chatgpt_opt_in=True)["source"]).startswith("chatgpt"):
                continue
            if self._matches(rule, sample):
                return {"matched": True, "rule_id": rule["id"], "reply_text": rule["reply_text"],
                        "live_send_available": not self._conversation(
                            user_id, conversation_id, chatgpt_opt_in=True)["source"].startswith("chatgpt")
                        or self.chatgpt_live_ready()}
        return {"matched": False, "rule_id": None, "reply_text": None,
                "live_send_available": not self._conversation(
                    user_id, conversation_id, chatgpt_opt_in=True)["source"].startswith("chatgpt")
                or self.chatgpt_live_ready()}

    def claimed_for(self, user_id: str, conversation_id: str, message_id: str) -> bool:
        """Prevent an existing AI auto-send path from answering this event again."""
        with self.store._lock:
            row = self.store._connection.execute("""
                SELECT 1 FROM auto_reply_deliveries
                WHERE user_id=? AND conversation_id=? AND message_id=?
                LIMIT 1
            """, (user_id, conversation_id, message_id)).fetchone()
        return row is not None

    def on_inbound(self, user_id: str, conversation_id: str, message: Any) -> None:
        # Never make the provider's ingest fail because an optional rule failed.
        try:
            self._process(user_id, conversation_id, message)
        except Exception as exc:
            _LOG.warning("auto_reply_inbound_failed category=%s", type(exc).__name__)

    def _process(self, user_id: str, conversation_id: str, message: Any) -> None:
        if (message.direction != "incoming" or message.reconciliation or message.sender_is_bot
                or message.outbox_receipt or not message.body.strip()):
            return
        age = time.time() - message.received_at
        if age > _FRESH_SECONDS or age < -60:
            return  # Historical replay/old sync, not a live event.
        if not self.store.send_enabled(user_id=user_id) or not self.store.capability_enabled("send", user_id=user_id):
            return
        chat = self._conversation(user_id, conversation_id, chatgpt_opt_in=True)
        # Existing provider-level manual-approval locks are not overridden by
        # the rule's enabled setting or a ChatGPT opt-in.
        if self.service.manual_approval_required(
                self.store.conversation(conversation_id, user_id=user_id)):
            return
        if chat["source"] != message.source or not self.store.route_allowed(
                user_id=user_id, source=chat["source"], route_id=chat["route_id"]):
            return
        selected = None
        for rule in self.list(user_id):
            if rule["conversation_id"] != conversation_id or not rule["enabled"]:
                continue
            if chat["source"].startswith("chatgpt"):
                if not rule["chatgpt_opt_in"] or not self.chatgpt_live_ready():
                    continue
                # ChatGPT messages can carry requests for mandatory human
                # approval. Current generic UserIO ingest has no authoritative
                # action/provenance/approval classification. Opt-in is never
                # evidence that such a request was pre-authorized.
                # An adapter may later supply a verified non-approval
                # event_type; unknown classifications stay human-only.
                if (getattr(message, "trusted_event_type", None) != "ordinary_message"
                        or getattr(message, "approval_required", None) is not False
                        or getattr(message, "provenance_verified", False) is not True):
                    continue
            try:
                if self._matches(rule, message.body):
                    selected = rule
                    break
            except AutoReplyError:
                # Invalid/expensive pattern can never block another rule or ingestion.
                _LOG.warning("auto_reply_rule_match_skipped")
        if selected is None:
            return
        digest = hashlib.sha256(
            "\0".join([user_id, chat["source"], chat["account_ref"], message.message_id]).encode()
        ).hexdigest()
        request_id = "auto_" + digest[:52]
        now = time.time()
        # Claim is durable before any external I/O; no retries of unknown outcomes.
        with self.store._lock, self.store._connection:
            current = self.store._connection.execute(
                "SELECT enabled,conversation_id,chatgpt_opt_in FROM auto_reply_rules WHERE user_id=? AND id=?",
                (user_id, selected["id"]),
            ).fetchone()
            if current is None or not current["enabled"] or current["conversation_id"] != conversation_id:
                return
            if chat["source"].startswith("chatgpt") and not current["chatgpt_opt_in"]:
                return
            cursor = self.store._connection.execute("""
                INSERT OR IGNORE INTO auto_reply_deliveries
                (user_id,source,account_ref,message_id,conversation_id,rule_id,request_id,state,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (user_id, chat["source"], chat["account_ref"], message.message_id,
                  conversation_id, selected["id"], request_id, "claimed", now, now))
            if cursor.rowcount != 1:
                return
        draft_id = ""
        state = "unknown"
        try:
            draft = self.service.prepare_manual_message(
                conversation_id, body=selected["reply_text"], request_id=request_id,
                user_id=user_id, notify=False,
            )
            draft_id = draft.id
            approved = self.service.approve(draft.id, user_id=user_id)
            if approved.status == "approved":
                state = "sent"
        except Exception as exc:
            _LOG.warning("auto_reply_delivery_unknown category=%s", type(exc).__name__)
        finally:
            with self.store._lock, self.store._connection:
                self.store._connection.execute("""
                    UPDATE auto_reply_deliveries SET draft_id=?,state=?,updated_at=?
                    WHERE user_id=? AND source=? AND account_ref=? AND message_id=?
                """, (draft_id, state, time.time(), user_id, chat["source"],
                      chat["account_ref"], message.message_id))
