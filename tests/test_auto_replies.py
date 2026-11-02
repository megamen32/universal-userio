"""Isolated auto-reply tests: no network, credentials, or real provider send."""
import sqlite3
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from types import SimpleNamespace

from universal_userio.auto_replies import AutoReplyError, AutoReplyRules


@dataclass
class Inbound:
    message_id: str = "m1"
    source: str = "telegram"
    body: str = "Hello"
    received_at: float = 0
    direction: str = "incoming"
    reconciliation: bool = False
    sender_is_bot: bool = False
    outbox_receipt: str = ""

    def __post_init__(self):
        if not self.received_at:
            self.received_at = time.time()


class Store:
    def __init__(self):
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(":memory:", check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            "CREATE TABLE conversations (user_id TEXT,id TEXT,source TEXT,account_ref TEXT,route_id TEXT)"
        )
        self._connection.executemany(
            "INSERT INTO conversations VALUES (?,?,?,?,?)",
            [("alice", "chat-a", "telegram", "telegram:alice", "telegram"),
             ("alice", "chat-b", "telegram", "telegram:alice", "telegram"),
             ("bob", "chat-x", "telegram", "telegram:bob", "telegram"),
             ("alice", "chat-gpt", "chatgpt:owner", "chatgpt:owner", "chatgpt"),
             ("alice", "chat-restricted", "telegram", "telegram:disabled", "telegram")],
        )
        self._connection.commit()

    def accounts(self, *, user_id):
        return [{"id": "telegram:" + user_id, "enabled": True, "capabilities": ["read", "reply"]},
                {"id": "chatgpt:owner", "enabled": True, "capabilities": ["read", "reply"]}]

    def conversation(self, conversation_id, *, user_id):
        with self._lock:
            row = self._connection.execute(
                "SELECT source,account_ref,route_id FROM conversations WHERE user_id=? AND id=?",
                (user_id, conversation_id),
            ).fetchone()
        return dict(row) if row else None

    def send_enabled(self, *, user_id): return True
    def capability_enabled(self, name, *, user_id): return True
    def route_allowed(self, *, user_id, source, route_id): return True


class Service:
    def __init__(self):
        self._store = Store()
        self.prepared = []
        self.sent = []
        self.fail = False
        self._lock = threading.Lock()

    def prepare_manual_message(self, conversation_id, *, body, request_id, user_id, notify):
        assert notify is False
        with self._lock:
            self.prepared.append((user_id, conversation_id, body, request_id))
        return SimpleNamespace(id="draft-" + request_id)

    def manual_approval_required(self, conversation): return False
    def approve(self, draft_id, *, user_id):
        with self._lock:
            self.sent.append((user_id, draft_id))
        if self.fail:
            raise TimeoutError("unknown provider result")
        return SimpleNamespace(status="approved")


class AutoReplyTests(unittest.TestCase):
    def setUp(self):
        self.service = Service()
        self.rules = AutoReplyRules(self.service)

    def payload(self, **overrides):
        return {"conversation_id": "chat-a", "match_type": "keyword", "pattern": "hello",
                "flags": "i", "reply_text": "Hi there", "enabled": True} | overrides

    def test_empty_disabled_default_and_crud_order(self):
        self.assertEqual(self.rules.list("alice"), [])
        a = self.rules.create("alice", self.payload(enabled=False))
        b = self.rules.create("alice", self.payload(pattern="hello again", reply_text="2"))
        self.assertFalse(a["enabled"])
        self.assertEqual([r["id"] for r in self.rules.move("alice", b["id"], "up")], [b["id"], a["id"]])
        a = self.rules.update("alice", a["id"], self.payload(reply_text="Updated"))
        self.assertEqual(a["reply_text"], "Updated")
        self.rules.toggle("alice", a["id"], True)
        self.assertTrue(self.rules.get("alice", a["id"])["enabled"])
        self.rules.delete("alice", b["id"])
        self.assertEqual(self.rules.list("alice")[0]["position"], 1)

    def test_tenant_and_account_acl(self):
        a = self.rules.create("alice", self.payload())
        self.assertEqual(self.rules.list("bob"), [])
        for operation in (
            lambda: self.rules.get("bob", a["id"]),
            lambda: self.rules.toggle("bob", a["id"], False),
            lambda: self.rules.update("bob", a["id"], self.payload(conversation_id="chat-x")),
            lambda: self.rules.delete("bob", a["id"]),
            lambda: self.rules.create("bob", self.payload(conversation_id="chat-a")),
            lambda: self.rules.create("alice", self.payload(conversation_id="chat-x")),
        ):
            with self.assertRaises(KeyError):
                operation()
        with self.assertRaises(AutoReplyError):
            self.rules.create("alice", self.payload(conversation_id="chat-restricted"))
        with self.assertRaises(AutoReplyError):
            self.rules.create("alice", self.payload(conversation_id="chat-gpt"))
        self.assertEqual(len(self.rules.list("alice")), 1)

    def test_safe_regex_validation_and_timeout(self):
        for data in (
            self.payload(match_type="regex", pattern="("),
            self.payload(flags="g"),
            self.payload(pattern=""),
            self.payload(pattern="a" * 257),
            self.payload(reply_text=""),
        ):
            with self.assertRaises(AutoReplyError):
                self.rules.create("alice", data)
        self.rules.create("alice", self.payload(match_type="regex", pattern=r"^hello\s+\w+$", flags="im"))
        self.assertTrue(self.rules.preview("alice", {"conversation_id": "chat-a", "sample_text": "HELLO world"})["matched"])
        with self.assertRaises(AutoReplyError):
            self.rules.preview("alice", {"conversation_id": "chat-a", "sample_text": "X" * 4097})
        self.rules.create("alice", self.payload(match_type="regex", pattern=r"(a+)+$", reply_text="Unsafe match"))
        start = time.monotonic()
        with self.assertRaises(AutoReplyError):
            self.rules.preview("alice", {"conversation_id": "chat-a",
                "sample_text": "a" * 3600 + "!", "rule": self.payload(match_type="regex", pattern=r"(a+)+$")})
        self.assertLess(time.monotonic() - start, 1.0)

    def test_preview_has_no_send_or_delivery_claim(self):
        self.rules.create("alice", self.payload())
        self.assertTrue(self.rules.preview("alice", {"conversation_id": "chat-a", "sample_text": "HELLO"})["matched"])
        self.assertFalse(self.rules.preview("alice", {"conversation_id": "chat-a", "sample_text": "bye"})["matched"])
        self.assertEqual(self.service.prepared, [])
        self.assertEqual(self.service.sent, [])
        self.assertEqual(self.service._store._connection.execute("SELECT count(*) FROM auto_reply_deliveries").fetchone()[0], 0)

    def test_first_rule_only_and_duplicate_durable_claim(self):
        self.rules.create("alice", self.payload(reply_text="first"))
        self.rules.create("alice", self.payload(reply_text="second"))
        event = Inbound()
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda _: self.rules.on_inbound("alice", "chat-a", event), range(6)))
        self.assertEqual(len(self.service.sent), 1)
        self.assertEqual(self.service.prepared[0][2], "first")
        self.rules.on_inbound("alice", "chat-a", event)
        self.assertEqual(len(self.service.sent), 1)
        row = self.service._store._connection.execute("SELECT state FROM auto_reply_deliveries").fetchone()
        self.assertEqual(row[0], "sent")

    def test_unknown_outcome_never_retried(self):
        self.rules.create("alice", self.payload())
        self.service.fail = True
        event = Inbound()
        self.rules.on_inbound("alice", "chat-a", event)
        self.service.fail = False
        self.rules.on_inbound("alice", "chat-a", event)
        self.assertEqual(len(self.service.sent), 1)
        row = self.service._store._connection.execute("SELECT state FROM auto_reply_deliveries").fetchone()
        self.assertEqual(row[0], "unknown")

    def test_chatgpt_explicit_opt_in_but_unclassified_approval_stays_human(self):
        with self.assertRaises(AutoReplyError):
            self.rules.create("alice", self.payload(conversation_id="chat-gpt"))
        rule = self.rules.create("alice", self.payload(conversation_id="chat-gpt",
                                                      chatgpt_opt_in=True))
        self.assertTrue(rule["chatgpt_opt_in"])
        self.assertTrue(self.rules.preview("alice", {
            "conversation_id": "chat-gpt", "sample_text": "hello",
        })["matched"])
        # Real generic ChatGPT ingress currently has no authoritative
        # action/provenance metadata. Neither opt-in nor matching words
        # satisfy a required human approval.
        self.rules.on_inbound("alice", "chat-gpt", Inbound(source="chatgpt:owner"))
        self.assertEqual(self.service.sent, [])
        self.assertEqual(self.service._store._connection.execute(
            "SELECT count(*) FROM auto_reply_deliveries").fetchone()[0], 0)

    def test_channel_manual_approval_lock_wins_over_enabled_rule(self):
        self.rules.create("alice", self.payload())
        self.service.manual_approval_required = lambda _: True
        self.service._store.conversation = lambda *_args, **_kwargs: {}
        self.rules.on_inbound("alice", "chat-a", Inbound())
        self.assertEqual(self.service.sent, [])

    def test_no_replay_self_bot_automatic_or_wrong_chat(self):
        self.rules.create("alice", self.payload())
        events = [
            Inbound(direction="outgoing"),
            Inbound(reconciliation=True),
            Inbound(sender_is_bot=True),
            Inbound(outbox_receipt="receipt"),
            Inbound(received_at=time.time() - 400),
            Inbound(received_at=time.time() + 400),
            Inbound(source="chatgpt:owner"),
        ]
        for event in events:
            self.rules.on_inbound("alice", "chat-a", event)
        self.rules.on_inbound("alice", "chat-b", Inbound())
        self.assertEqual(self.service.sent, [])


if __name__ == "__main__":
    unittest.main()
