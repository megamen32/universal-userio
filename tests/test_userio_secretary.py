from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from universal_userio.contracts import InboxMessage
from universal_userio.http_api import handler
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class _Generator:
    def suggest(self, **_kwargs: object) -> str:
        return "draft"

    def triage_with_context(self, **_kwargs: object) -> dict[str, object]:
        return {
            "decision": "notify",
            "importance": 0.84,
            "urgency": "high",
            "confidence": 0.91,
            "reason_codes": ["owner_action"],
            "reason_ru": "Нужен ответ владельца.",
            "action_required": True,
            "action_summary": "Ответить сегодня.",
            "deadline_at": None,
            "suggested_replies": [{"body": "Принял, отвечу сегодня."}],
            "safety_override": False,
            "policy_version": "userio-test-v1",
        }


class _Outbox:
    def send_reply(self, **_kwargs: object) -> str:
        return "receipt"


def _service(tmp_path, *, body="Нужно подписать сегодня") -> tuple[UserIOService, str]:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, _Generator(), _Outbox())
    conversation_id, accepted = service.receive(
        InboxMessage(
            "telegram", "540308572:44", "Катя", body, 44.0,
            conversation_kind="direct", peer_id="540308572",
        ),
        route_id="telegram-owner",
    )
    assert accepted is True
    assert store.set_conversation_account(conversation_id, "telegram:owner") is True
    return service, conversation_id


def test_userio_secretary_persists_exact_identity_and_deep_session(tmp_path) -> None:
    service, conversation_id = _service(tmp_path)
    store = service._store

    overview = service.triage_conversation_secretary(
        conversation_id=conversation_id, actor="userio-web:owner",
    )
    assert overview["conversation"] == {
        "id": conversation_id,
        "source": "telegram",
        "account_ref": "telegram:owner",
        "peer_id": "540308572",
        "conversation_kind": "direct",
        "event_seq": 1,
        "message_id": "540308572:44",
        "message_source": "telegram",
        "received_at": 44.0,
    }
    assert overview["triage"]["triage"]["importance"] == 0.84

    first = service.request_conversation_secretary_deep(
        conversation_id=conversation_id, actor="userio-web:owner",
    )
    repeated = service.request_conversation_secretary_deep(
        conversation_id=conversation_id, actor="userio-web:owner",
    )
    job = first["deep"]
    assert job["status"] == "accepted"
    assert repeated["deep"]["job_id"] == job["job_id"]

    with pytest.raises(ValueError, match="worker_id"):
        store.update_conversation_secretary_job(
            job_id=job["job_id"], phase="accepted",
        )
    with pytest.raises(ValueError, match="claimed"):
        store.update_conversation_secretary_job(
            job_id=job["job_id"], phase="running", worker_id="dispatcher-1",
            session_id="fast-agent:analysis-44",
            session_url="https://agent.bezrabotnyi.com/#/session/fast-agent%3Aanalysis-44",
        )
    claimed = store.update_conversation_secretary_job(
        job_id=job["job_id"], phase="accepted", worker_id="dispatcher-1",
    )
    assert claimed["worker_id"] == "dispatcher-1"
    assert store.update_conversation_secretary_job(
        job_id=job["job_id"], phase="accepted", worker_id="dispatcher-1",
    )["status"] == "accepted"
    with pytest.raises(ValueError, match="worker binding"):
        store.update_conversation_secretary_job(
            job_id=job["job_id"], phase="accepted", worker_id="dispatcher-2",
        )

    for invalid_url in (
        "https://evil.test/#/session/x",
        "https://agent.bezrabotnyi.com/#/session/fast-agent/x",
        "https://agent.bezrabotnyi.com/#/session/fast-agent%ZZx",
        "https://agent.bezrabotnyi.com/#/session/fast-agent:raw-colon",
    ):
        with pytest.raises(ValueError, match="Agent Herder session URL"):
            store.update_conversation_secretary_job(
                job_id=job["job_id"], phase="running", worker_id="dispatcher-1",
                session_id="fast-agent:analysis-44", session_url=invalid_url,
            )
    running = store.update_conversation_secretary_job(
        job_id=job["job_id"], phase="running", worker_id="dispatcher-1",
        session_id="fast-agent:analysis-44",
        session_url="https://agent.bezrabotnyi.com/#/session/fast-agent%3Aanalysis-44",
    )
    assert running["session_id"] == "fast-agent:analysis-44"
    completed = store.update_conversation_secretary_job(
        job_id=job["job_id"], phase="completed", worker_id="dispatcher-1",
        result="Документы настоящие; ответить Кате.",
    )
    assert completed["result"] == "Документы настоящие; ответить Кате."
    assert store.conversation_secretary(conversation_id)["deep"] == completed
    with pytest.raises(ValueError, match="immutable"):
        store.update_conversation_secretary_job(
            job_id=job["job_id"], phase="completed", result="ignored",
            worker_id="dispatcher-1", session_id="fast-agent:other",
        )
    same_conversation, _accepted = service.receive(
        InboxMessage(
            "telegram", "540308572:45", "Катя", "И ещё один вопрос", 45.0,
            conversation_kind="direct", peer_id="540308572",
        ),
        route_id="telegram-owner",
    )
    assert same_conversation == conversation_id
    refreshed = store.conversation_secretary(conversation_id)
    assert refreshed["conversation"]["event_seq"] == 2
    assert refreshed["deep"]["event_seq"] == 1
    assert refreshed["deep"]["result"] == "Документы настоящие; ответить Кате."
    second_job = service.request_conversation_secretary_deep(
        conversation_id=conversation_id, actor="userio-web:owner",
    )["deep"]
    store.update_conversation_secretary_job(
        job_id=second_job["job_id"], phase="accepted", worker_id="dispatcher-1",
    )
    with pytest.raises(ValueError, match="earlier running session"):
        store.update_conversation_secretary_job(
            job_id=second_job["job_id"], phase="completed", worker_id="dispatcher-1",
            result="too early",
        )
    failed = store.update_conversation_secretary_job(
        job_id=second_job["job_id"], phase="failed", worker_id="dispatcher-1",
        error="Fast Agent did not accept the prompt",
    )
    assert failed["status"] == "failed"
    assert "session_id" not in failed
    assert store.delete_conversation(conversation_id) is True
    assert store.conversation_secretary_jobs(status="completed") == []
    assert store.conversation_secretary_jobs(status="failed") == []


def test_userio_secretary_http_is_owner_scoped_and_worker_token_stays_server_side(tmp_path) -> None:
    service, conversation_id = _service(tmp_path)
    _user, user_token = service._store.create_user(
        "userio-secretary-reader", "correct horse battery staple",
    )
    owner_session = service._store.create_oauth_session(service._store.owner().user_id)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), handler(service, token="workspace-service-token")
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def call(
        method: str, path: str, bearer: str = "", payload: dict | None = None,
        *, cookie: str = "",
    ) -> tuple[int, dict]:
        data = None if payload is None else json.dumps(payload).encode()
        request = Request(
            base + path, data=data, method=method,
            headers={
                **({"Authorization": f"Bearer {bearer}"} if bearer else {}),
                **({"Cookie": f"userio_web_session={cookie}"} if cookie else {}),
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
        )
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    try:
        path = f"/v1/conversations/{conversation_id}/secretary"
        assert call("GET", path, "")[0] == 401
        assert call("GET", path, user_token)[0] == 403
        status, initial = call("GET", path, cookie=owner_session)
        assert status == 200
        assert initial["conversation"]["id"] == conversation_id
        assert "token" not in json.dumps(initial).lower()

        status, triage = call(
            "POST", path + "/triage", payload={}, cookie=owner_session,
        )
        assert status == 200
        assert triage["triage"]["triage"]["reason_ru"] == "Нужен ответ владельца."
        status, launched = call(
            "POST", path + "/deep", payload={}, cookie=owner_session,
        )
        assert status == 202
        job_id = launched["deep"]["job_id"]

        jobs_path = "/v1/workspace/secretary/deep-jobs?status=accepted&limit=10"
        assert call("GET", jobs_path, user_token)[0] == 403
        status, jobs = call("GET", jobs_path, "workspace-service-token")
        assert status == 200
        assert [job["job_id"] for job in jobs["jobs"]] == [job_id]

        progress_path = f"/v1/workspace/secretary/deep-jobs/{job_id}/progress"
        assert call("POST", progress_path, user_token, {"phase": "running"})[0] == 403
        assert call("POST", progress_path, "workspace-service-token", {
            "phase": "accepted",
        })[0] == 400
        status, claim = call("POST", progress_path, "workspace-service-token", {
            "phase": "accepted", "worker_id": "dispatcher-1",
        })
        assert status == 200
        assert claim["job"]["worker_id"] == "dispatcher-1"
        assert call("POST", progress_path, "workspace-service-token", {
            "phase": "running", "worker_id": "dispatcher-1",
        })[0] == 400
        status, progress = call("POST", progress_path, "workspace-service-token", {
            "phase": "running",
            "worker_id": "dispatcher-1",
            "session_id": "fast-agent:http-44",
            "session_url": "https://agent.bezrabotnyi.com/#/session/fast-agent%3Ahttp-44",
        })
        assert status == 200
        assert progress["job"]["session_url"].startswith("https://agent.bezrabotnyi.com/")
        assert "workspace-service-token" not in json.dumps(progress)
        assert call("GET", path, cookie=owner_session)[1]["deep"]["status"] == "running"
        service._store.set_user_capability("read", False, user_id=service._store.owner().user_id)
        assert call("GET", jobs_path, "workspace-service-token")[0] == 403
        assert call("GET", path, cookie=owner_session)[0] == 403
        assert call("POST", path + "/triage", payload={}, cookie=owner_session)[0] == 403
        assert call("POST", path + "/deep", payload={}, cookie=owner_session)[0] == 403
    finally:
        server.shutdown()
        server.server_close()


def test_secretary_new_message_during_triage_keeps_the_selected_event(tmp_path) -> None:
    service, conversation_id = _service(tmp_path)
    original = service._generator.triage_with_context

    def arrival(**kwargs):
        service.receive(InboxMessage(
            "telegram", "540308572:45", "Катя", "Новый вопрос", 45.0,
            conversation_kind="direct", peer_id="540308572",
        ), route_id="telegram-owner")
        return original(**kwargs)

    service._generator.triage_with_context = arrival
    overview = service.request_conversation_secretary_deep(
        conversation_id=conversation_id, actor="userio-web:owner",
    )
    assert overview["conversation"]["event_seq"] == overview["triage"]["event_seq"] == 1
    assert overview["deep"]["event_seq"] == 1
    assert service._store.conversation_secretary(conversation_id)["conversation"]["event_seq"] == 2


def test_secretary_failed_retry_gets_new_job_and_preserves_old_result(tmp_path) -> None:
    # A failed ordinary triage must also be recoverable by the owner's
    # explicit action, without automatic replays or a different request ID.
    retry_path = tmp_path / "ordinary-retry"
    retry_path.mkdir()
    retry_service, retry_conversation = _service(retry_path, body="Привет, как дела?")
    class FailingGenerator(_Generator):
        def triage_with_context(self, **kwargs):
            raise ValueError("provider structured result failure")
    retry_service._generator = FailingGenerator()
    request_id = "userio-web-triage-1"
    for _ in range(3):
        retry_service.triage_workspace_event(event_seq=1, request_id=request_id, max_drafts=0)
    held = retry_service.triage_workspace_event(event_seq=1, request_id=request_id, max_drafts=0)
    assert held["status"] == "review"
    assert retry_service._store._connection.execute(
        "SELECT attempts FROM workspace_triage WHERE event_seq=1"
    ).fetchone()[0] == 3
    retry_service._generator = _Generator()
    recovered = retry_service.triage_conversation_secretary(
        conversation_id=retry_conversation, actor="userio-web:owner",
    )
    assert recovered["triage"]["status"] == "completed"
    assert recovered["triage"]["request_id"] == request_id
    assert retry_service._store._connection.execute(
        "SELECT attempts FROM workspace_triage WHERE event_seq=1"
    ).fetchone()[0] == 4
    retry_service.triage_conversation_secretary(
        conversation_id=retry_conversation, actor="userio-web:owner",
    )
    assert retry_service._store._connection.execute(
        "SELECT attempts FROM workspace_triage WHERE event_seq=1"
    ).fetchone()[0] == 4
    service, conversation_id = _service(tmp_path)
    store = service._store
    first = service.request_conversation_secretary_deep(
        conversation_id=conversation_id, actor="userio-web:owner",
    )["deep"]
    store.update_conversation_secretary_job(
        job_id=first["job_id"], phase="accepted", worker_id="worker-1",
    )
    failed = store.update_conversation_secretary_job(
        job_id=first["job_id"], phase="failed", worker_id="worker-1", error="Provider unavailable",
    )
    assert service.request_conversation_secretary_deep(
        conversation_id=conversation_id, actor="userio-web:owner",
    )["deep"] == failed
    retry = service.request_conversation_secretary_deep(
        conversation_id=conversation_id, actor="userio-web:owner", retry_of_job_id=first["job_id"],
    )["deep"]
    assert retry["job_id"] != first["job_id"]
    assert retry["attempt"] == 2
    assert service.request_conversation_secretary_deep(
        conversation_id=conversation_id, actor="userio-web:owner", retry_of_job_id=first["job_id"],
    )["deep"]["job_id"] == retry["job_id"]
    assert store.conversation_secretary_jobs(status="failed") == [failed]
    store.update_conversation_secretary_job(
        job_id=first["job_id"], phase="failed", worker_id="worker-1", error="Provider unavailable",
    )
    assert store.conversation_secretary(conversation_id)["deep"]["job_id"] == retry["job_id"]
    store.update_conversation_secretary_job(
        job_id=retry["job_id"], phase="accepted", worker_id="worker-2",
    )
    store.update_conversation_secretary_job(
        job_id=retry["job_id"], phase="failed", worker_id="worker-2", error="Second failure",
    )
    replay = service.request_conversation_secretary_deep(
        conversation_id=conversation_id, actor="userio-web:owner", retry_of_job_id=first["job_id"],
    )["deep"]
    assert replay["job_id"] == retry["job_id"] and replay["status"] == "failed"
    third = service.request_conversation_secretary_deep(
        conversation_id=conversation_id, actor="userio-web:owner", retry_of_job_id=retry["job_id"],
    )["deep"]
    assert third["attempt"] == 3 and third["parent_job_id"] == retry["job_id"]
    store.set_conversation_account(conversation_id, "telegram:changed")
    assert store.conversation_secretary_jobs()[0]["account_ref"] == "telegram:owner"


def test_secretary_claim_is_exclusive_across_store_connections(tmp_path) -> None:
    service, conversation_id = _service(tmp_path)
    job = service.request_conversation_secretary_deep(
        conversation_id=conversation_id, actor="userio-web:owner",
    )["deep"]
    other = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    barrier = threading.Barrier(2)
    outcomes = []

    def claim(store, worker):
        barrier.wait()
        try:
            result = store.update_conversation_secretary_job(
                job_id=job["job_id"], phase="accepted", worker_id=worker,
            )
            outcomes.append(result["worker_id"])
        except ValueError as error:
            outcomes.append(str(error))

    workers = [threading.Thread(target=claim, args=(store, worker))
               for store, worker in [(service._store, "first"), (other, "second")]]
    for worker in workers: worker.start()
    for worker in workers: worker.join(timeout=3)
    assert all(not worker.is_alive() for worker in workers)
    assert len(outcomes) == 2
    assert sum("worker binding is immutable" in item for item in outcomes) == 1


def test_secretary_migrates_pre_retry_schema_without_rebuilding_on_reopen(tmp_path) -> None:
    service, conversation_id = _service(tmp_path)
    store = service._store
    job = service.request_conversation_secretary_deep(
        conversation_id=conversation_id, actor="userio-web:owner",
    )["deep"]
    user = store.owner().user_id
    with store._connection:
        store._connection.execute("DROP TABLE conversation_secretary_jobs")
        store._connection.execute("""CREATE TABLE conversation_secretary_jobs (
            user_id TEXT NOT NULL,job_id TEXT NOT NULL,conversation_id TEXT NOT NULL,
            event_seq INTEGER NOT NULL,triage_request_id TEXT NOT NULL,actor TEXT NOT NULL,
            status TEXT NOT NULL,worker_id TEXT,session_id TEXT,session_url TEXT,
            result_text TEXT,last_error TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL,
            PRIMARY KEY(user_id,job_id),UNIQUE(user_id,conversation_id,event_seq))""")
        store._connection.execute("""INSERT INTO conversation_secretary_jobs
            (user_id,job_id,conversation_id,event_seq,triage_request_id,actor,status,
             worker_id,last_error,created_at,updated_at) VALUES (?,?,?,?,?,?,'failed',?,?,?,?)""",
            (user,job["job_id"],conversation_id,job["event_seq"],job["triage_request_id"],
             job["actor"],"legacy-worker","Legacy error",job["created_at"],job["updated_at"]))
    store._connection.close()
    migrated = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    preserved = migrated.conversation_secretary_job(job["job_id"])
    assert preserved["status"] == "failed" and preserved["error"] == "Legacy error"
    assert preserved["worker_id"] == "legacy-worker" and preserved["attempt"] == 1
    assert preserved["account_ref"] == ""  # Never invent historical account provenance.
    assert preserved["message_id"] == "540308572:44"
    version = migrated._connection.execute("PRAGMA schema_version").fetchone()[0]
    migrated._connection.close()
    reopened = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    assert reopened._connection.execute("PRAGMA schema_version").fetchone()[0] == version
    assert reopened.conversation_secretary_job(job["job_id"]) == preserved


def test_secretary_migration_preserves_intermediate_attempt_history(tmp_path) -> None:
    service, conversation_id = _service(tmp_path)
    store = service._store
    job = service.request_conversation_secretary_deep(
        conversation_id=conversation_id, actor="userio-web:owner",
    )["deep"]
    user = store.owner().user_id
    child_id = "secretary-" + "b" * 32
    with store._connection:
        store._connection.execute("DROP TABLE conversation_secretary_jobs")
        store._connection.execute("""CREATE TABLE conversation_secretary_jobs (
            user_id TEXT NOT NULL,job_id TEXT NOT NULL,conversation_id TEXT NOT NULL,
            event_seq INTEGER NOT NULL,triage_request_id TEXT NOT NULL,actor TEXT NOT NULL,
            source TEXT NOT NULL,account_ref TEXT NOT NULL,peer_id TEXT NOT NULL,
            attempt INTEGER NOT NULL,status TEXT NOT NULL,worker_id TEXT,session_id TEXT,
            session_url TEXT,result_text TEXT,last_error TEXT,created_at REAL NOT NULL,
            updated_at REAL NOT NULL,PRIMARY KEY(user_id,job_id),
            UNIQUE(user_id,conversation_id,event_seq,attempt))""")
        for attempt, identity in [(1, job["job_id"]), (2, child_id)]:
            store._connection.execute("""INSERT INTO conversation_secretary_jobs
                (user_id,job_id,conversation_id,event_seq,triage_request_id,actor,
                 source,account_ref,peer_id,attempt,status,worker_id,last_error,created_at,updated_at)
                 VALUES (?,?,?,?,?,?,?,?,?,?,'failed','legacy-worker',?,?,?)""",
                (user,identity,conversation_id,job["event_seq"],job["triage_request_id"],job["actor"],
                 job["source"],job["account_ref"],job["peer_id"],attempt,"Legacy error",float(attempt),float(attempt)))
    store._connection.close()
    migrated = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    assert migrated.conversation_secretary_job(job["job_id"])["attempt"] == 1
    child = migrated.conversation_secretary_job(child_id)
    assert child["parent_job_id"] == job["job_id"] and child["attempt"] == 2
    assert child["account_ref"] == job["account_ref"] and child["error"] == "Legacy error"
    successor = migrated.retry_conversation_secretary_job(
        job_id=child_id, actor="userio-web:owner", conversation_id=conversation_id,
    )
    assert successor["attempt"] == 3 and successor["parent_job_id"] == child_id
