"""Allow/deny proof for the scoped QA voice session (caller+room+transport+TTL)."""
from __future__ import annotations

import json
import os
import time
import unittest
from types import SimpleNamespace

from universal_userio.voice.agentcall_realtime_bridge import (
    QA_TEST_SCOPE_ENV,
    qa_guest_promotion,
    qa_test_admission,
)

CALLER = "@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com"
ROOM = "!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com"
OWNER_CALLER = "@bezrabotnyi:chat.bezrabotnyi.com"
OWNER_UUID = "5e438326-6339-4ac8-a328-59dae1d6241f"


def prepared(mode="qa_incoming", caller=CALLER, room=ROOM, transport="matrix"):
    return SimpleNamespace(agent_context={
        "call_mode": mode, "qa_caller": caller, "qa_room": room,
        "voice_transport": transport,
    })


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
        entry = {"caller": CALLER, "room": ROOM, "transport": "matrix",
                 "expires_at": time.time() + expires_in}
        entry.update(overrides)
        os.environ[QA_TEST_SCOPE_ENV] = json.dumps({"entries": [entry]})

    def test_admitted_for_exact_binding(self):
        self._scope()
        result = qa_test_admission("qa_incoming", prepared())
        self.assertTrue(result["allowed"], result)
        self.assertEqual(result["reason"], "qa_scope_admitted")

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


if __name__ == "__main__":
    unittest.main()
