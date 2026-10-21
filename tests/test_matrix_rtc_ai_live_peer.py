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
    base = {"phone:+70000000000": "00000000-0000-0000-0000-000000000001"}
    first = PEER._fixture_identity_map(base, caller_a)
    assert first["matrix:" + caller_a] == base["phone:+70000000000"]
    assert "matrix:" + caller_a not in base
    assert "matrix:" + caller_b not in first

    with pytest.raises(PermissionError):
        PEER._fixture_identity_map(base, "@someone-else:chat.bezrabotnyi.com")
    with pytest.raises(PermissionError):
        PEER._fixture_identity_map({**base, "phone:+79999999999": "another-owner"}, caller_a)
    with pytest.raises(PermissionError):
        PEER._fixture_identity_map({**base, "matrix:" + caller_a: "another-owner"}, caller_a)


def test_secretary_mode_evidence_requires_recorded_sdk_transitions():
    log = """
    [agentcall] voice_mode call=x turn=1 mode=secretary
    [agentcall] voice_mode call=x turn=2 mode=incidents
    [agentcall] voice_mode call=x turn=3 mode=secretary
    """
    assert PEER._modes_seen(log) == ["incidents", "secretary"]
    assert PEER._modes_seen("[agentcall] voice_mode call=x mode=secretary") == ["secretary"]
