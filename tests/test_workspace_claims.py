from __future__ import annotations

import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from universal_userio.contracts import InboxMessage
from universal_userio.http_api import handler
from universal_userio.mcp_catalog import TOOL_SPECS as MODULAR_TOOL_SPECS
from universal_userio.mcp_dispatch import UserIOToolDispatcher
from universal_userio.mcp_surface import UserIOMcpSurface
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore
from http.server import ThreadingHTTPServer


class Generator:
    def generate(self, *_args, **_kwargs):
        return []


class Outbox:
    def send_reply(self, **_kwargs):
        return "unused"


def receive_one(store: SQLiteUserIOStore) -> UserIOService:
    service = UserIOService(store, Generator(), Outbox())
    service.receive(
        InboxMessage("gmail:self", "lease-1", "sender@example.test", "claim me", 1.0),
        route_id="workspace-claim-test",
    )
    return service


def test_first_claim_wins_and_failed_or_expired_work_can_be_reclaimed(tmp_path) -> None:
    database = tmp_path / "userio.sqlite3"
    owner_store = SQLiteUserIOStore(database)
    receive_one(owner_store)
    owner_store.close()
    stores = [SQLiteUserIOStore(database), SQLiteUserIOStore(database)]
    barrier = threading.Barrier(2)
    results: list[dict | None] = [None, None]

    def claim(index: int) -> None:
        barrier.wait()
        results[index] = stores[index].claim_workspace_event(
            worker_id=f"worker-{index}", lease_seconds=600
        )

    threads = [threading.Thread(target=claim, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    winner = winners[0]
    claim_data = winner["claim"]
    winner_store = stores[int(str(claim_data["worker_id"]).removeprefix("worker-"))]
    with pytest.raises(PermissionError):
        winner_store.complete_workspace_claim(
            event_seq=claim_data["event_seq"], worker_id="other",
            lease_token=claim_data["lease_token"], detail="wrong owner",
        )
    failed = winner_store.fail_workspace_claim(
        event_seq=claim_data["event_seq"], worker_id=claim_data["worker_id"],
        lease_token=claim_data["lease_token"], detail="bounded failure",
    )
    assert failed["status"] == "failed"

    retry = stores[1].claim_workspace_event(worker_id="retry-worker", lease_seconds=600)
    assert retry is not None
    assert retry["claim"]["attempts"] == 2
    done = stores[1].complete_workspace_claim(
        event_seq=retry["claim"]["event_seq"], worker_id="retry-worker",
        lease_token=retry["claim"]["lease_token"], detail="handled",
    )
    assert done["status"] == "done"
    assert stores[0].claim_workspace_event(worker_id="late-worker") is None
    log = stores[0].workspace_claim_log(event_seq=retry["claim"]["event_seq"])
    assert [entry["action"] for entry in log["log"]] == [
        "claimed", "failed", "reclaimed", "completed",
    ]
    assert "lease_token" not in json.dumps(log)
    assert stores[0].new_messages(limit=10)[0]["message_id"] == "lease-1"


def test_expired_claim_is_reclaimed_and_stale_token_cannot_complete(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    receive_one(store)
    first = store.claim_workspace_event(worker_id="slow-worker", lease_seconds=30)
    assert first is not None
    with store._lock, store._connection:
        store._connection.execute(
            "UPDATE workspace_claims SET lease_expires_at=0 WHERE event_seq=?",
            (first["claim"]["event_seq"],),
        )
    second = store.claim_workspace_event(worker_id="replacement", lease_seconds=600)
    assert second is not None
    with pytest.raises(PermissionError):
        store.complete_workspace_claim(
            event_seq=first["claim"]["event_seq"], worker_id="slow-worker",
            lease_token=first["claim"]["lease_token"],
        )


def test_claim_after_skips_historical_events(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = receive_one(store)
    head = store.workspace_events()["head"]
    assert store.claim_workspace_event(worker_id="new-only", after=head) is None
    service.receive(
        InboxMessage("gmail:self", "lease-2", "new@example.test", "new event", 2.0),
        route_id="workspace-claim-test",
    )
    claimed = store.claim_workspace_event(worker_id="new-only", after=head)
    assert claimed is not None
    assert claimed["event"]["message_id"] == "lease-2"


def test_direct_only_claim_skips_telegram_groups_without_consuming_them(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    service.receive(
        InboxMessage("telegram", "-100123:10", "busy group", "group noise", 1.0),
        route_id="telegram",
    )
    service.receive(
        InboxMessage("telegram", "540308572:11", "private user", "direct message", 2.0),
        route_id="telegram",
    )

    direct = store.claim_workspace_event(
        worker_id="hermes", telegram_direct_only=True,
    )
    assert direct is not None
    assert direct["event"]["message_id"] == "540308572:11"

    group = store.claim_workspace_event(worker_id="group-worker")
    assert group is not None
    assert group["event"]["message_id"] == "-100123:10"


def test_durable_chat_exclusion_skips_claims_and_can_be_removed(tmp_path) -> None:
    database = tmp_path / "userio.sqlite3"
    store = SQLiteUserIOStore(database)
    service = UserIOService(store, Generator(), Outbox())
    excluded_id, _ = service.receive(
        InboxMessage("telegram", "-100123:10", "ignored group", "noise", 1.0),
        route_id="telegram",
    )
    service.receive(
        InboxMessage("gmail:self", "mail-1", "useful@example.test", "useful", 2.0),
        route_id="gmail-read-only",
    )

    added = store.add_workspace_exclusion(
        conversation_id=excluded_id, reason="owner requested",
    )
    assert added["conversation_id"] == excluded_id
    assert added["chat_name"] == "ignored group"
    assert added["reason"] == "owner requested"
    assert store.add_workspace_exclusion(
        conversation_id=excluded_id, reason="still ignored",
    )["created_at"] == added["created_at"]
    store.close()
    store = SQLiteUserIOStore(database)
    assert store.workspace_exclusions()[0]["reason"] == "still ignored"

    claimed = store.claim_workspace_event(worker_id="hermes")
    assert claimed is not None
    assert claimed["event"]["message_id"] == "mail-1"
    assert store.complete_workspace_claim(
        event_seq=claimed["claim"]["event_seq"], worker_id="hermes",
        lease_token=claimed["claim"]["lease_token"],
    )["status"] == "done"
    assert store.claim_workspace_event(worker_id="hermes") is None

    assert store.remove_workspace_exclusion(conversation_id=excluded_id) is True
    group = store.claim_workspace_event(worker_id="group-worker")
    assert group is not None
    assert group["event"]["message_id"] == "-100123:10"


def test_exclusions_are_user_scoped_and_removed_with_local_chat(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    owner_id, _ = service.receive(
        InboxMessage("telegram", "same-message", "owner chat", "owner text", 1.0),
        route_id="telegram",
    )
    user, _ = store.create_user("other_user", "other-password")
    store.bind_channel_route(user_id=user.user_id, source="telegram", route_id="telegram")
    other_id, _ = service.receive(
        InboxMessage("telegram", "same-message", "other chat", "other text", 2.0),
        route_id="telegram", user_id=user.user_id,
    )
    assert owner_id != other_id
    store.add_workspace_exclusion(conversation_id=owner_id)
    with pytest.raises(KeyError, match="conversation not found"):
        store.add_workspace_exclusion(conversation_id=owner_id, user_id=user.user_id)
    assert store.workspace_exclusions(user_id=user.user_id) == []
    assert store.claim_workspace_event(worker_id="owner-worker") is None
    assert store.claim_workspace_event(
        worker_id="other-worker", user_id=user.user_id,
    )["event"]["conversation_id"] == other_id
    assert store.delete_conversation(owner_id) is True
    assert store.workspace_exclusions() == []


@pytest.mark.parametrize("modular", [False, True])
def test_mcp_exclusion_tools_filter_claims_for_each_surface(tmp_path, modular: bool) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    conversation_id, _ = service.receive(
        InboxMessage("telegram", "group:1", "quiet chat", "noise", 1.0),
        route_id="telegram",
    )
    owner = store.owner()
    if modular:
        tool_names = {spec.name for spec in MODULAR_TOOL_SPECS}
        dispatch = lambda name, args: UserIOToolDispatcher(store, service).dispatch(
            name, args, principal=owner,
        )
    else:
        surface = UserIOMcpSurface(store, service)
        tool_names = {tool["name"] for tool in surface.tool_manifest()["tools"]}
        dispatch = lambda name, args: surface.dispatch(name, args, principal=owner)
    assert {
        "userio.workspace.exclusions.list", "userio.workspace.exclusions.add",
        "userio.workspace.exclusions.remove",
    } <= tool_names
    assert dispatch("userio.workspace.exclusions.add", {
        "conversation_id": conversation_id, "reason": "owner requested",
    })["exclusion"]["reason"] == "owner requested"
    listed = dispatch("userio.workspace.exclusions.list", {})["exclusions"]
    assert [item["conversation_id"] for item in listed] == [conversation_id]
    assert dispatch("userio.workspace.claim", {"worker_id": "automatic"}) == {
        "ok": True, "claimed": False,
    }
    assert dispatch("userio.workspace.exclusions.remove", {
        "conversation_id": conversation_id,
    }) == {"ok": True, "removed": True}
    assert dispatch("userio.workspace.claim", {"worker_id": "automatic"})["claimed"] is True


def test_mcp_claim_lifecycle_is_advertised_and_user_scoped(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = receive_one(store)
    surface = UserIOMcpSurface(store, service)
    names = {tool["name"] for tool in surface.tool_manifest()["tools"]}
    assert {
        "userio.workspace.claim", "userio.workspace.renew",
        "userio.workspace.complete", "userio.workspace.fail",
        "userio.workspace.claim_log", "userio.workspace.exclusions.list",
        "userio.workspace.exclusions.add", "userio.workspace.exclusions.remove",
    } <= names
    claimed = surface.dispatch(
        "userio.workspace.claim", {"worker_id": "codex-a", "lease_seconds": 600}
    )
    assert claimed["ok"] is True and claimed["claimed"] is True
    completed = surface.dispatch("userio.workspace.complete", {
        "event_seq": claimed["claim"]["event_seq"], "worker_id": "codex-a",
        "lease_token": claimed["claim"]["lease_token"], "detail": "done",
    })
    assert completed["claim"]["status"] == "done"


def test_http_claim_lifecycle_requires_auth_and_exposes_log(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = receive_one(store)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="owner-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def post(path: str, payload: dict, token: str = "owner-token") -> dict:
        request = Request(
            base + path, data=json.dumps(payload).encode(), method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urlopen(request) as response:
            return json.loads(response.read())

    try:
        with pytest.raises(HTTPError) as unauthenticated:
            urlopen(Request(base + "/v1/workspace/claims", data=b"{}", method="POST"))
        assert unauthenticated.value.code == 401
        claimed = post("/v1/workspace/claims", {"worker_id": "hermes", "lease_seconds": 600})
        assert claimed["claimed"] is True
        seq = claimed["claim"]["event_seq"]
        post(f"/v1/workspace/claims/{seq}/renew", {
            "worker_id": "hermes", "lease_token": claimed["claim"]["lease_token"],
            "lease_seconds": 600,
        })
        post(f"/v1/workspace/claims/{seq}/complete", {
            "worker_id": "hermes", "lease_token": claimed["claim"]["lease_token"],
            "detail": "sent to home chat",
        })
        request = Request(
            base + f"/v1/workspace/claims/{seq}",
            headers={"Authorization": "Bearer owner-token"},
        )
        with urlopen(request) as response:
            log = json.loads(response.read())
        assert log["claim"]["status"] == "done"
        assert [item["action"] for item in log["log"]] == [
            "claimed", "renewed", "completed",
        ]
    finally:
        server.shutdown()
        server.server_close()
