from __future__ import annotations

import json
import os
from pathlib import Path
import time

import pytest

from universal_userio.account_connections import ConnectorSecrets
from universal_userio.dion_auth import DionIdentity, connect_dion
from universal_userio.dion_calls import DionCalls
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


def identity(access, refresh):
    return DionIdentity("user-1", "org-1", "DION Owner")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    secrets = tmp_path / "secrets"
    monkeypatch.setenv("USERIO_CONNECTION_SECRETS_DIR", str(secrets))
    store = SQLiteUserIOStore(tmp_path / "db.sqlite3")
    service = UserIOService(store, object(), object())
    account = connect_dion(
        service, store.default_user_id,
        {"access_token": "access", "refresh_token": "refresh", "expected_generation": 0},
        vault=ConnectorSecrets(secrets), verifier=identity,
    )
    helper = tmp_path / "fake-helper"
    helper.write_text("""#!/usr/bin/env python3
import json,sys
for line in sys.stdin:
    r=json.loads(line)
    if r['action']=='leave':
        out={'request_id':r['request_id'],'status':'closed','room_id':r['room_id']}
    else:
        out={'request_id':r['request_id'],'status':'connected','room_id':'event-1','room_slug':'slug-1'}
    print(json.dumps(out),flush=True)
""")
    os.chmod(helper, 0o700)
    yield service, store, account["id"], helper, tmp_path
    store.close()


def wait_for(manager, call_id, user_id, status, timeout=3):
    deadline=time.monotonic()+timeout
    record=None
    while time.monotonic()<deadline:
        record=manager.get(call_id,user_id)
        if record["status"]==status:
            return record
        time.sleep(.02)
    pytest.fail(f"call status did not become {status}: {record}")


def test_dion_call_create_is_durable_and_stop_closes_same_helper(setup):
    service, store, account_id, helper, root = setup
    manager = DionCalls(service, root=root/"calls", helper_path=helper)
    record = manager.start(
        user_id=store.default_user_id, account_ref=account_id,
        request_id="request-dion-000001", confirm=True, max_seconds=30,
    )
    active = wait_for(manager, record["call_id"], store.default_user_id, "active")
    assert active["room_id"]=="event-1"
    assert active["room_slug"]=="slug-1"

    replay = manager.start(
        user_id=store.default_user_id, account_ref=account_id,
        request_id="request-dion-000001", confirm=True, max_seconds=30,
    )
    assert replay["call_id"]==record["call_id"]
    assert len(manager.active)==1

    manager.stop(record["call_id"], store.default_user_id)
    done = wait_for(manager, record["call_id"], store.default_user_id, "completed")
    assert done["room_id"]=="event-1"


def test_dion_call_request_id_conflict_does_not_start_second_call(setup):
    service, store, account_id, helper, root = setup
    manager = DionCalls(service, root=root/"calls", helper_path=helper)
    record = manager.start(
        user_id=store.default_user_id, account_ref=account_id,
        request_id="request-dion-000002", room_id="room-a", isolated_tunnel=True,
        confirm=True, max_seconds=30,
    )
    wait_for(manager, record["call_id"], store.default_user_id, "active")
    with pytest.raises(ValueError, match="conflict"):
        manager.start(
            user_id=store.default_user_id, account_ref=account_id,
            request_id="request-dion-000002", room_id="room-b", isolated_tunnel=True,
            confirm=True, max_seconds=30,
        )
    manager.stop(record["call_id"], store.default_user_id)
    wait_for(manager, record["call_id"], store.default_user_id, "completed")


def test_dion_call_requires_explicit_confirmation(setup):
    service, store, account_id, helper, root = setup
    manager = DionCalls(service, root=root/"calls", helper_path=helper)
    with pytest.raises(ValueError, match="confirmation"):
        manager.start(
            user_id=store.default_user_id, account_ref=account_id,
            request_id="request-dion-000003", confirm=False,
        )


def test_dion_call_tools_are_exposed_with_correct_read_write_annotations(setup):
    from universal_userio.mcp_surface import UserIOMcpSurface
    service, store, account_id, helper, root = setup
    surface = UserIOMcpSurface(store, service)
    tools = {item["name"]: item for item in surface.tool_manifest()["tools"]}
    assert "userio.calls.dion.start" in tools
    assert "userio.calls.dion.get" in tools
    assert "userio.calls.dion.stop" in tools
    assert tools["userio.calls.dion.get"]["annotations"]["readOnlyHint"] is True
    assert tools["userio.calls.dion.start"]["annotations"]["readOnlyHint"] is False


def test_dion_refuses_ordinary_shared_meeting_and_reports_data_only(setup):
    service, store, account_id, helper, root = setup
    manager = DionCalls(service, root=root/"calls", helper_path=helper)
    with pytest.raises(ValueError, match="ordinary DION audio"):
        manager.start(user_id=store.default_user_id, account_ref=account_id,
                      request_id="request-dion-ordinary", confirm=True, room_id="ordinary")
    record = manager.start(user_id=store.default_user_id, account_ref=account_id,
                           request_id="request-dion-isolated", confirm=True)
    assert record["audio_supported"] is False
    assert record["media_mode"] == "vp8_data_tunnel"
    wait_for(manager, record["call_id"], store.default_user_id, "active")
    manager.stop(record["call_id"], store.default_user_id)
    wait_for(manager, record["call_id"], store.default_user_id, "completed")
