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


def _server(tmp_path, monkeypatch) -> str:
    monkeypatch.setattr(agent_channel, "COMMANDS_FILE", tmp_path / "agent-commands.jsonl")
    monkeypatch.setattr(agent_channel, "RESULTS_FILE", tmp_path / "agent-results.jsonl")
    monkeypatch.setattr(agent_channel, "_PENDING", {})
    monkeypatch.setattr(agent_channel, "_LAST_POLL", {})
    service = UserIOService(SQLiteUserIOStore(tmp_path / "userio.sqlite3"), Generator(), Outbox())
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
