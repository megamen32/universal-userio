import importlib.util
import sys
from pathlib import Path
import pytest


SCRIPT = Path(__file__).parent / "manual/matrix_rtc_ai_live_peer.py"
SPEC = importlib.util.spec_from_file_location("matrix_rtc_ai_live_peer", SCRIPT)
PEER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PEER
SPEC.loader.exec_module(PEER)


def test_secretary_identity_gate_requires_exact_fixture_caller(tmp_path, monkeypatch):
    caller_a = PEER.FIXTURES["a"]["matrix_id"]
    caller_b = PEER.FIXTURES["b"]["matrix_id"]
    aiadmin = "@aiadmin:chat.bezrabotnyi.com"
    owner_id = "00000000-0000-0000-0000-000000000001"
    base = {"phone:+70000000000": owner_id, "matrix:" + aiadmin: owner_id}
    first = PEER._fixture_identity_map(base, caller_a)
    assert first["matrix:" + caller_a] == PEER.QA_SCOPE_IDENTITY_MAP[caller_a]
    assert first["matrix:" + caller_a] != owner_id
    assert "matrix:" + caller_a not in base
    assert "matrix:" + caller_b not in first
    assert all(not key.startswith("matrix:") or value != owner_id
               for key, value in first.items())

    scoped_aiadmin = PEER._fixture_identity_map(base, aiadmin)
    assert scoped_aiadmin["matrix:" + aiadmin] == PEER.QA_SCOPE_IDENTITY_MAP[aiadmin]
    assert PEER.QA_SCOPE_IDENTITY_MAP[aiadmin] == PEER.QA_SCOPE_IDENTITY_MAP[caller_a]
    assert PEER.QA_SCOPE_IDENTITY_MAP[aiadmin] == "9b4ab5e7-546e-4df9-a4c9-5c28f850cd26"
    assert scoped_aiadmin["matrix:" + aiadmin] != owner_id

    with pytest.raises(PermissionError):
        PEER._fixture_identity_map(base, "@someone-else:chat.bezrabotnyi.com")
    with pytest.raises(PermissionError):
        PEER._fixture_identity_map({**base, "phone:+79999999999": "another-owner"}, caller_a)
    with pytest.raises(PermissionError):
        PEER._fixture_identity_map({**base, "matrix:" + caller_a: "another-owner"}, caller_a)


def test_qa_scope_allows_only_the_owner_approved_aiadmin_fixture_pair():
    aiadmin = "@aiadmin:chat.bezrabotnyi.com"
    fixture_b = PEER.FIXTURES["b"]["matrix_id"]
    room = "!WcdQRAMzHhrxcZzQad:chat.bezrabotnyi.com"
    scope = PEER.build_qa_scope(aiadmin, room=room, participants=(aiadmin, fixture_b),
                                now=1000)
    assert scope["entries"][0]["room"] == room
    assert set(scope["entries"][0]["participants"]) == {aiadmin, fixture_b}
    assert scope["identity_map"] == {aiadmin: PEER.QA_SCOPE_IDENTITY_MAP[aiadmin]}
    with pytest.raises(ValueError, match="qa_scope_pair_not_allowed"):
        PEER.build_qa_scope(aiadmin, room=room, participants=(aiadmin, fixture_b, "@extra:chat.bezrabotnyi.com"))
    with pytest.raises(ValueError, match="qa_scope_room_not_allowed"):
        PEER.build_qa_scope(aiadmin, room="!random:chat.bezrabotnyi.com",
                            participants=(aiadmin, fixture_b))


def test_secretary_mode_evidence_requires_recorded_sdk_transitions():
    log = """
    [agentcall] voice_mode call=x turn=1 mode=secretary
    [agentcall] voice_mode call=x turn=2 mode=incidents
    [agentcall] voice_mode call=x turn=3 mode=secretary
    """
    assert PEER._modes_seen(log) == ["incidents", "secretary"]
    assert PEER._modes_seen("[agentcall] voice_mode call=x mode=secretary") == ["secretary"]
