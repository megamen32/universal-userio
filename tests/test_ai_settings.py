"""Per-user BYOK AI settings."""
import json

from universal_userio.store import SQLiteUserIOStore


def test_ai_settings_roundtrip_and_validation(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "u.sqlite3")
    owner = store.seed_owner("owner", "password123")
    assert store.ai_settings(user_id=owner.user_id) is None

    store.set_ai_settings(
        endpoint="https://api.minimax.io/v1", model="MiniMax-M2.7",
        token="sk-user-key", user_id=owner.user_id,
    )
    settings = store.ai_settings(user_id=owner.user_id)
    assert settings == {
        "endpoint": "https://api.minimax.io/v1", "model": "MiniMax-M2.7", "token": "sk-user-key",
    }

    try:
        store.set_ai_settings(endpoint="ftp://nope", model="m", token="k", user_id=owner.user_id)
    except ValueError:
        pass
    else:
        raise AssertionError("non-http endpoint must be rejected")

    assert store.clear_ai_settings(user_id=owner.user_id) is True
    assert store.ai_settings(user_id=owner.user_id) is None


def test_ai_settings_are_per_user(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "u.sqlite3")
    owner = store.seed_owner("owner", "password123")
    other, _token = store.create_user("someone", "password456")
    store.set_ai_settings(endpoint="https://a.example/v1", model="m1", token="k1", user_id=other.user_id)
    assert store.ai_settings(user_id=owner.user_id) is None
    assert store.ai_settings(user_id=other.user_id)["model"] == "m1"
