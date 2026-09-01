from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from universal_userio import collect
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
    monkeypatch.setattr(collect, "TASKS_FILE", tmp_path / "collect-tasks.json")
    monkeypatch.setattr(collect, "RESULTS_FILE", tmp_path / "collect-results.jsonl")
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


def test_collect_tasks_results_roundtrip(tmp_path, monkeypatch) -> None:
    base = _server(tmp_path, monkeypatch)
    (tmp_path / "collect-tasks.json").write_text(json.dumps([
        {"id": "sitecart-orders", "title": "SiteCart orders", "site": "https://shop.example",
         "every_sec": 60, "active": True,
         "recipe": {"kind": "fetch", "url": "https://shop.example/api/orders"}},
        {"id": "off", "active": False, "recipe": {"kind": "fetch", "url": "https://x.example"}},
    ]), encoding="utf-8")

    status, tasks = _get(base, "/v1/collect/tasks")
    assert status == 200
    assert [t["id"] for t in tasks["tasks"]] == ["sitecart-orders"]
    assert tasks["tasks"][0]["type"] == "universal.collect.task.v1"
    assert tasks["tasks"][0]["recipe"]["url"] == "https://shop.example/api/orders"
    assert tasks["tasks"][0]["every_sec"] == 60

    try:
        _get(base, "/v1/collect/tasks", token="wrong")
    except HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError("collect tasks exposed without a valid token")

    _post(base, "/v1/collect/results", {
        "task_id": "sitecart-orders", "status": "ok", "http_status": 200,
        "data": {"orders": 3}, "agent": "test/1",
    })
    _post(base, "/v1/collect/results", {
        "task_id": "sitecart-orders", "status": "error", "http_status": 0,
        "error": "boom", "agent": "test/1",
    })
    status, results = _get(base, "/v1/collect/results?task_id=sitecart-orders")
    assert status == 200
    assert [r["status"] for r in results["results"]] == ["error", "ok"]
    assert results["results"][0]["type"] == "universal.collect.result.v1"
    assert results["results"][0]["user"]
    assert results["results"][0]["task_id"] == "sitecart-orders"

    try:
        _post(base, "/v1/collect/results", {"status": "ok", "data": None})
    except HTTPError as error:
        assert error.code == 400
    else:
        raise AssertionError("result without task_id was accepted")


def test_collect_rejects_oversized_result(tmp_path, monkeypatch) -> None:
    base = _server(tmp_path, monkeypatch)
    monkeypatch.setattr(collect, "MAX_RESULT_BYTES", 128)
    try:
        _post(base, "/v1/collect/results", {
            "task_id": "t", "status": "ok", "http_status": 200, "data": "x" * 500,
        })
    except HTTPError as error:
        assert error.code == 400
    else:
        raise AssertionError("oversized result was accepted")


def test_collect_broken_tasks_file_returns_server_error(tmp_path, monkeypatch) -> None:
    base = _server(tmp_path, monkeypatch)
    (tmp_path / "collect-tasks.json").write_text('{"not": "a list"}', encoding="utf-8")
    try:
        _get(base, "/v1/collect/tasks")
    except HTTPError as error:
        assert error.code == 500
    else:
        raise AssertionError("malformed tasks file was not reported")
