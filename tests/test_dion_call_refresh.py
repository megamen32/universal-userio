"""Verify Python retries only an explicitly rejected setup, never an unknown call."""
import json
import os
import threading

from universal_userio.account_connections import ConnectorSecrets
from universal_userio.dion_auth import DionAuthBroker, DionIdentity, connect_dion
from universal_userio.dion_auth import DionAuthError
from universal_userio.dion_calls import DionCalls
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


def test_temporary_broker_failure_does_not_require_reauthentication(tmp_path, monkeypatch):
    manager = DionCalls(object(), root=tmp_path / "calls")
    def unavailable(*args):
        raise DionAuthError("temporary provider outage")
    monkeypatch.setattr(DionAuthBroker, "load", unavailable)
    record = {"call_id": "fixture", "user_id": "owner", "account_ref": "dion:o:u"}
    slot = {"stop": threading.Event(), "process": None, "io_lock": threading.Lock()}
    manager._run(record, slot)
    assert record["status"] == "failed"
    assert record["error"] == "DionAuthError"


def test_auth_expired_after_create_refreshes_then_joins_existing_room(tmp_path, monkeypatch):
    monkeypatch.setenv("USERIO_CONNECTION_SECRETS_DIR", str(tmp_path / "secrets"))
    store = SQLiteUserIOStore(tmp_path / "db.sqlite3")
    service = UserIOService(store, object(), object())
    account = connect_dion(service, store.default_user_id, {
        "access_token": "access", "refresh_token": "refresh", "expected_generation": 0,
    }, vault=ConnectorSecrets(), verifier=lambda *_: DionIdentity("u", "o", "Fixture"))
    attempts = tmp_path / "attempts"
    helper = tmp_path / "helper"
    helper.write_text('''#!/usr/bin/env python3
import json,sys
for index,line in enumerate(sys.stdin):
    r=json.loads(line)
    with open(''' + repr(str(attempts)) + ''','a') as f: f.write(r['action']+'\\n')
    assert 'refresh_token' not in r
    status='auth_expired' if index==0 else ('closed' if r['action']=='leave' else 'connected')
    print(json.dumps(dict(request_id=r['request_id'],status=status,room_id='event',room_slug='slug')),flush=True)
''')
    os.chmod(helper, 0o700)
    refreshes = []
    def refresh(self, account_id, *, rejected_access_token=None):
        refreshes.append((account_id, rejected_access_token))
        return {"access_token": "fresh"}
    monkeypatch.setattr(DionAuthBroker, "refresh", refresh)
    manager = DionCalls(service, root=tmp_path / "calls", helper_path=helper)
    record = {"call_id": "fixture", "user_id": store.default_user_id,
              "account_ref": account["id"], "action": "create", "room_id": "",
              "max_seconds": 30, "status": "accepted"}
    stop = threading.Event()
    slot = {"stop": stop, "process": None, "io_lock": threading.Lock()}
    manager.active["fixture"] = slot
    save = manager._save
    def save_and_stop(record):
        save(record)
        if record.get("status") == "active":
            stop.set()
    manager._save = save_and_stop
    try:
        manager._run(record, slot)
        assert record["status"] == "completed"
        assert attempts.read_text().splitlines() == ["create", "join", "leave"]
        assert refreshes == [(account["id"], "access")]
    finally:
        store.close()
