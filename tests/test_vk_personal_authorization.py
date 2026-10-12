import json
from urllib.parse import quote

import pytest

from universal_userio.channels.vk_authorization import resolve_authorization


def callback(**changes):
    data = dict(type="silent_token", token="fresh-private-silent-token", uuid="exact-attempt",
                ttl=600, user={"id": 42})
    data.update(changes)
    return "https://oauth.vk.ru/blank.html#payload=" + quote(json.dumps(data))


def test_silent_exchange_uses_exact_provider_uuid_and_anonymous_token():
    calls = []
    def provider(method, **fields):
        calls.append((method, fields))
        if method == "auth.getAnonymToken":
            return {"token": "private-anonymous-token"}
        return {"access_token": "private-personal-token", "user_id": 42, "expires_in": 3600}
    token, uid, ttl = resolve_authorization(callback(), call=provider)
    assert (token, uid, ttl) == ("private-personal-token", "42", 3600)
    assert calls[1] == ("auth.exchangeSilentAuthToken", dict(
        access_token="private-anonymous-token", token="fresh-private-silent-token", uuid="exact-attempt"))


def test_raw_access_token_does_not_exchange_or_invent_owner():
    assert resolve_authorization("private-personal-token", call=lambda *_: pytest.fail()) == (
        "private-personal-token", "", None)


@pytest.mark.parametrize("url", [
    "http://oauth.vk.ru/blank.html#access_token=private-personal-token",
    "https://evil.test/blank.html#access_token=private-personal-token",
    "https://oauth.vk.ru:444/blank.html#access_token=private-personal-token",
    "https://user@oauth.vk.ru/blank.html#access_token=private-personal-token",
    "https://oauth.vk.ru/blank.html#access_token=private-personal-token&access_token=other-private-token",
    callback(type="other"), callback(ttl=0), callback(user={"id": True}),
])
def test_invalid_callback_never_contacts_provider(url):
    with pytest.raises(ValueError):
        resolve_authorization(url, call=lambda *_: pytest.fail("invalid callback contacted provider"))


@pytest.mark.parametrize("extra", [{"user_id": 43}, {"is_partial": True}, {"additional_signup_required": True}])
def test_wrong_identity_and_partial_exchange_are_rejected(extra):
    def provider(method, **fields):
        if method == "auth.getAnonymToken": return {"token": "private-anonymous-token"}
        return {"access_token": "private-personal-token", "user_id": 42, **extra}
    with pytest.raises(ValueError): resolve_authorization(callback(), call=provider)


def test_import_form_payload_checks_real_identity_before_saving(tmp_path, monkeypatch):
    from universal_userio import account_connections as conn
    from universal_userio.service import UserIOService
    from universal_userio.store import SQLiteUserIOStore
    from test_vk_personal_messenger import StubVK
    monkeypatch.setenv("USERIO_CONNECTION_SECRETS_DIR", str(tmp_path / "secrets"))
    store = SQLiteUserIOStore(tmp_path / "store.db")
    service = UserIOService(store, object(), object())
    value = "https://oauth.vk.ru/blank.html#access_token=private-personal-token&user_id=42&expires_in=3600"
    try:
        account = conn.connect_vk_personal(service, store.default_user_id,
                                           {"authorization": value}, reader_factory=StubVK)
        assert account["id"] == "vk:42"
        bound = next(a for a in store.accounts() if a["id"] == "vk:42")
        saved = conn.ConnectorSecrets().load(bound["credential_ref"], user_id=store.default_user_id, provider="vk")
        assert saved["access_token"] == "private-personal-token"
        assert float(saved["expires_at"]) > 0
        with pytest.raises(ValueError, match="другому"):
            conn.connect_vk_personal(service, store.default_user_id,
                                     {"authorization": value.replace("user_id=42", "user_id=43")},
                                     reader_factory=StubVK)
        assert conn.ConnectorSecrets().load(bound["credential_ref"], user_id=store.default_user_id, provider="vk") == saved
    finally:
        store.close()
