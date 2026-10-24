"""Allow/deny proof for the scoped QA voice session (caller+room+transport+TTL)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
import unittest
from types import SimpleNamespace

from universal_userio.voice.agentcall_realtime_bridge import (
    QA_TEST_SCOPE_ENV,
    qa_guest_promotion,
    qa_test_admission,
)

CALLER = "@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com"
PEER = "@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com"
ROOM = "!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com"
OWNER_CALLER = "@bezrabotnyi:chat.bezrabotnyi.com"
OWNER_UUID = "5e438326-6339-4ac8-a328-59dae1d6241f"


def prepared(mode="qa_incoming", caller=CALLER, room=ROOM, transport="matrix"):
    return SimpleNamespace(agent_context={
        "call_mode": mode, "qa_caller": caller, "qa_room": room,
        "voice_transport": transport,
    })


def load_peer_fixture():
    """Load the peer harness module itself, without running any call."""
    import importlib.util
    location = Path(__file__).resolve().parent / "manual" / "matrix_rtc_ai_live_peer.py"
    spec = importlib.util.spec_from_file_location("matrix_rtc_ai_live_peer_fixture", location)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QaScopeAdmissionTest(unittest.TestCase):
    def setUp(self):
        self.previous = os.environ.get(QA_TEST_SCOPE_ENV)
        self.previous_map = os.environ.get("EXMANAGER_VOICE_IDENTITIES_FILE")

    def tearDown(self):
        if self.previous is None:
            os.environ.pop(QA_TEST_SCOPE_ENV, None)
        else:
            os.environ[QA_TEST_SCOPE_ENV] = self.previous
        if self.previous_map is None:
            os.environ.pop("EXMANAGER_VOICE_IDENTITIES_FILE", None)
        else:
            os.environ["EXMANAGER_VOICE_IDENTITIES_FILE"] = self.previous_map

    def _scope(self, expires_in=600, **overrides):
        expires = time.time() + expires_in
        entry = {"caller": CALLER, "room": ROOM, "transport": "matrix",
                 "participants": [CALLER, PEER], "expires_at": expires}
        entry.update(overrides)
        pair_entry = {"room": ROOM, "transport": "matrix", "promote_guest": True,
                      "participants": [CALLER, PEER], "expires_at": expires}
        os.environ[QA_TEST_SCOPE_ENV] = json.dumps({"entries": [entry, pair_entry]})

    def _pair_scope(self, expires_in=600, entry_participants=(CALLER, PEER),
                    pair_participants=(CALLER, PEER)):
        """A scope with the caller entry and the scoped pair entry."""
        expires = time.time() + expires_in
        entry = {"caller": CALLER, "room": ROOM, "transport": "matrix",
                 "participants": list(entry_participants), "expires_at": expires}
        pair_entry = {"room": ROOM, "transport": "matrix", "promote_guest": True,
                      "participants": list(pair_participants), "expires_at": expires}
        os.environ[QA_TEST_SCOPE_ENV] = json.dumps({"entries": [entry, pair_entry]})

    def test_admitted_for_exact_binding(self):
        self._scope()
        result = qa_test_admission("qa_incoming", prepared())
        self.assertTrue(result["allowed"], result)
        self.assertEqual(result["reason"], "qa_scope_admitted")

    def test_admitted_for_exact_room_transport_pair_and_live_expiry(self):
        self._pair_scope(expires_in=600)
        result = qa_test_admission("qa_incoming", prepared())
        self.assertTrue(result["allowed"], result)
        self.assertEqual(result["reason"], "qa_scope_admitted")

    def test_denied_when_entry_pair_missing_second_participant(self):
        self._pair_scope(entry_participants=(CALLER,))
        result = qa_test_admission("qa_incoming", prepared())
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "qa_scope_pair_incomplete")

    def test_denied_when_entry_pair_is_empty(self):
        self._pair_scope(entry_participants=())
        result = qa_test_admission("qa_incoming", prepared())
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "qa_scope_pair_incomplete")

    def test_denied_when_scope_pair_is_empty(self):
        self._pair_scope(pair_participants=())
        result = qa_test_admission("qa_incoming", prepared())
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "qa_scope_pair_incomplete")

    def test_denied_when_caller_is_not_part_of_the_pair(self):
        self._pair_scope(pair_participants=(PEER,))
        result = qa_test_admission("qa_incoming", prepared())
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "qa_scope_pair_incomplete")

    def test_denied_without_scope(self):
        os.environ.pop(QA_TEST_SCOPE_ENV, None)
        result = qa_test_admission("qa_incoming", prepared())
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "qa_scope_missing")

    def test_denied_unreadable_scope(self):
        os.environ[QA_TEST_SCOPE_ENV] = "{not json"
        result = qa_test_admission("qa_incoming", prepared())
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "qa_scope_unreadable")

    def test_denied_wrong_caller(self):
        self._scope()
        result = qa_test_admission("qa_incoming", prepared(caller="@someone_else:chat.bezrabotnyi.com"))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "qa_scope_no_match")

    def test_denied_wrong_room(self):
        self._scope()
        result = qa_test_admission("qa_incoming", prepared(room="!other:chat.bezrabotnyi.com"))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "qa_scope_no_match")

    def test_denied_wrong_transport(self):
        self._scope()
        result = qa_test_admission("qa_incoming", prepared(transport="phone"))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "qa_scope_no_match")

    def test_denied_incomplete_binding(self):
        self._scope()
        result = qa_test_admission("qa_incoming", prepared(caller=""))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "qa_binding_incomplete")

    def test_denied_expired_scope(self):
        self._scope(expires_in=-10)
        result = qa_test_admission("qa_incoming", prepared())
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "qa_scope_expired")

    def test_denied_owner_identity(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"matrix:" + OWNER_CALLER: OWNER_UUID,
                       "phone:phone:v1:test": OWNER_UUID}, handle)
            path = handle.name
        self.addCleanup(os.unlink, path)
        os.environ["EXMANAGER_VOICE_IDENTITIES_FILE"] = path
        self._scope()
        os.environ[QA_TEST_SCOPE_ENV] = json.dumps({
            "entries": [{"caller": OWNER_CALLER, "room": ROOM, "transport": "matrix",
                         "expires_at": time.time() + 600}],
            "identity_map": {OWNER_CALLER: OWNER_UUID},
        })
        result = qa_test_admission("qa_incoming", prepared(caller=OWNER_CALLER))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "qa_scope_is_owner_identity")

    def test_ordinary_guest_mode_is_never_qa(self):
        self._scope()
        result = qa_test_admission("incoming_guest", prepared(mode="incoming_guest"))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "not_qa_mode")

    def test_owner_incoming_mode_is_never_qa(self):
        self._scope()
        result = qa_test_admission("incoming", prepared(mode="incoming"))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "not_qa_mode")


class QaGuestPromotionTest(unittest.TestCase):
    """Scoped test pair may leave the guest branch; everything else stays guest."""

    ROOM = "!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com"
    A = "@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com"
    B = "@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com"

    def setUp(self):
        self.previous = os.environ.get(QA_TEST_SCOPE_ENV)
        self.previous_map = os.environ.get("EXMANAGER_VOICE_IDENTITIES_FILE")

    def tearDown(self):
        if self.previous is None:
            os.environ.pop(QA_TEST_SCOPE_ENV, None)
        else:
            os.environ[QA_TEST_SCOPE_ENV] = self.previous
        if self.previous_map is None:
            os.environ.pop("EXMANAGER_VOICE_IDENTITIES_FILE", None)
        else:
            os.environ["EXMANAGER_VOICE_IDENTITIES_FILE"] = self.previous_map

    def _prepared(self, peer_label, account_ref, transport="matrix", mode="incoming_guest"):
        return SimpleNamespace(
            agent_context={"call_mode": mode, "voice_transport": transport,
                           "voice_target": peer_label},
            peer_ref=self.ROOM, peer_label=peer_label, account_ref=account_ref)

    def _scope(self, expires_in=600, **overrides):
        entry = {"room": self.ROOM, "transport": "matrix", "promote_guest": True,
                 "participants": [self.A, self.B], "expires_at": time.time() + expires_in}
        entry.update(overrides)
        os.environ[QA_TEST_SCOPE_ENV] = json.dumps({"entries": [entry]})

    def test_promoted_for_scoped_pair(self):
        self._scope()
        result = qa_guest_promotion(self._prepared(self.A, "matrix:37698708b623db45e1bfe61f4d01"))
        self.assertTrue(result["allowed"], result)
        self.assertEqual(result["reason"], "qa_guest_promoted")

    def test_not_promoted_without_scope(self):
        os.environ.pop(QA_TEST_SCOPE_ENV, None)
        result = qa_guest_promotion(self._prepared(self.A, "matrix:37698708b623db45e1bfe61f4d01"))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "qa_scope_missing")

    def test_not_promoted_for_other_room(self):
        self._scope(room="!other:chat.bezrabotnyi.com")
        result = qa_guest_promotion(self._prepared(self.A, "matrix:37698708b623db45e1bfe61f4d01"))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "qa_promotion_no_match")

    def test_not_promoted_for_other_transport(self):
        self._scope(transport="phone")
        result = qa_guest_promotion(self._prepared(self.A, "matrix:37698708b623db45e1bfe61f4d01"))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "qa_promotion_no_match")

    def test_not_promoted_for_unlisted_participant(self):
        self._scope()
        result = qa_guest_promotion(self._prepared("@stranger:chat.bezrabotnyi.com", "matrix:x"))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "qa_promotion_no_match")

    def test_not_promoted_when_expired(self):
        self._scope(expires_in=-5)
        result = qa_guest_promotion(self._prepared(self.A, "matrix:37698708b623db45e1bfe61f4d01"))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "qa_scope_expired")


class QaScopeBuilderTest(unittest.TestCase):
    """The peer harness must emit exactly the scoped QA binding, with no call."""

    def setUp(self):
        self.peer = load_peer_fixture()

    def test_builder_emits_expected_scope(self):
        scope = self.peer.build_qa_scope(CALLER)
        entry = scope["entries"][0]
        self.assertEqual(entry["caller"], CALLER)
        self.assertEqual(entry["room"], ROOM)
        self.assertEqual(entry["transport"], "matrix")
        self.assertEqual(sorted(entry["participants"]), sorted([CALLER, PEER]))
        self.assertGreater(entry["expires_at"], time.time())
        pair_entry = scope["entries"][1]
        self.assertTrue(pair_entry["promote_guest"])
        self.assertEqual(pair_entry["room"], ROOM)
        self.assertEqual(pair_entry["transport"], "matrix")
        self.assertEqual(sorted(pair_entry["participants"]), sorted([CALLER, PEER]))
        self.assertEqual(scope["identity_map"],
                         {CALLER: "9b4ab5e7-546e-4df9-a4c9-5c28f850cd26"})

    def test_builder_expiry_is_live_and_bounded(self):
        before = time.time()
        scope = self.peer.build_qa_scope(CALLER)
        after = time.time()
        expires = scope["entries"][0]["expires_at"]
        self.assertGreaterEqual(expires, before + 900)
        self.assertLessEqual(expires, after + 900)

    def test_builder_scope_is_admitted_by_the_engine_seam(self):
        scope = self.peer.build_qa_scope(CALLER)
        previous = os.environ.get(QA_TEST_SCOPE_ENV)
        os.environ[QA_TEST_SCOPE_ENV] = json.dumps(scope)
        try:
            result = qa_test_admission("qa_incoming", prepared())
        finally:
            if previous is None:
                os.environ.pop(QA_TEST_SCOPE_ENV, None)
            else:
                os.environ[QA_TEST_SCOPE_ENV] = previous
        self.assertTrue(result["allowed"], result)
        self.assertEqual(result["reason"], "qa_scope_admitted")

    def test_builder_readback_reports_the_binding(self):
        scope = self.peer.build_qa_scope(CALLER)
        readback = self.peer.qa_scope_readback(scope, self.peer.QA_SCOPE_TTL_SECONDS)
        self.assertEqual(readback["event"], "qa_scope")
        self.assertEqual(readback["room"], ROOM)
        self.assertEqual(readback["transport"], "matrix")
        self.assertEqual(sorted(readback["participants"]), sorted([CALLER, PEER]))
        self.assertGreater(readback["expires_at"], time.time())
        self.assertEqual(readback["ttl_seconds"], 900)
        self.assertEqual(readback["identity_map_keys"], [CALLER])

    def test_builder_refuses_other_room(self):
        with self.assertRaises(ValueError):
            self.peer.build_qa_scope(CALLER, room="!other:chat.bezrabotnyi.com")

    def test_builder_refuses_other_transport(self):
        with self.assertRaises(ValueError):
            self.peer.build_qa_scope(CALLER, transport="phone")

    def test_builder_refuses_other_pair(self):
        with self.assertRaises(ValueError):
            self.peer.build_qa_scope(
                CALLER, participants=(CALLER, "@stranger:chat.bezrabotnyi.com"))

    def test_builder_refuses_caller_outside_the_pair(self):
        with self.assertRaises(ValueError):
            self.peer.build_qa_scope("@stranger:chat.bezrabotnyi.com")

    def test_builder_refuses_expired_ttl(self):
        with self.assertRaises(ValueError):
            self.peer.build_qa_scope(CALLER, ttl_seconds=0)


if __name__ == "__main__":
    unittest.main()
