import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).parent / "manual/start_matrix_rtc_fixture_call.py"
SPEC = importlib.util.spec_from_file_location("start_matrix_rtc_fixture_call", SCRIPT)
CALLER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CALLER
SPEC.loader.exec_module(CALLER)


def test_call_helpers_are_fixed_to_two_fixture_directions():
    assert set(CALLER.FIXTURES) == {"a", "b"}
    for alias, fixture in CALLER.FIXTURES.items():
        payload = CALLER._payload(fixture, "matrix:fixture-account")
        assert payload["room_id"] == CALLER.ROOM
        assert payload["request_id"] == fixture["request_id"]
        assert payload["confirm"] is True
        assert "agent_context" not in payload
        assert payload["target"] != fixture["matrix_id"]


def test_call_id_is_stable_for_exact_fixture_request():
    user_id = "user_test_fixture"
    request_id = CALLER.FIXTURES["a"]["request_id"]
    first = CALLER._call_id(user_id, request_id)
    assert first == CALLER._call_id(user_id, request_id)
    assert first != CALLER._call_id("user_other", request_id)


def test_ephemeral_personal_token_adapter_revokes_its_token(tmp_path, monkeypatch):
    from universal_userio.personal_tokens import PersonalTokens
    from universal_userio.store import SQLiteUserIOStore

    database = tmp_path / "fixtures.sqlite3"
    owner_store = SQLiteUserIOStore(database)
    user, _ = owner_store.create_user("call-fixture", "fixture-password-long")
    PersonalTokens(owner_store)  # Mirrors the live HTTP handler schema setup.
    monkeypatch.setattr(CALLER, "DB", str(database))
    adapter = CALLER._ExistingFixtureStore()
    try:
        tokens = PersonalTokens(adapter)
        issued = tokens.create(user.user_id, "temporary test", scope="full_access")
        assert owner_store.authenticate_token(issued["token"]).user_id == user.user_id
        assert tokens.revoke(user.user_id, issued["id"]) is True
        assert owner_store.authenticate_token(issued["token"]) is None
    finally:
        adapter.close()
        owner_store.close()
