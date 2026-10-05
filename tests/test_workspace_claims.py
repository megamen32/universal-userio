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

    assert store.claim_workspace_event(worker_id="group-worker") is None


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
    # Removing an exclusion does not enable a group or replay old arrivals.
    assert store.claim_workspace_event(worker_id="group-worker") is None


def test_exclusions_are_user_scoped_and_removed_with_local_chat(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    owner_id, _ = service.receive(
        InboxMessage("telegram", "same-message", "owner chat", "owner text", 1.0,
                     conversation_kind="direct"),
        route_id="telegram",
    )
    user, _ = store.create_user("other_user", "other-password")
    store.bind_channel_route(user_id=user.user_id, source="telegram", route_id="telegram")
    other_id, _ = service.receive(
        InboxMessage("telegram", "same-message", "other chat", "other text", 2.0,
                     conversation_kind="direct"),
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
        InboxMessage("telegram", "42:1", "quiet chat", "noise", 1.0),
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
    assert dispatch("userio.workspace.claim", {"worker_id": "automatic"}) == {
        "ok": True, "claimed": False,
    }
    service.receive(
        InboxMessage("telegram", "42:2", "quiet chat", "fresh", 2.0),
        route_id="telegram",
    )
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


@pytest.mark.parametrize("modular", [False, True])
def test_policy_defaults_rules_and_no_historical_flood(tmp_path, modular: bool) -> None:
    database = tmp_path / "userio.sqlite3"
    store = SQLiteUserIOStore(database)
    service = UserIOService(store, Generator(), Outbox())
    owner = store.owner()
    if modular:
        dispatch = lambda name, args: UserIOToolDispatcher(store, service).dispatch(
            name, args, principal=owner)
    else:
        surface = UserIOMcpSurface(store, service)
        dispatch = lambda name, args: surface.dispatch(name, args, principal=owner)
    group_id, _ = service.receive(
        InboxMessage("telegram", "-1007:1", "Project", "old group", 1.0,
                     conversation_kind="group", peer_id="-1007"),
        route_id="telegram", account_ref="telegram:11",
    )
    channel_id, _ = service.receive(
        InboxMessage("telegram", "-1008:1", "News", "old channel", 2.0,
                     conversation_kind="channel", peer_id="-1008"),
        route_id="telegram", account_ref="telegram:11",
    )
    unknown_id, _ = service.receive(
        InboxMessage("telegram", "opaque:1", "Mystery", "old unknown", 3.0),
        route_id="telegram", account_ref="telegram:11",
    )
    direct_id, _ = service.receive(
        InboxMessage("telegram", "21:1", "Alice", "old direct", 4.0,
                     conversation_kind="direct", peer_id="21"),
        route_id="telegram", account_ref="telegram:11",
    )
    defaults = dispatch("userio.workspace.policy.get", {})["defaults"]
    assert defaults == {"direct": True, "group": False, "channel": False, "unknown": False}
    assert [event["message_id"] for event in store.workspace_events()["events"]] == ["telegram:11|21:1"]
    assert {group_id, channel_id, unknown_id, direct_id} == {
        chat["conversation_id"] for chat in dispatch("userio.workspace.policy.chats.list", {})["chats"]}

    dispatch("userio.workspace.policy.set_default", {"conversation_kind": "group", "enabled": True})
    assert store.claim_workspace_event(worker_id="worker")["event"]["message_id"] == "telegram:11|21:1"
    assert store.claim_workspace_event(worker_id="worker") is None
    service.receive(
        InboxMessage("telegram", "-1007:2", "Renamed Project", "new group", 5.0,
                     conversation_kind="group", peer_id="-1007"),
        route_id="telegram", account_ref="telegram:11",
    )
    assert store.claim_workspace_event(worker_id="worker")["event"]["message_id"] == "telegram:11|-1007:2"
    assert dispatch("userio.workspace.policy.evaluate", {"conversation_id": group_id})["chat"]["allowed"]
    assert dispatch("userio.workspace.policy.chats.list", {
        "account_ref": "telegram:11", "peer_id": "-1007",
    })["chats"][0]["chat_name"] == "Renamed Project"

    dispatch("userio.workspace.policy.chats.set", {
        "conversation_id": direct_id, "action": "ignore", "reason": "noise"})
    assert not dispatch("userio.workspace.policy.evaluate", {"conversation_id": direct_id})["chat"]["allowed"]
    dispatch("userio.workspace.policy.chats.set", {"conversation_id": channel_id, "action": "allow"})
    assert store.claim_workspace_event(worker_id="worker") is None
    service.receive(
        InboxMessage("telegram", "-1008:2", "News", "new channel", 6.0,
                     conversation_kind="channel", peer_id="-1008"),
        route_id="telegram", account_ref="telegram:11",
    )
    assert store.claim_workspace_event(worker_id="worker")["event"]["message_id"] == "telegram:11|-1008:2"
    assert dispatch("userio.workspace.policy.chats.set", {
        "conversation_id": channel_id, "action": "inherit"})["chat"]["allowed"] is False
    assert store.claim_workspace_event(worker_id="worker") is None
    store.close()
    reopened = SQLiteUserIOStore(database)
    assert reopened.workspace_policy()["defaults"]["group"] is True
    assert reopened.evaluate_workspace_chat(conversation_id=direct_id)["action"] == "ignore"


def test_telegram_identity_is_account_and_peer_scoped(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    first, _ = service.receive(
        InboxMessage("telegram", "51:1", "Same Title", "first", 1.0, peer_id="51"),
        route_id="telegram", account_ref="telegram:11")
    second, _ = service.receive(
        InboxMessage("telegram", "52:1", "Same Title", "second", 2.0, peer_id="52"),
        route_id="telegram", account_ref="telegram:11")
    third, _ = service.receive(
        InboxMessage("telegram", "51:1", "Same Title", "third", 3.0, peer_id="51"),
        route_id="telegram", account_ref="telegram:22")
    assert len({first, second, third}) == 3
    assert {chat["account_ref"] for chat in store.workspace_chat_rules()} == {
        "telegram:11", "telegram:22"}
    store.set_workspace_chat_rule(conversation_id=first, action="ignore")
    assert store.evaluate_workspace_chat(conversation_id=second)["allowed"]
    assert store.evaluate_workspace_chat(conversation_id=third)["allowed"]
    assert store.message("telegram:11|51:1", source="telegram")["body"] == "first"
    assert store.message("telegram:22|51:1", source="telegram")["body"] == "third"
    listed = UserIOMcpSurface(store, service).dispatch("userio.workspace.policy.chats.list", {
        "source": "telegram", "account_ref": "telegram:22", "peer_id": "51", "limit": 10,
    })["chats"]
    assert [(row["conversation_id"], row["peer_id"], row["chat_name"])
            for row in listed] == [(third, "51", "Same Title")]


def test_legacy_telegram_exclusion_survives_account_peer_rekey(tmp_path) -> None:
    database = tmp_path / "userio.sqlite3"
    store = SQLiteUserIOStore(database)
    service = UserIOService(store, Generator(), Outbox())
    legacy_id, _ = service.receive(
        InboxMessage("telegram", "-10099:1", "Old Name", "old", 1.0),
        route_id="telegram")
    store.set_conversation_account(legacy_id, "telegram:11")
    store.add_workspace_exclusion(conversation_id=legacy_id, reason="old exclusion")
    with store._lock, store._connection:
        store._connection.execute("DELETE FROM settings WHERE key='workspace_conversation_kind_backfilled_v1'")
    store.close()
    store = SQLiteUserIOStore(database)
    service = UserIOService(store, Generator(), Outbox())
    renamed_id, _ = service.receive(
        InboxMessage("telegram", "-10099:2", "New Name", "new", 2.0,
                     conversation_kind="group", peer_id="-10099"),
        route_id="telegram", account_ref="telegram:11")
    assert renamed_id == legacy_id
    assert store.evaluate_workspace_chat(conversation_id=legacy_id)["action"] == "ignore"
    assert store.claim_workspace_event(worker_id="worker") is None


def test_legacy_telegram_ignore_is_copied_to_new_account_scoped_chat(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    legacy_id, _ = service.receive(
        InboxMessage("telegram", "-10077:1", "Legacy", "old", 1.0,
                     peer_id="-10077", conversation_kind="group"),
        route_id="telegram")
    store.add_workspace_exclusion(conversation_id=legacy_id, reason="owner ignored legacy peer")

    scoped_id, _ = service.receive(
        InboxMessage("telegram", "-10077:2", "Current", "new", 2.0,
                     peer_id="-10077", conversation_kind="group"),
        route_id="telegram", account_ref="telegram:11")
    assert scoped_id != legacy_id
    scoped = store.evaluate_workspace_chat(conversation_id=scoped_id)
    assert scoped["account_ref"] == "telegram:11"
    assert scoped["action"] == "ignore"
    assert scoped["reason"] == "owner ignored legacy peer"
    assert store.claim_workspace_event(worker_id="worker") is None

    # Once the account-scoped chat is explicitly allowed, the legacy row does
    # not overwrite that decision on every subsequent arrival.
    store.set_workspace_chat_rule(conversation_id=scoped_id, action="allow")
    service.receive(
        InboxMessage("telegram", "-10077:3", "Current", "fresh", 3.0,
                     peer_id="-10077", conversation_kind="group"),
        route_id="telegram", account_ref="telegram:11")
    assert store.evaluate_workspace_chat(conversation_id=scoped_id)["action"] == "allow"
    assert store.claim_workspace_event(worker_id="worker")["event"]["message_id"] == "telegram:11|-10077:3"


def test_whatsapp_group_and_unclassified_vk_are_disabled_by_default(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    group_id, _ = service.receive(
        InboxMessage("whatsapp", "g1", "123@g.us", "group", 1.0), route_id="whatsapp")
    vk_id, _ = service.receive(
        InboxMessage("vk", "v1", "peer", "unknown", 2.0), route_id="vk")
    assert store.evaluate_workspace_chat(conversation_id=group_id)["conversation_kind"] == "group"
    assert store.evaluate_workspace_chat(conversation_id=vk_id)["conversation_kind"] == "unknown"
    assert store.workspace_events()["events"] == []
    store.set_workspace_chat_rule(conversation_id=group_id, action="allow")
    assert store.claim_workspace_event(worker_id="worker") is None
    service.receive(InboxMessage("whatsapp", "g2", "123@g.us", "new", 3.0), route_id="whatsapp")
    assert store.claim_workspace_event(worker_id="worker")["event"]["message_id"] == "g2"


def test_provider_group_identity_overrides_contradictory_direct_hint(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    telegram_id, _ = service.receive(
        InboxMessage("telegram", "-10088:1", "Telegram group", "noise", 1.0,
                     conversation_kind="direct", peer_id="-10088"),
        route_id="telegram", account_ref="telegram:11")
    whatsapp_id, _ = service.receive(
        InboxMessage("whatsapp", "w1", "123@g.us", "noise", 2.0,
                     conversation_kind="direct", peer_id="123@g.us"),
        route_id="whatsapp")
    assert store.evaluate_workspace_chat(conversation_id=telegram_id)["conversation_kind"] == "unknown"
    assert store.evaluate_workspace_chat(conversation_id=whatsapp_id)["conversation_kind"] == "group"
    assert store.claim_workspace_event(worker_id="worker") is None


def test_policy_disable_irreversibly_retires_pending_events(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    store.set_workspace_default(conversation_kind="group", enabled=True)
    group_id, _ = service.receive(
        InboxMessage("telegram", "-10071:1", "Group", "queued", 1.0,
                     conversation_kind="group", peer_id="-10071"),
        route_id="telegram", account_ref="telegram:11")

    store.set_workspace_default(conversation_kind="group", enabled=False)
    store.set_workspace_default(conversation_kind="group", enabled=True)
    assert store.claim_workspace_event(worker_id="worker") is None

    service.receive(
        InboxMessage("telegram", "-10071:2", "Group", "fresh", 2.0,
                     conversation_kind="group", peer_id="-10071"),
        route_id="telegram", account_ref="telegram:11")
    assert store.claim_workspace_event(worker_id="worker")["event"]["message_id"] == "telegram:11|-10071:2"

    direct_id, _ = service.receive(
        InboxMessage("telegram", "72:1", "Direct", "queued direct", 3.0,
                     conversation_kind="direct", peer_id="72"),
        route_id="telegram", account_ref="telegram:11")
    store.set_workspace_chat_rule(conversation_id=direct_id, action="ignore")
    store.set_workspace_chat_rule(conversation_id=direct_id, action="allow")
    # The old direct event cannot reappear after ignore -> allow.
    assert store.claim_workspace_event(worker_id="worker-2") is None

    service.receive(
        InboxMessage("telegram", "72:2", "Direct", "fresh direct", 4.0,
                     conversation_kind="direct", peer_id="72"),
        route_id="telegram", account_ref="telegram:11")
    assert store.claim_workspace_event(worker_id="worker-2")["event"]["message_id"] == "telegram:11|72:2"


def test_policy_migration_does_not_enable_historical_group_or_unknown_events(tmp_path) -> None:
    database = tmp_path / "userio.sqlite3"
    store = SQLiteUserIOStore(database)
    service = UserIOService(store, Generator(), Outbox())
    direct_id, _ = service.receive(
        InboxMessage("email", "d1", "owner@example.test", "direct", 1.0), route_id="email")
    group_id, _ = service.receive(
        InboxMessage("whatsapp", "g1", "123@g.us", "group", 2.0), route_id="whatsapp")
    unknown_id, _ = service.receive(
        InboxMessage("vk", "u1", "peer", "unknown", 3.0), route_id="vk")
    with store._lock, store._connection:
        store._connection.execute("ALTER TABLE workspace_events RENAME TO old_workspace_events")
        store._connection.execute("""CREATE TABLE workspace_events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,source TEXT NOT NULL,
            message_id TEXT NOT NULL,conversation_id TEXT NOT NULL,
            UNIQUE(user_id,source,message_id))""")
        store._connection.execute("""INSERT INTO workspace_events(user_id,source,message_id,conversation_id)
            SELECT user_id,source,message_id,conversation_id FROM old_workspace_events""")
        store._connection.execute("DROP TABLE old_workspace_events")
        store._connection.execute(
            "DELETE FROM settings WHERE key='workspace_conversation_kind_backfilled_v1'")
    store.close()

    reopened = SQLiteUserIOStore(database)
    events = {row["conversation_id"]: row for row in reopened._connection.execute(
        "SELECT conversation_id,eligible FROM workspace_events")}
    assert events[direct_id]["eligible"] == 1
    assert events[group_id]["eligible"] == 0
    assert events[unknown_id]["eligible"] == 0


def test_http_policy_gate_checks_ingest_eligibility_account_and_peer(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    store.register_account(
        account_id="telegram:11", provider="telegram", display_name="Policy test",
        can_read=True, can_reply=False, credential_ref="telegram-qr:account-11",
    )
    store.register_account(
        account_id="telegram:22", provider="telegram", display_name="Other account",
        can_read=True, can_reply=False, credential_ref="telegram-qr:account-22",
    )
    conversation_id, _ = service.receive(
        InboxMessage("telegram", "-1007:1", "Group", "old", 1.0,
                     conversation_kind="group", peer_id="-1007"),
        route_id="telegram", account_ref="telegram:11")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="owner-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def evaluate(message_id: str, *, account_id: str = "telegram:11", peer_id: str = "-1007") -> tuple[int, dict]:
        body = {"source": "telegram", "conversation_id": conversation_id,
                "message_id": message_id, "account_id": account_id, "peer_id": peer_id}
        request = Request(base + "/v1/workspace/policy/evaluate", data=json.dumps(body).encode(),
                          method="POST", headers={"Authorization": "Bearer owner-token",
                                                   "Content-Type": "application/json"})
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    try:
        assert evaluate("telegram:11|-1007:1")[1]["allowed"] is False
        store.set_workspace_chat_rule(conversation_id=conversation_id, action="allow")
        # The pre-allow event never becomes eligible retroactively.
        assert evaluate("telegram:11|-1007:1")[1]["allowed"] is False
        service.receive(InboxMessage("telegram", "-1007:2", "Group", "new", 2.0,
                                     conversation_kind="group", peer_id="-1007"),
                        route_id="telegram", account_ref="telegram:11")
        assert evaluate("telegram:11|-1007:2")[1]["allowed"] is True
        assert evaluate("telegram:11|-1007:2", account_id="telegram:22")[0] == 403
        assert evaluate("telegram:11|-1007:2", peer_id="-1008")[0] == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


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
