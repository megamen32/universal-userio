"""Offline tests of the isolated question coordination candidate.

No Telegram, outgoing messages, network, real accounts or actual human approvals.
The journal double models the existing UserIO outgoing receipt contract and is
REPLACED by its real adapter only after owner-coordinated backend integration.
"""
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from universal_userio.question_coordination import (
    AccessDenied, Actor, Binding, Busy, QuestionCoordination,
    Rejected, RevisionConflict,
)


class QuestionCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, "userio-test.db")
        self.conn = self.connect()
        self.now = [1000.0]
        self.result_refs = {"private-result-ref-1", "neighbor-result", "authorized-result-ref"}
        self.human_approved = True  # trusted existing human-approval fixture
        self.allow_sender = True    # trusted server-side live permission fixture
        self.actors = {
            "dot": Actor("tenant-1", "user-1", "dot", "authenticated-dot",
                         frozenset({"acct-1"}), frozenset({"manager"}), True),
            "manager": Actor("tenant-1", "user-1", "manager", "authenticated-manager",
                             frozenset({"acct-1"}), frozenset({"manager"})),
            "foreign": Actor("tenant-1", "user-2", "manager", "other-user",
                             frozenset({"acct-1"}), frozenset({"manager"})),
            "wrong-account": Actor("tenant-1", "user-1", "manager", "wrong-account",
                                   frozenset({"acct-other"}), frozenset({"manager"})),
            "wrong-project": Actor("tenant-1", "user-1", "manager", "wrong-project",
                                   frozenset({"acct-1"}), frozenset({"unrelated"})),
            "wrong-tenant": Actor("tenant-other", "user-1", "manager", "wrong-tenant",
                                   frozenset({"acct-1"}), frozenset({"manager"})),
        }
        with self.conn:
            self.conn.executescript("""
                CREATE TABLE workspace_events (
                  seq INTEGER PRIMARY KEY, user_id TEXT, source TEXT,
                  message_id TEXT, conversation_id TEXT, eligible INTEGER);
                CREATE TABLE messages (
                  user_id TEXT, source TEXT, message_id TEXT, sender TEXT,
                  direction TEXT, sender_is_bot INTEGER, edited_at REAL);
                CREATE TABLE conversations (
                  user_id TEXT, id TEXT, account_ref TEXT, peer_id TEXT,route_id TEXT);
                CREATE TABLE existing_outgoing_journal (
                  request_id TEXT PRIMARY KEY, question_id TEXT NOT NULL,
                  state TEXT NOT NULL,receipt_ref TEXT);
            """)
            self.conn.execute("INSERT INTO conversations VALUES (?,?,?,?,?)",
                              ("user-1", "conv-1", "acct-1", "peer-1", "bound-route-1"))
            self.conn.execute("INSERT INTO conversations VALUES (?,?,?,?,?)",
                              ("user-2", "conv-other", "acct-1", "peer-x", "bound-route-x"))
            self.add_event(1, "user-1", "conv-1")
            self.add_event(2, "user-1", "conv-1")
            self.add_event(3, "user-2", "conv-other")
            self.add_event(4, "user-1", "conv-1", bot=True)
        self.engine = self.make_engine(self.conn)
        self.engine.install()

    def connect(self):
        return sqlite3.connect(self.path, timeout=4, check_same_thread=False)

    def add_event(self, seq, user, conv, *, bot=False):
        mid = "message-" + str(seq)
        self.conn.execute("INSERT INTO workspace_events VALUES (?,?,?,?,?,?)",
                          (seq, user, "telegram", mid, conv, 1))
        self.conn.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
                          (user, "telegram", mid, "real-author-" + str(seq),
                           "incoming", int(bot), None))

    def make_engine(self, db):
        def resolve_actor(context):
            if context == "revoked":
                raise AccessDenied("revoked")
            if context not in self.actors:
                raise AccessDenied("authenticated_service_required")
            return self.actors[context]

        def resolve_binding(actor, source):
            # In production this comes ONLY from UserIO saved signed bindings.
            if source["route_id"] not in {"bound-route-1", "bound-route-x"}:
                raise AccessDenied("unverified_route")
            return Binding("manager", "verified-binding:" + source["route_id"],
                           frozenset({"dot", "manager"}))

        def reserve_outgoing(conn, source, request_id):
            # Mock of the existing journal, not a second production outbox.
            conn.execute("INSERT INTO existing_outgoing_journal VALUES (?,?,?,?)",
                         (request_id, source["question_id"], "in_flight", None))

        def read_receipt(source, request_id):
            row = db.execute("""
                SELECT state,receipt_ref FROM existing_outgoing_journal
                WHERE request_id=? AND question_id=?
            """, (request_id, source["question_id"])).fetchone()
            if row:
                return {"state": row[0], "receipt_ref": row[1]}
            return None

        return QuestionCoordination(
            db, threading.RLock(), tenant="tenant-1",
            token_key=b"test-existing-app-secret-32-bytes-long!!",
            resolve_actor=resolve_actor, resolve_binding=resolve_binding,
            journal_reserve=reserve_outgoing, journal_read=read_receipt,
            result_exists=lambda source, ref: ref in self.result_refs,
            delivery_authorized=lambda actor, source, req: (
                self.human_approved and self.allow_sender
                and source["binding_id"].startswith("verified-binding:")
                and actor.subject.startswith("authenticated-")
            ),
            clock=lambda: self.now[0],
        )

    def claim(self, who="manager", seq=1, req="acquire-1", revision="0", **kwargs):
        return self.engine.acquire(who, seq, revision, req, **kwargs)

    def save(self, c, req="result-1", who="manager", seq=1, ref="private-result-ref-1"):
        return self.engine.result_saved(who, seq, "0", c["lease_token"], c["epoch"], req, ref)

    def reserve(self, c, req="delivery-1", who="manager", seq=1):
        return self.engine.reserve_delivery(who, seq, "0", c["lease_token"], c["epoch"], req)

    def settle(self, request_id, state="sent", ref="receipt-1"):
        self.conn.execute("UPDATE existing_outgoing_journal SET state=?,receipt_ref=? WHERE request_id=?",
                          (state, ref, request_id))
        self.conn.commit()

    def journal_count(self):
        return self.conn.execute("SELECT COUNT(*) FROM existing_outgoing_journal").fetchone()[0]

    def test_01_one_result_and_defined_receipt(self):
        c = self.claim()
        self.save(c)
        self.reserve(c)
        self.settle("delivery-1")
        r = self.engine.reconcile_delivery("manager", 1, "0", "receipt-check-1")
        self.assertEqual("sent", r["delivery_state"])
        done = self.engine.complete("manager", 1, "0", c["lease_token"], c["epoch"], "done-1")
        self.assertEqual(("completed", "private-result-ref-1", "receipt-1"),
                         (done["state"], done["result_ref"], done["receipt_ref"]))
        self.assertEqual(1, self.journal_count())
        self.assertEqual(1, sum(x["action"] == "completed" for x in self.engine.audit("manager", 1)))

    def test_02_race_dot_manager_has_one_winner(self):
        # Two separate SQLite connections and independent mutexes simulate processes.
        a, b = self.make_engine(self.connect()), self.make_engine(self.connect())
        gate = threading.Barrier(2)
        def run(engine, who):
            gate.wait()
            try:
                return engine.acquire(who, 1, "0", "race-" + who)["holder"]
            except Busy:
                return "busy"
        with ThreadPoolExecutor(max_workers=2) as pool:
            x = pool.submit(run, a, "dot")
            y = pool.submit(run, b, "manager")
            result = [x.result(), y.result()]
        self.assertEqual(1, result.count("busy"))
        self.assertEqual(1, sum(v in {"dot", "manager"} for v in result))
        self.assertEqual(0, self.journal_count())

    def test_03_retry_and_payload_conflict(self):
        a = self.claim()
        b = self.claim()
        self.assertEqual(a["lease_token"], b["lease_token"])
        self.assertTrue(b["idempotent"])
        with self.assertRaises(Busy):
            self.claim(req="new-attempt")
        self.save(a)
        self.reserve(a)
        self.assertEqual("in_flight", self.reserve(a)["state"])
        self.assertEqual(1, self.journal_count())
        with self.assertRaises(Rejected):
            self.engine.pause("dot", 1, "0", "acquire-1", "changed-payload")

    def test_04_expiry_new_holder_old_epoch_fenced(self):
        a = self.claim()
        self.now[0] += 301
        b = self.claim(who="dot", req="acquire-after-expiry")
        self.assertEqual(a["epoch"] + 1, b["epoch"])
        with self.assertRaises((AccessDenied, Rejected)):
            self.engine.renew("manager", 1, "0", a["lease_token"], a["epoch"], "stale-renew")
        with self.assertRaises((AccessDenied, Rejected)):
            self.reserve(a)
        self.assertIn("expired", [x["action"] for x in self.engine.audit("dot", 1)])

    def test_05_unknown_ttl_and_handoff_never_replay(self):
        a = self.claim()
        self.save(a)
        self.reserve(a)
        self.now[0] += 301
        x = self.engine.handoff("dot", 1, "0", "handoff-1", "dot")
        self.assertEqual("transferring", x["state"])
        with self.assertRaises(Busy):
            self.claim(who="dot", req="resend")
        with self.assertRaises(Busy):
            self.engine.resume("dot", 1, "0", "resume-while-unknown")
        r = self.engine.reconcile_delivery("dot", 1, "0", "receipt-unknown")
        self.assertEqual("unknown", r["delivery_state"])
        self.assertEqual(1, self.journal_count())

    def test_06_pause_only_selected_question(self):
        a = self.claim()
        b = self.claim(seq=2, req="neighbor")
        x = self.engine.pause("dot", 1, "0", "pause-1")
        self.assertEqual("paused", x["state"])
        with self.assertRaises(Rejected):
            self.save(a)
        self.save(b, req="save-neighbor", seq=2, ref="neighbor-result")
        self.assertEqual(2, self.conn.execute("SELECT COUNT(*) FROM question_coordination").fetchone()[0])

    def test_07_pending_question_same_id_no_duplicate_card(self):
        c = self.claim(pending_question="existing-pending-question", card_id="existing-card-3122894f")
        row = self.conn.execute("SELECT pending_question,card_id FROM question_coordination").fetchone()
        self.assertEqual(("existing-pending-question", "existing-card-3122894f"), row)
        self.engine.pause("dot", 1, "0", "pause-owner-answer")
        transfer = self.engine.handoff("dot", 1, "0", "handoff-owner-answer", "dot")
        self.assertEqual("handoff", transfer["state"])
        resumed = self.engine.resume("dot", 1, "0", "resume-owner-answer")
        self.assertEqual(c["question_id"], resumed["question_id"])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM question_coordination").fetchone()[0])

    def test_08_crash_after_result_saved_reuses_original_receipt(self):
        c = self.claim()
        self.save(c)
        # Simulate process replacement after the saved result.
        fresh = self.make_engine(self.connect())
        row = fresh.db.execute("SELECT result_ref FROM question_coordination").fetchone()
        self.assertEqual("private-result-ref-1", row[0])
        fresh.reserve_delivery("manager", 1, "0", c["lease_token"], c["epoch"], "delivery-1")
        self.settle("delivery-1", "sent", "original-receipt")
        receipt = fresh.reconcile_delivery("manager", 1, "0", "reconcile-after-crash")
        self.assertEqual("original-receipt", receipt["receipt_ref"])
        self.assertEqual(1, self.journal_count())

    def test_09_forged_account_project_revocation_denied(self):
        with self.assertRaises(AccessDenied):
            self.claim(who="unknown")
        with self.assertRaises(AccessDenied):
            self.claim(who="foreign")
        with self.assertRaises(AccessDenied):
            self.claim(who="wrong-account")
        with self.assertRaises(AccessDenied):
            self.claim(who="wrong-project")
        with self.assertRaises(AccessDenied):
            self.claim(who="wrong-tenant")
        c = self.claim()
        with self.assertRaises(AccessDenied):
            self.engine.result_saved("revoked", 1, "0", c["lease_token"], c["epoch"],
                                     "revoked-use", "should-not-save")
        self.assertIsNone(self.conn.execute(
            "SELECT result_ref FROM question_coordination").fetchone()[0])

    def test_10_edited_source_revision_conflicts(self):
        c = self.claim()
        self.conn.execute("UPDATE messages SET edited_at=1010 WHERE message_id='message-1'")
        self.conn.commit()
        with self.assertRaises(RevisionConflict):
            self.engine.result_saved("manager", 1, "0", c["lease_token"], c["epoch"],
                                     "stale-revision", "not-saved")
        with self.assertRaises(RevisionConflict):
            self.claim(req="new-edited-claim", revision="1010.0")
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM question_coordination").fetchone()[0])

    def test_11_bot_default_deny_and_single_consumer(self):
        with self.assertRaises(AccessDenied):
            self.claim(seq=4, req="bot-claim")
        c = self.claim()
        self.assertEqual("manager", c["holder"])
        self.assertEqual(0, self.journal_count())
        with self.assertRaises(Rejected):
            self.engine.complete("manager", 1, "0", c["lease_token"], c["epoch"], "fake-approval")
        self.assertEqual(0, self.journal_count())

    def test_12_successful_handoff_fences_old_token(self):
        a = self.claim()
        h = self.engine.handoff("manager", 1, "0", "handoff-to-dot", "dot")
        self.assertGreater(h["epoch"], a["epoch"])
        with self.assertRaises(Rejected):
            self.engine.result_saved("manager", 1, "0", a["lease_token"], a["epoch"],
                                     "old-writer", "unsafe")
        b = self.engine.resume("dot", 1, "0", "dot-resume")
        self.assertEqual("dot", b["holder"])
        self.engine.result_saved("dot", 1, "0", b["lease_token"], b["epoch"],
                                 "dot-result", "authorized-result-ref")
        self.assertIn("handoff", [x["action"] for x in self.engine.audit("dot", 1)])

    def test_13_real_send_boundary_rechecks_fence_and_is_idempotent(self):
        a = self.claim()
        self.save(a)
        self.reserve(a)
        calls = []
        def trusted_transport(src, request_id):
            calls.append((src["binding_id"], request_id))
            # A REAL provider adapter writes via another connection: network
            # send must NOT pin UserIO's SQLite writer transaction.
            conn = self.connect()
            try:
                with conn:
                    conn.execute("""
                        UPDATE existing_outgoing_journal SET state='sent',receipt_ref='provider-receipt'
                        WHERE request_id=?
                    """, (request_id,))
            finally:
                conn.close()
        r = self.engine.send_fenced(
            "manager", 1, "0", a["lease_token"], a["epoch"], "dispatch-1",
            trusted_transport,
        )
        self.assertEqual("sent", r["delivery_state"])
        # Same request ID returns a stored outcome and NEVER invokes transport.
        again = self.engine.send_fenced(
            "manager", 1, "0", a["lease_token"], a["epoch"], "dispatch-1",
            trusted_transport,
        )
        self.assertTrue(again["idempotent"])
        self.assertEqual(1, len(calls))
        self.assertEqual(1, self.journal_count())
        self.engine.complete("manager", 1, "0", a["lease_token"], a["epoch"], "completed-1")

    def test_14_unknown_transport_failure_remains_fenced(self):
        a = self.claim()
        self.save(a)
        self.reserve(a)
        attempts = []
        def uncertain_transport(src, request_id):
            attempts.append(request_id)
            raise RuntimeError("provider answer unknown")
        r = self.engine.send_fenced(
            "manager", 1, "0", a["lease_token"], a["epoch"], "dispatch-1",
            uncertain_transport,
        )
        self.assertEqual("unknown", r["delivery_state"])
        self.engine.send_fenced("manager", 1, "0", a["lease_token"],
                                a["epoch"], "dispatch-1", uncertain_transport)
        self.assertEqual(1, len(attempts))
        h = self.engine.handoff("dot", 1, "0", "handoff-after-unknown", "dot")
        self.assertEqual("transferring", h["state"])
        with self.assertRaises(Busy):
            self.claim("dot", req="no-replay")

    def test_15_cannot_save_nonexistent_result_reference(self):
        a = self.claim()
        with self.assertRaises(Rejected):
            self.save(a, ref="invented-result-ref")
        self.assertIsNone(self.conn.execute(
            "SELECT result_ref FROM question_coordination").fetchone()[0])

    def test_16_rejected_action_audited_without_lease_secret(self):
        a = self.claim()
        self.engine.handoff("manager", 1, "0", "handoff-x", "dot")
        with self.assertRaises(Rejected):
            self.engine.result_saved("manager", 1, "0", a["lease_token"], a["epoch"],
                                     "rejected-save", "private-result-ref-1")
        audit = self.engine.audit("dot", 1)
        rejected = [row for row in audit if row["action"] == "rejected"]
        self.assertEqual(1, len(rejected))
        self.assertEqual("authenticated-manager", rejected[0]["actor"])
        self.assertEqual("telegram", rejected[0]["source"])
        self.assertNotIn(a["lease_token"], repr(audit))

    def test_17_handoff_during_send_preserves_original_receipt(self):
        first = self.claim()
        self.save(first)
        self.reserve(first)
        started, finish = threading.Event(), threading.Event()
        calls = []
        def trusted_transport(src, request_id):
            calls.append(request_id)
            started.set()
            if not finish.wait(timeout=3):
                raise RuntimeError("fixture did not release send")
            conn = self.connect()
            try:
                with conn:
                    conn.execute("""
                        UPDATE existing_outgoing_journal
                        SET state='sent',receipt_ref='confirmed-original'
                        WHERE request_id=?
                    """, (request_id,))
            finally:
                conn.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            task = pool.submit(self.engine.send_fenced, "manager", 1, "0",
                               first["lease_token"], first["epoch"],
                               "send-before-handoff", trusted_transport)
            self.assertTrue(started.wait(timeout=3), "send never began")
            # Must not deadlock behind external network wait.
            h = self.engine.handoff("dot", 1, "0", "handoff-mid-send", "dot")
            self.assertEqual("transferring", h["state"])
            finish.set()
            result = task.result(timeout=4)
        self.assertEqual(("handoff", "dot", "confirmed-original"),
                         (result["state"], result["holder"], result["receipt_ref"]))
        self.assertEqual(1, len(calls))
        self.assertEqual(1, self.journal_count())
        with self.assertRaises((Rejected, AccessDenied)):
            self.engine.renew("manager", 1, "0", first["lease_token"],
                              first["epoch"], "post-handoff-renew")

    def test_18_request_id_bound_to_real_caller_identity(self):
        a = self.claim()
        with self.assertRaises(Rejected):
            self.claim(who="dot")
        log = self.engine.audit("manager", 1)
        self.assertEqual(1, len([x for x in log if x["action"] == "rejected"]))
        self.assertEqual("manager", a["holder"])

    def test_19_human_approval_required_before_outgoing_reservation(self):
        c = self.claim()
        self.save(c)
        self.human_approved = False
        with self.assertRaises(AccessDenied):
            self.reserve(c)
        self.assertEqual(0, self.journal_count())
        self.assertIsNone(self.conn.execute(
            "SELECT delivery_request_id FROM question_coordination").fetchone()[0])
        self.human_approved = True
        self.reserve(c)
        self.assertEqual(1, self.journal_count())

    def test_20_revoked_send_permission_fences_before_provider_io(self):
        c = self.claim()
        self.save(c)
        self.reserve(c)
        self.allow_sender = False
        dispatched = []
        with self.assertRaises(AccessDenied):
            self.engine.send_fenced("manager", 1, "0", c["lease_token"],
                                    c["epoch"], "dispatch-when-revoked",
                                    lambda *args: dispatched.append(args))
        self.assertEqual([], dispatched)
        self.assertEqual("in_flight", self.conn.execute(
            "SELECT delivery_state FROM question_coordination").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
