from __future__ import annotations

import json
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from universal_userio import agent_channel
from universal_userio.http_api import handler
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def suggest(self, *, conversation_id, latest_message):
        return "draft"


class Outbox:
    def send_reply(self, **kwargs):
        return "event_1"


_HTTP_SERVICES: list = []


def _server(tmp_path, monkeypatch) -> str:
    monkeypatch.setattr(agent_channel, "COMMANDS_FILE", tmp_path / "agent-commands.jsonl")
    monkeypatch.setattr(agent_channel, "RESULTS_FILE", tmp_path / "agent-results.jsonl")
    monkeypatch.setattr(agent_channel, "IDENTITY_DIR", tmp_path / "identity")
    monkeypatch.setattr(agent_channel, "_PENDING", {})
    monkeypatch.setattr(agent_channel, "_LAST_POLL", {})
    monkeypatch.setattr(agent_channel, "_ACTIVE_POLLERS", {})
    service = UserIOService(SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Generator(), Outbox())
    _HTTP_SERVICES.append(service)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="test-token"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}"


def _get(base: str, path: str, *, token: str = "test-token"):
    request = Request(base + path, headers={"Authorization": f"Bearer {token}"})
    with urlopen(request) as response:
        return response.status, json.loads(response.read())


def _post(base: str, path: str, payload: dict, *, token: str = "test-token"):
    request = Request(
        base + path, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urlopen(request) as response:
        return response.status, json.loads(response.read())


def test_command_roundtrip_operator_to_agent(tmp_path, monkeypatch) -> None:
    base = _server(tmp_path, monkeypatch)

    status, ack = _post(base, "/v1/agent/commands", {
        "agent_id": "vk-mac", "action": "ping", "args": {"deep": False},
    })
    assert status == 202
    assert ack["queued"] is True
    command_id = ack["id"]

    status, body = _get(base, "/v1/agent/poll?agent_id=vk-mac&wait=1")
    assert status == 200
    command = body["command"]
    assert command["id"] == command_id
    assert command["action"] == "ping"
    assert command["args"] == {"deep": False}

    status, ack = _post(base, "/v1/agent/results", {
        "id": command_id, "agent_id": "vk-mac", "action": "ping",
        "result": {"ok": True, "vk_tabs": 1},
    })
    assert status == 202

    status, body = _get(base, "/v1/agent/results?agent_id=vk-mac")
    assert status == 200
    assert body["results"][0]["result"] == {"ok": True, "vk_tabs": 1}

    status, body = _get(base, "/v1/agent/status")
    assert status == 200
    entry = next(a for a in body["agents"] if a["agent_id"] == "vk-mac")
    assert entry["pending"] == 0
    assert entry["last_poll_at"] > 0

    # commands file is the audit log
    lines = (tmp_path / "agent-commands.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["action"] == "ping"


def test_long_poll_wakes_up_on_enqueue(tmp_path, monkeypatch) -> None:
    base = _server(tmp_path, monkeypatch)
    outcome: dict = {}

    def poller() -> None:
        started = time.monotonic()
        status, body = _get(base, "/v1/agent/poll?agent_id=vk-mac&wait=10")
        outcome["elapsed"] = time.monotonic() - started
        outcome["command"] = body["command"]

    thread = threading.Thread(target=poller, daemon=True)
    thread.start()
    time.sleep(0.5)
    _post(base, "/v1/agent/commands", {"agent_id": "vk-mac", "action": "navigate", "args": {"url": "https://vk.com/im"}})
    thread.join(timeout=15)

    assert outcome["command"]["action"] == "navigate"
    assert outcome["elapsed"] < 5  # woke up immediately, did not sit out the 10s


def test_poll_timeout_returns_null_command(tmp_path, monkeypatch) -> None:
    base = _server(tmp_path, monkeypatch)
    started = time.monotonic()
    status, body = _get(base, "/v1/agent/poll?agent_id=quiet&wait=1")
    assert status == 200
    assert body["command"] is None
    assert 1.0 <= time.monotonic() - started < 20


def test_agent_channel_requires_auth(tmp_path, monkeypatch) -> None:
    base = _server(tmp_path, monkeypatch)
    request = Request(base + "/v1/agent/poll?agent_id=vk-mac&wait=0")
    try:
        urlopen(request)
    except HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError("unauthenticated poll was accepted")


def test_enqueue_validation(tmp_path, monkeypatch) -> None:
    base = _server(tmp_path, monkeypatch)
    for payload in ({}, {"agent_id": "vk-mac"}, {"action": "ping"}):
        request = Request(
            base + "/v1/agent/commands", data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer test-token"},
        )
        try:
            urlopen(request)
        except HTTPError as error:
            assert error.code == 400
        else:
            raise AssertionError(f"invalid payload accepted: {payload}")


def test_push_result_does_not_retain_unawaited_result(tmp_path, monkeypatch):
    from universal_userio import agent_channel
    monkeypatch.setattr(agent_channel, "RESULTS_FILE", tmp_path / "results.jsonl")
    agent_channel._RESULTS.clear()
    agent_channel._WAITING.clear()
    agent_channel._COMMANDS.clear()
    agent_channel.push_result({"id": "orphan", "result": {"ok": True}}, user="owner")
    assert "orphan" not in agent_channel._RESULTS
    assert (tmp_path / "results.jsonl").exists()


def test_result_receipt_preserves_profile_and_principal_context(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_channel, "COMMANDS_FILE", tmp_path / "commands.jsonl")
    monkeypatch.setattr(agent_channel, "RESULTS_FILE", tmp_path / "results.jsonl")
    monkeypatch.setattr(agent_channel, "_PENDING", {})
    agent_channel._COMMANDS.clear()
    instance = "browser-11111111-1111-4111-8111-111111111111"
    queued = agent_channel.enqueue({
        "agent_id": "browser-agent", "agent_instance_id": instance,
        "connection_id": "work", "action": "ping",
    }, user="owner", principal_id="principal-work")
    agent_channel.push_result({
        "id": queued["id"], "agent_id": "browser-agent", "agent_instance_id": instance,
        "connection_id": "work", "principal_id": "principal-work", "action": "ping", "result": {"ok": True},
    }, user="owner", principal_id="principal-work")
    record = json.loads((tmp_path / "results.jsonl").read_text().splitlines()[-1])
    assert record["connection_id"] == "work"
    assert record["principal_id"] == "principal-work"
    assert record["agent_instance_id"] == instance


def test_agent_command_ids_do_not_repeat_after_server_restart(tmp_path, monkeypatch):
    """Persisted receipts must not be mistaken for a post-restart command."""
    import itertools
    monkeypatch.setattr(agent_channel, 'COMMANDS_FILE', tmp_path / 'commands.jsonl')
    monkeypatch.setattr(agent_channel, '_PENDING', {})
    monkeypatch.setattr(agent_channel, '_SEQ', itertools.count(1), raising=False)
    first = agent_channel.enqueue({'agent_id': 'same-browser', 'action': 'ping'}, user='owner')
    monkeypatch.setattr(agent_channel, '_SEQ', itertools.count(1), raising=False)
    second = agent_channel.enqueue({'agent_id': 'same-browser', 'action': 'ping'}, user='owner')
    assert first['id'] != second['id']


def test_conflicting_profile_instances_cannot_consume_the_same_route(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_channel, 'COMMANDS_FILE', tmp_path / 'commands.jsonl')
    monkeypatch.setattr(agent_channel, 'IDENTITY_DIR', tmp_path / 'identity')
    monkeypatch.setattr(agent_channel, '_PENDING', {})
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    one = 'browser-11111111-1111-4111-8111-111111111111'
    two = 'browser-22222222-2222-4222-8222-222222222222'
    agent_channel.poll('legacy', 0, user='owner', instance_id=one)
    conflict = agent_channel.poll('legacy', 0, user='owner', instance_id=two)
    assert conflict['identity_conflict'] is True
    agent_channel.enqueue({'agent_id':'legacy','action':'ping'},user='owner')
    assert agent_channel.poll('legacy',0,user='owner',instance_id=one)['command'] is None
    assert agent_channel.poll('legacy',0,user='owner',instance_id=two)['command'] is None
    assert agent_channel.status(user='owner')['agents'][0]['identity_conflict'] is True
    agent_channel.enqueue({'agent_id':'legacy','action':'ping'},user='other')
    assert agent_channel.poll('legacy',0,user='other')['command']['action']=='ping'


def test_profile_conflict_is_exposed_over_http_and_survives_ttl_until_migration(tmp_path, monkeypatch):
    base = _server(tmp_path, monkeypatch)
    now = [100.0]
    monkeypatch.setattr(agent_channel, '_now', lambda: now[0])
    one = ONE
    two = TWO
    assert _get(base, f'/v1/agent/poll?agent_id=legacy&wait=0&instance_id={one}')[1]['command'] is None
    assert _get(base, f'/v1/agent/poll?agent_id=legacy&wait=0&instance_id={two}')[1]['identity_conflict'] is True
    _post(base, '/v1/agent/commands', {'agent_id':'legacy','action':'ping'})
    assert _get(base, f'/v1/agent/poll?agent_id=legacy&wait=0&instance_id={one}')[1]['command'] is None
    now[0] += agent_channel.ACTIVE_POLLER_TTL + 1
    # the detected ambiguity is durable: the queue is not silently delivered
    # to a random survivor after the other profile's poll expires
    held = _get(base, f'/v1/agent/poll?agent_id=legacy&wait=0&instance_id={one}')[1]
    assert held['command'] is None
    assert held['identity_conflict'] is True
    # a correct migration ends the contest and the pinned owner gets the queue
    agent_channel.resolve_owner('owner', 'legacy', one)
    delivered = _get(base, f'/v1/agent/poll?agent_id=legacy&wait=0&instance_id={one}')[1]
    assert delivered['command']['action'] == 'ping'
    assert delivered['identity_conflict'] is False


ONE = 'browser-11111111-1111-4111-8111-111111111111'
TWO = 'browser-22222222-2222-4222-8222-222222222222'


def test_first_modern_profile_cannot_take_unverified_legacy_route(tmp_path, monkeypatch):
    """A second profile may arrive later, including after a server restart."""
    monkeypatch.setattr(agent_channel, 'COMMANDS_FILE', tmp_path / 'commands.jsonl')
    monkeypatch.setattr(agent_channel, 'IDENTITY_DIR', tmp_path / 'identity')
    monkeypatch.setattr(agent_channel, '_PENDING', {})
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    monkeypatch.setattr(agent_channel, '_LAST_POLL', {})
    agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    agent_channel.enqueue({'agent_id': 'legacy', 'action': 'send'}, user='owner')
    held = agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    assert held['command'] is None
    assert held['identity_conflict'] is True
    assert agent_channel.status(user='owner')['agents'][0]['identity_conflict'] is True
    agent_channel.enqueue({'agent_id': ONE, 'action': 'probe'}, user='owner')
    addressed = agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    assert addressed['command']['action'] == 'probe'
    assert addressed['identity_conflict'] is False


def test_new_profile_with_own_uuid_keeps_direct_route(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_channel, 'COMMANDS_FILE', tmp_path / 'commands.jsonl')
    monkeypatch.setattr(agent_channel, 'IDENTITY_DIR', tmp_path / 'identity')
    monkeypatch.setattr(agent_channel, '_PENDING', {})
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    monkeypatch.setattr(agent_channel, '_LAST_POLL', {})
    agent_channel.poll(ONE, 0, user='owner', instance_id=ONE)
    agent_channel.enqueue({'agent_id': ONE, 'action': 'ping'}, user='owner')
    body = agent_channel.poll(ONE, 0, user='owner', instance_id=ONE)
    assert body['command']['action'] == 'ping'
    assert body['identity_conflict'] is False
    assert agent_channel.status(user='owner')['agents'][0]['identity_conflict'] is False


def test_legacy_route_holds_the_queue_while_two_instances_are_contested(tmp_path, monkeypatch):
    """A contested legacy route must not hand its queue to an arbitrary profile."""
    monkeypatch.setattr(agent_channel, 'COMMANDS_FILE', tmp_path / 'commands.jsonl')
    monkeypatch.setattr(agent_channel, 'IDENTITY_DIR', tmp_path / 'identity')
    monkeypatch.setattr(agent_channel, '_PENDING', {})
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    assert agent_channel.poll('legacy', 0, user='owner', instance_id=TWO)['command'] is None
    # both profiles are now known: the route is contested before the command
    # arrives, so the shared queue must wait for a correct migration
    agent_channel.enqueue({'agent_id': 'legacy', 'action': 'ping'}, user='owner')
    assert agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)['command'] is None
    assert agent_channel.poll('legacy', 0, user='owner', instance_id=TWO)['command'] is None
    # the legacy command is still pending for nobody, and was not consumed
    assert agent_channel.status(user='owner')['agents'][0]['pending'] == 1


def test_each_instance_gets_the_commands_addressed_to_its_own_browser_uuid(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_channel, 'COMMANDS_FILE', tmp_path / 'commands.jsonl')
    monkeypatch.setattr(agent_channel, 'IDENTITY_DIR', tmp_path / 'identity')
    monkeypatch.setattr(agent_channel, '_PENDING', {})
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    agent_channel.enqueue({'agent_id': ONE, 'action': 'navigate'}, user='owner')
    agent_channel.enqueue({'agent_id': TWO, 'action': 'eval'}, user='owner')
    first = agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    second = agent_channel.poll('legacy', 0, user='owner', instance_id=TWO)
    assert first['command']['action'] == 'navigate'
    assert first['command']['agent_id'] == ONE
    assert first['address'] == ONE
    assert second['command']['action'] == 'eval'
    assert second['command']['agent_id'] == TWO
    assert second['address'] == TWO


def test_status_lists_active_instances_and_flags_unaddressable_legacy_poll(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_channel, 'COMMANDS_FILE', tmp_path / 'commands.jsonl')
    monkeypatch.setattr(agent_channel, 'IDENTITY_DIR', tmp_path / 'identity')
    monkeypatch.setattr(agent_channel, '_PENDING', {})
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    agent_channel.poll('legacy', 0, user='owner', instance_id=TWO)
    entry = agent_channel.status(user='owner')['agents'][0]
    assert entry['identity_conflict'] is True
    assert entry['instances'] == [ONE, TWO]
    assert entry['legacy_unaddressable'] is False
    # an old client polling without an instance makes the legacy queue ambiguous
    agent_channel.poll('legacy', 0, user='owner')
    entry = agent_channel.status(user='owner')['agents'][0]
    assert entry['legacy_unaddressable'] is True
    assert entry['identity_conflict'] is True


def test_detected_ambiguity_survives_restart_and_ttl_expiry(tmp_path, monkeypatch):
    """Once two instances were seen for one legacy route, the fact is durable."""
    monkeypatch.setattr(agent_channel, 'COMMANDS_FILE', tmp_path / 'commands.jsonl')
    monkeypatch.setattr(agent_channel, 'IDENTITY_DIR', tmp_path / 'identity')
    monkeypatch.setattr(agent_channel, '_PENDING', {})
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    now = [1000.0]
    monkeypatch.setattr(agent_channel, '_now', lambda: now[0])
    agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    agent_channel.poll('legacy', 0, user='owner', instance_id=TWO)
    agent_channel.enqueue({'agent_id': 'legacy', 'action': 'ping'}, user='owner')

    # server restart: in-memory state is gone, the durable fact is not
    monkeypatch.setattr(agent_channel, '_PENDING', {})
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    monkeypatch.setattr(agent_channel, '_LAST_POLL', {}, raising=False)
    now[0] += agent_channel.ACTIVE_POLLER_TTL + 1
    assert agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)['command'] is None
    entry = agent_channel.status(user='owner')['agents'][0]
    assert entry['identity_conflict'] is True
    assert entry['known_instances'] == [ONE, TWO]


def test_durable_ambiguity_is_isolated_per_user(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_channel, 'COMMANDS_FILE', tmp_path / 'commands.jsonl')
    monkeypatch.setattr(agent_channel, 'IDENTITY_DIR', tmp_path / 'identity')
    monkeypatch.setattr(agent_channel, '_PENDING', {})
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    agent_channel.poll('legacy', 0, user='owner', instance_id=TWO)
    agent_channel.enqueue({'agent_id': 'legacy', 'action': 'ping'}, user='other')
    body = agent_channel.poll('legacy', 0, user='other')
    assert body['command']['action'] == 'ping'
    assert body['identity_conflict'] is False


def test_single_profile_legacy_client_keeps_working(tmp_path, monkeypatch):
    """Old client, one profile: no conflict, queue delivered, poll flagged."""
    monkeypatch.setattr(agent_channel, 'COMMANDS_FILE', tmp_path / 'commands.jsonl')
    monkeypatch.setattr(agent_channel, 'IDENTITY_DIR', tmp_path / 'identity')
    monkeypatch.setattr(agent_channel, '_PENDING', {})
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    agent_channel.enqueue({'agent_id': 'legacy', 'action': 'ping'}, user='owner')
    body = agent_channel.poll('legacy', 0, user='owner')
    assert body['command']['action'] == 'ping'
    assert body['identity_conflict'] is False
    entry = agent_channel.status(user='owner')['agents'][0]
    assert entry['identity_conflict'] is False
    assert entry['legacy_unaddressable'] is True


def test_identity_state_file_is_private_and_user_scoped(tmp_path, monkeypatch):
    identity_dir = tmp_path / 'identity'
    monkeypatch.setattr(agent_channel, 'COMMANDS_FILE', tmp_path / 'commands.jsonl')
    monkeypatch.setattr(agent_channel, 'IDENTITY_DIR', identity_dir)
    monkeypatch.setattr(agent_channel, '_PENDING', {})
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    files = list(identity_dir.glob('*.json'))
    assert len(files) == 1
    assert oct(files[0].stat().st_mode & 0o777) == '0o600'
    assert oct(identity_dir.stat().st_mode & 0o777) == '0o700'
    assert json.loads(files[0].read_text())['user'] == 'owner'


def test_http_status_exposes_conflict_instances_and_user_isolation(tmp_path, monkeypatch):
    base = _server(tmp_path, monkeypatch)
    _get(base, f'/v1/agent/poll?agent_id=legacy&wait=0&instance_id={ONE}')
    _get(base, f'/v1/agent/poll?agent_id=legacy&wait=0&instance_id={TWO}')
    status, body = _get(base, '/v1/agent/status')
    assert status == 200
    entry = next(a for a in body['agents'] if a['agent_id'] == 'legacy')
    assert entry['identity_conflict'] is True
    assert entry['instances'] == [ONE, TWO]
    assert entry['legacy_unaddressable'] is False
    # another user sees none of it
    service = _HTTP_SERVICES[-1]
    other_principal = service._store.register_user('other-owner', 'other-owner-password')
    other_token = service._store.issue_access_token(other_principal.user_id)
    request = Request(base + '/v1/agent/status', headers={'Authorization': f'Bearer {other_token}'})
    with urlopen(request) as response:
        other = json.loads(response.read())
    assert other['agents'] == []


def test_corrupt_ledger_never_silently_resolves_the_contest(tmp_path, monkeypatch):
    """A damaged ledger must keep the route contested, not drop the ambiguity."""
    identity_dir = tmp_path / 'identity'
    monkeypatch.setattr(agent_channel, 'COMMANDS_FILE', tmp_path / 'commands.jsonl')
    monkeypatch.setattr(agent_channel, 'IDENTITY_DIR', identity_dir)
    monkeypatch.setattr(agent_channel, '_PENDING', {})
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    agent_channel.poll('legacy', 0, user='owner', instance_id=TWO)
    ledger = next(identity_dir.glob('*.json'))
    ledger.write_text('{ not json')
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    body = agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    assert body['command'] is None
    assert body['identity_conflict'] is True
    assert body['identity_error'] == 'corrupt'
    entry = agent_channel.status(user='owner')['agents'][0]
    assert entry['identity_conflict'] is True
    assert entry['identity_error'] == 'corrupt'


def test_unreadable_ledger_is_reported_and_keeps_the_route_contested(tmp_path, monkeypatch):
    identity_dir = tmp_path / 'identity'
    monkeypatch.setattr(agent_channel, 'COMMANDS_FILE', tmp_path / 'commands.jsonl')
    monkeypatch.setattr(agent_channel, 'IDENTITY_DIR', identity_dir)
    monkeypatch.setattr(agent_channel, '_PENDING', {})
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    ledger = next(identity_dir.glob('*.json'))
    ledger.chmod(0o000)
    try:
        body = agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    finally:
        ledger.chmod(0o600)
    assert body['identity_conflict'] is True
    assert body['identity_error'] in {'unreadable', 'corrupt'}


def test_prior_legacy_peer_history_survives_ttl_and_restart(tmp_path, monkeypatch):
    """An old client that polled before the upgrade stays known."""
    monkeypatch.setattr(agent_channel, 'COMMANDS_FILE', tmp_path / 'commands.jsonl')
    monkeypatch.setattr(agent_channel, 'IDENTITY_DIR', tmp_path / 'identity')
    monkeypatch.setattr(agent_channel, '_PENDING', {})
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    now = [500.0]
    monkeypatch.setattr(agent_channel, '_now', lambda: now[0])
    agent_channel.poll('legacy', 0, user='owner')          # old client, no instance
    agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    entry = agent_channel.status(user='owner')['agents'][0]
    assert entry['legacy_unaddressable'] is True
    assert entry['legacy_seen'] is True
    assert entry['identity_conflict'] is True
    # the legacy poll expires, the fact does not
    now[0] += agent_channel.ACTIVE_POLLER_TTL + 1
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    entry = agent_channel.status(user='owner')['agents'][0]
    assert entry['legacy_unaddressable'] is False
    assert entry['legacy_seen'] is True
    assert entry['identity_conflict'] is True


def test_ledger_is_written_only_when_new_information_arrives(tmp_path, monkeypatch):
    identity_dir = tmp_path / 'identity'
    monkeypatch.setattr(agent_channel, 'COMMANDS_FILE', tmp_path / 'commands.jsonl')
    monkeypatch.setattr(agent_channel, 'IDENTITY_DIR', identity_dir)
    monkeypatch.setattr(agent_channel, '_PENDING', {})
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    ledger = next(identity_dir.glob('*.json'))
    first = ledger.stat().st_mtime_ns
    for _ in range(3):
        agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    assert ledger.stat().st_mtime_ns == first
    agent_channel.poll('legacy', 0, user='owner', instance_id=TWO)
    assert ledger.stat().st_mtime_ns != first
    assert len(list(identity_dir.glob('*.json'))) == 1


def test_resolve_owner_ends_the_contest_and_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_channel, 'COMMANDS_FILE', tmp_path / 'commands.jsonl')
    monkeypatch.setattr(agent_channel, 'IDENTITY_DIR', tmp_path / 'identity')
    monkeypatch.setattr(agent_channel, '_PENDING', {})
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    agent_channel.poll('legacy', 0, user='owner', instance_id=TWO)
    agent_channel.enqueue({'agent_id': 'legacy', 'action': 'ping'}, user='owner')
    agent_channel.resolve_owner('owner', 'legacy', ONE)
    body = agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    assert body['command']['action'] == 'ping'
    assert body['identity_conflict'] is False
    # the other profile does not steal the migrated queue
    agent_channel.enqueue({'agent_id': 'legacy', 'action': 'ping2'}, user='owner')
    other = agent_channel.poll('legacy', 0, user='owner', instance_id=TWO)
    assert other['command'] is None
    assert other['identity_conflict'] is True


def test_resolve_owner_rejects_invalid_instance_and_corrupt_ledger(tmp_path, monkeypatch):
    identity_dir = tmp_path / 'identity'
    monkeypatch.setattr(agent_channel, 'COMMANDS_FILE', tmp_path / 'commands.jsonl')
    monkeypatch.setattr(agent_channel, 'IDENTITY_DIR', identity_dir)
    monkeypatch.setattr(agent_channel, '_PENDING', {})
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    try:
        agent_channel.resolve_owner('owner', 'legacy', 'not-a-uuid')
    except ValueError:
        pass
    else:
        raise AssertionError('invalid instance accepted')
    ledger = next(identity_dir.glob('*.json'))
    ledger.write_text('broken')
    try:
        agent_channel.resolve_owner('owner', 'legacy', ONE)
    except ValueError as error:
        assert 'corrupt' in str(error)
    else:
        raise AssertionError('corrupt ledger accepted')


def test_old_client_first_poll_pending_and_restart_keep_serving(tmp_path, monkeypatch):
    """An empty instance_id must keep serving a single legacy route even
    though the very first poll creates the identity ledger."""
    monkeypatch.setattr(agent_channel, 'COMMANDS_FILE', tmp_path / 'commands.jsonl')
    monkeypatch.setattr(agent_channel, 'IDENTITY_DIR', tmp_path / 'identity')
    monkeypatch.setattr(agent_channel, '_PENDING', {})
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    agent_channel.enqueue({'agent_id': 'legacy', 'action': 'ping'}, user='owner')
    body = agent_channel.poll('legacy', 0, user='owner')
    assert body['command']['action'] == 'ping'
    assert body['identity_conflict'] is False
    entry = agent_channel.status(user='owner')['agents'][0]
    assert entry['identity_conflict'] is False
    assert entry['legacy_seen'] is True
    assert list((tmp_path / 'identity').glob('*.json'))  # ledger exists now
    # restart: in-memory state is gone, the ledger persists, delivery works
    monkeypatch.setattr(agent_channel, '_PENDING', {})
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    agent_channel.enqueue({'agent_id': 'legacy', 'action': 'second'}, user='owner')
    body = agent_channel.poll('legacy', 0, user='owner')
    assert body['command']['action'] == 'second'
    assert body['identity_conflict'] is False


def test_ledger_with_invalid_instance_entries_is_corrupt(tmp_path, monkeypatch):
    """Invalid or duplicate instances must not be silently dropped into a
    clean-looking ledger: the route stays contested with an explicit error."""
    identity_dir = tmp_path / 'identity'
    monkeypatch.setattr(agent_channel, 'COMMANDS_FILE', tmp_path / 'commands.jsonl')
    monkeypatch.setattr(agent_channel, 'IDENTITY_DIR', identity_dir)
    monkeypatch.setattr(agent_channel, '_PENDING', {})
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    agent_channel.poll('legacy', 0, user='owner', instance_id=TWO)
    ledger = next(identity_dir.glob('*.json'))
    raw = json.loads(ledger.read_text())
    raw['instances'] = ['not-a-uuid', ONE, ONE]
    ledger.write_text(json.dumps(raw))
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    agent_channel.enqueue({'agent_id': 'legacy', 'action': 'ping'}, user='owner')
    body = agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    assert body['command'] is None
    assert body['identity_conflict'] is True
    assert body['identity_error'] == 'corrupt'
    entry = agent_channel.status(user='owner')['agents'][0]
    assert entry['identity_conflict'] is True
    assert entry['identity_error'] == 'corrupt'
    try:
        agent_channel.resolve_owner('owner', 'legacy', ONE)
    except ValueError as error:
        assert 'corrupt' in str(error)
    else:
        raise AssertionError('tampered ledger accepted for migration')


def test_ledger_missing_required_fields_is_corrupt(tmp_path, monkeypatch):
    identity_dir = tmp_path / 'identity'
    monkeypatch.setattr(agent_channel, 'COMMANDS_FILE', tmp_path / 'commands.jsonl')
    monkeypatch.setattr(agent_channel, 'IDENTITY_DIR', identity_dir)
    monkeypatch.setattr(agent_channel, '_PENDING', {})
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    identity_dir.mkdir()
    path = identity_dir / agent_channel._identity_path('owner', 'legacy').name
    path.write_text(json.dumps({
        'schema': agent_channel.IDENTITY_SCHEMA,
        'user': 'owner', 'agent_id': 'legacy', 'instances': [ONE],
    }))
    body = agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    assert body['identity_conflict'] is True
    assert body['identity_error'] == 'corrupt'


def test_addressed_commands_reach_each_instance_during_contest(tmp_path, monkeypatch):
    """While a route is contested, commands addressed to a browser UUID must
    still be delivered to exactly that instance, without identity_conflict
    (the extension drops conflicting responses) and without cross-delivery."""
    monkeypatch.setattr(agent_channel, 'COMMANDS_FILE', tmp_path / 'commands.jsonl')
    monkeypatch.setattr(agent_channel, 'IDENTITY_DIR', tmp_path / 'identity')
    monkeypatch.setattr(agent_channel, '_PENDING', {})
    monkeypatch.setattr(agent_channel, '_ACTIVE_POLLERS', {}, raising=False)
    three = 'browser-33333333-3333-4333-8333-333333333333'
    agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    agent_channel.poll('legacy', 0, user='owner', instance_id=TWO)
    agent_channel.enqueue({'agent_id': ONE, 'action': 'navigate'}, user='owner')
    agent_channel.enqueue({'agent_id': TWO, 'action': 'eval'}, user='owner')
    agent_channel.enqueue({'agent_id': three, 'action': 'nobody'}, user='owner')
    first = agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    assert first['command']['action'] == 'navigate'
    assert first['command']['agent_id'] == ONE
    assert first['identity_conflict'] is False
    assert first['route_contested'] is True
    second = agent_channel.poll('legacy', 0, user='owner', instance_id=TWO)
    assert second['command']['action'] == 'eval'
    assert second['command']['agent_id'] == TWO
    assert second['identity_conflict'] is False
    # nothing is re-delivered, duplicated or stolen on later polls
    again = agent_channel.poll('legacy', 0, user='owner', instance_id=ONE)
    assert again['command'] is None
    assert again['identity_conflict'] is True
    assert 'route_contested' not in again
    pending = {a['agent_id']: a['pending'] for a in agent_channel.status(user='owner')['agents']}
    assert pending[three] == 1
    assert pending.get(ONE, 0) == 0


def test_scoped_command_is_never_delivered_to_wrong_browser(tmp_path, monkeypatch) -> None:
    base = _server(tmp_path, monkeypatch)
    a = "browser-11111111-1111-4111-8111-111111111111"
    b = "browser-22222222-2222-4222-8222-222222222222"
    status, ack = _post(base, "/v1/agent/commands", {
        "agent_id": "shared-browser", "agent_instance_id": a,
        "action": "gpt_read", "args": {"chat_ref": "11111111-1111-4111-8111-111111111111"},
    })
    assert status == 202 and ack["queued"]
    _, other = _get(base, "/v1/agent/poll?agent_id=shared-browser&instance_id=" + b + "&wait=0")
    assert other["command"] is None
    _, exact = _get(base, "/v1/agent/poll?agent_id=shared-browser&instance_id=" + a + "&wait=0")
    assert exact["command"]["id"] == ack["id"]
    assert exact["command"]["agent_instance_id"] == a
    _, empty = _get(base, "/v1/agent/poll?agent_id=shared-browser&instance_id=" + a + "&wait=0")
    assert empty["command"] is None


def test_scoped_command_rejects_invalid_uuid(tmp_path, monkeypatch) -> None:
    base = _server(tmp_path, monkeypatch)
    for instance in ("nope", "browser-../../../etc/passwd"):
        request = Request(base + "/v1/agent/commands", method="POST",
            data=json.dumps({"agent_id":"shared","agent_instance_id":instance,
                             "action":"gpt_read"}).encode(),
            headers={"Content-Type":"application/json","Authorization":"Bearer test-token"})
        try:
            urlopen(request)
        except HTTPError as e:
            assert e.code == 400
        else:
            raise AssertionError("malformed instance ID was accepted")
