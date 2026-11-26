from __future__ import annotations

import json
import stat

import pytest

from universal_userio.account_connections import ConnectorSecrets
from universal_userio.dion_auth import (
    DionGenerationConflict,
    DionIdentity,
    connect_dion,
    dion_account_states,
    parse_identity,
)
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


@pytest.fixture
def context(tmp_path, monkeypatch):
    root = tmp_path / "secrets"
    monkeypatch.setenv("USERIO_CONNECTION_SECRETS_DIR", str(root))
    store = SQLiteUserIOStore(tmp_path / "db.sqlite3")
    service = UserIOService(store, object(), object())
    second, _ = store.create_user("dion-second", "correct-horse-battery-staple")
    yield service, store, second.user_id, root
    store.close()


def identity(access: str, refresh: str) -> DionIdentity:
    assert access.startswith("access-")
    assert refresh.startswith("refresh-")
    return DionIdentity("u-123", "org-456", "DION User")


def test_parse_identity_accepts_nested_whoami():
    result = parse_identity({
        "user": {"id": "u-123", "displayName": "Alice"},
        "organization": {"id": "org-456"},
    })
    assert result == DionIdentity("u-123", "org-456", "Alice")


def test_first_handoff_registers_native_account_without_exposing_tokens(context):
    service, store, _, root = context
    vault = ConnectorSecrets(root)
    result = connect_dion(service, store.default_user_id, {
        "access_token": "access-one",
        "refresh_token": "refresh-one",
        "expected_generation": 0,
    }, vault=vault, verifier=identity)
    assert result["id"] == "dion:org-456:u-123"
    assert result["generation"] == 1
    account = next(a for a in store.accounts() if a["id"] == result["id"])
    assert account["provider"] == "dion"
    assert account["capabilities"] == ["read", "reply"]
    assert "access-one" not in json.dumps(account)
    secret = vault.load(account["credential_ref"], user_id=store.default_user_id, provider="dion")
    assert secret["access_token"] == "access-one"
    assert secret["refresh_token"] == "refresh-one"
    assert secret["generation"] == 1
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in root.iterdir())


def test_stale_browser_handoff_cannot_overwrite_backend_generation(context):
    service, store, _, root = context
    vault = ConnectorSecrets(root)
    first = connect_dion(service, store.default_user_id, {
        "access_token": "access-one",
        "refresh_token": "refresh-one",
        "expected_generation": 0,
    }, vault=vault, verifier=identity)
    account = next(a for a in store.accounts() if a["id"] == first["id"])
    original_ref = account["credential_ref"]

    second = connect_dion(service, store.default_user_id, {
        "access_token": "access-two",
        "refresh_token": "refresh-two",
        "expected_generation": 1,
    }, vault=vault, verifier=identity)
    assert second["generation"] == 2

    with pytest.raises(DionGenerationConflict) as error:
        connect_dion(service, store.default_user_id, {
            "access_token": "access-stale",
            "refresh_token": "refresh-stale",
            "expected_generation": 1,
        }, vault=vault, verifier=identity)
    assert error.value.current_generation == 2

    current = next(a for a in store.accounts() if a["id"] == first["id"])
    secret = vault.load(current["credential_ref"], user_id=store.default_user_id, provider="dion")
    assert secret["access_token"] == "access-two"
    assert secret["refresh_token"] == "refresh-two"
    assert not (root / (original_ref.removeprefix("connector:") + ".json")).exists()


def test_same_dion_identity_is_isolated_per_userio_owner(context):
    service, store, second_user, root = context
    vault = ConnectorSecrets(root)
    a = connect_dion(service, store.default_user_id, {
        "access_token": "access-owner", "refresh_token": "refresh-owner",
        "expected_generation": 0,
    }, vault=vault, verifier=identity)
    b = connect_dion(service, second_user, {
        "access_token": "access-second", "refresh_token": "refresh-second",
        "expected_generation": 0,
    }, vault=vault, verifier=identity)
    assert a["id"] == b["id"]
    owner = next(x for x in store.accounts() if x["id"] == a["id"])
    other = next(x for x in store.accounts(user_id=second_user) if x["id"] == b["id"])
    assert owner["credential_ref"] != other["credential_ref"]
    with pytest.raises(PermissionError):
        vault.load(owner["credential_ref"], user_id=second_user, provider="dion")


def test_status_exposes_generation_not_credentials(context):
    service, store, _, root = context
    vault = ConnectorSecrets(root)
    connect_dion(service, store.default_user_id, {
        "access_token": "access-one", "refresh_token": "refresh-one",
        "expected_generation": 0,
    }, vault=vault, verifier=identity)
    states = dion_account_states(service, store.default_user_id, vault=vault)
    assert states == [{
        "id": "dion:org-456:u-123",
        "display_name": "DION User",
        "generation": 1,
        "state": "connected",
        "enabled": True,
    }]
    assert "access-one" not in json.dumps(states)
    assert "refresh-one" not in json.dumps(states)


def test_verify_identity_falls_back_to_real_dion_jwt_user_and_org_claims():
    import base64
    import json
    from universal_userio.dion_auth import verify_dion_identity

    payload = base64.urlsafe_b64encode(json.dumps({
        "user_id": "jwt-user",
        "org_id": "jwt-org",
        "exp": 4_102_444_800,
    }).encode()).decode().rstrip("=")
    token = "e30." + payload + ".signature"

    class Response:
        status = 200
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, limit=-1):
            return json.dumps({"displayName": "JWT User"}).encode()

    identity = verify_dion_identity(
        token, opener=lambda request, timeout=0: Response(),
    )
    assert identity.user_id == "jwt-user"
    assert identity.organization_id == "jwt-org"
    assert identity.display_name == "JWT User"


def test_password_reauth_replaces_current_chain_without_generation(context):
    service, store, _, root = context
    vault = ConnectorSecrets(root)
    calls = []
    def login(email, secret):
        calls.append((email, secret))
        n = len(calls)
        return {"access_token": f"access-{n}", "refresh_token": f"refresh-{n}"}
    first = connect_dion(service, store.default_user_id, {
        "email": "alice@example.org", "password": "x" * 20,
    }, vault=vault, verifier=identity, login=login)
    second = connect_dion(service, store.default_user_id, {
        "email": "alice@example.org", "password": "y" * 20,
    }, vault=vault, verifier=identity, login=login)
    assert first["generation"] == 1
    assert second["generation"] == 2
    account = next(a for a in store.accounts() if a["id"] == first["id"])
    secret = vault.load(account["credential_ref"], user_id=store.default_user_id, provider="dion")
    assert secret["access_token"] == "access-2"
    assert secret["refresh_token"] == "refresh-2"
    assert "password" not in secret


def test_imported_tokens_without_generation_are_refused(context):
    service, store, _, root = context
    with pytest.raises(Exception, match="expected_generation"):
        connect_dion(service, store.default_user_id, {
            "access_token": "access-one", "refresh_token": "refresh-one",
        }, vault=ConnectorSecrets(root), verifier=identity)
