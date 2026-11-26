"""Single-use credential failures tested at the real broker/storage boundary."""
import pytest

from universal_userio.account_connections import ConnectorSecrets
from universal_userio.dion_auth import (
    DionAuthBroker, DionAuthError, DionIdentity, connect_dion,
)
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


def test_rotated_chain_survives_identity_service_outage_and_backend_restart(tmp_path):
    database = tmp_path / "db.sqlite3"
    vault = ConnectorSecrets(tmp_path / "secrets")
    store = SQLiteUserIOStore(database)
    service = UserIOService(store, object(), object())
    identity = DionIdentity("u", "o", "Fixture")
    account = connect_dion(service, store.default_user_id, {
        "access_token": "old-access", "refresh_token": "single-use-old",
        "expected_generation": 0,
    }, vault=vault, verifier=lambda *_: identity)["id"]
    consumed = []
    def rotate(token):
        consumed.append(token)
        assert consumed == ["single-use-old"]
        return {"access_token": "new-access", "refresh_token": "single-use-new"}
    def unavailable(*_):
        raise DionAuthError("temporary identity outage")
    broker = DionAuthBroker(service, store.default_user_id, vault=vault,
                            refresher=rotate, verifier=unavailable)
    with pytest.raises(DionAuthError, match="temporary"):
        broker.refresh(account)
    store.close()

    store = SQLiteUserIOStore(database)
    try:
        restarted = DionAuthBroker(UserIOService(store, object(), object()),
                                   store.default_user_id, vault=vault,
                                   refresher=rotate, verifier=lambda *_: identity)
        saved = restarted.load(account)
        assert saved["refresh_token"] == "single-use-new"
        assert saved["generation"] == 2
        assert restarted.access_token(account) == "new-access"
        assert consumed == ["single-use-old"]
    finally:
        store.close()


def test_ambiguous_refresh_is_not_replayed_or_mislabeled_reauth(tmp_path):
    vault = ConnectorSecrets(tmp_path / "secrets")
    store = SQLiteUserIOStore(tmp_path / "db.sqlite3")
    try:
        service = UserIOService(store, object(), object())
        identity = DionIdentity("u", "o", "Fixture")
        account = connect_dion(service, store.default_user_id, {
            "access_token": "old", "refresh_token": "one-time",
            "expected_generation": 0,
        }, vault=vault, verifier=lambda *_: identity)["id"]
        attempts = []
        def unavailable(token):
            attempts.append(token)
            raise DionAuthError("temporary network failure")
        broker = DionAuthBroker(service, store.default_user_id, vault=vault,
                                refresher=unavailable)
        with pytest.raises(DionAuthError, match="temporary"):
            broker.refresh(account)
        assert broker.load(account)["state"] == "refresh_pending"
        with pytest.raises(DionAuthError, match="unknown"):
            broker.refresh(account)
        assert attempts == ["one-time"]
    finally:
        store.close()


def _competing_refresh(database, secret_root, start, output, marker):
    store = SQLiteUserIOStore(database)
    try:
        service = UserIOService(store, object(), object())
        def rotate(token):
            with open(marker, "a") as evidence:
                evidence.write("refresh\n")
            return {"access_token": "new", "refresh_token": "rotated"}
        broker = DionAuthBroker(service, store.default_user_id,
                                vault=ConnectorSecrets(secret_root), refresher=rotate,
                                verifier=lambda *_: DionIdentity("u", "o", "Fixture"))
        start.wait(5)
        result = broker.refresh("dion:o:u", rejected_access_token="old")
        output.put(result["generation"])
    finally:
        store.close()


def test_two_backend_processes_consume_refresh_once(tmp_path):
    import multiprocessing
    database = tmp_path / "db.sqlite3"
    secret_root = tmp_path / "secrets"
    store = SQLiteUserIOStore(database)
    connect_dion(UserIOService(store, object(), object()), store.default_user_id, {
        "access_token": "old", "refresh_token": "one-time",
        "expected_generation": 0,
    }, vault=ConnectorSecrets(secret_root),
        verifier=lambda *_: DionIdentity("u", "o", "Fixture"))
    store.close()
    ctx = multiprocessing.get_context("fork")
    start, output = ctx.Event(), ctx.Queue()
    marker = tmp_path / "refresh-count"
    workers = [ctx.Process(target=_competing_refresh,
                          args=(database, secret_root, start, output, marker)) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        start.set()
        assert [output.get(timeout=10), output.get(timeout=10)] == [2, 2]
        for worker in workers:
            worker.join(5)
            assert worker.exitcode == 0
        assert marker.read_text() == "refresh\n"
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            worker.join(5)
