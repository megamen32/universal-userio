"""Browser-agent command channel: operator enqueues commands, the extension
long-polls them, executes them inside the user's real logged-in browser, and
posts results back. Same no-database design as collect.py: JSONL audit files
plus an in-memory pending queue with a Condition for long-poll wakeups.

Deliberate boundary: the operator never receives site credentials — commands
run inside the browser and only their JSON-serializable outputs come back.
"""

from __future__ import annotations

import itertools
import json
import os
import threading
import time
from collections import deque
from pathlib import Path

COMMANDS_FILE = Path(os.environ.get(
    "USERIO_AGENT_COMMANDS_FILE", "/var/lib/universal-userio/agent-commands.jsonl"))
RESULTS_FILE = Path(os.environ.get(
    "USERIO_AGENT_RESULTS_FILE", "/var/lib/universal-userio/agent-results.jsonl"))
MAX_RESULT_BYTES = 4_000_000
MAX_WAIT_SEC = 30
MAX_PENDING_PER_AGENT = 32

_LOCK = threading.Lock()
# (user, agent_id) -> deque of queued commands not yet delivered.
_PENDING: dict[tuple[str, str], deque] = {}
_COND = threading.Condition(_LOCK)
_LAST_POLL: dict[tuple[str, str], float] = {}
_SEQ = itertools.count(1)


def _now() -> float:
    return time.time()


def enqueue(payload: dict, *, user: str) -> dict:
    agent_id = str(payload.get("agent_id") or "").strip()
    action = str(payload.get("action") or "").strip()
    if not agent_id or not action:
        raise ValueError("agent_id and action are required")
    args = payload.get("args")
    if args is not None and not isinstance(args, dict):
        raise ValueError("args must be an object")
    command = {
        "id": f"{agent_id}:{next(_SEQ)}",
        "agent_id": agent_id,
        "action": action,
        "args": args or {},
        "queued_at": _now(),
        "user": user,
    }
    record = dict(command)
    COMMANDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        key = (user, agent_id)
        queue = _PENDING.setdefault(key, deque())
        if len(queue) >= MAX_PENDING_PER_AGENT:
            raise ValueError(f"pending queue full for {agent_id}")
        queue.append(command)
        _COND.notify_all()
    with _LOCK:
        with COMMANDS_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"queued": True, "id": command["id"], "agent_id": agent_id, "action": action}


def poll(agent_id: str, wait: str | int, *, user: str) -> dict:
    agent_id = str(agent_id or "").strip()
    if not agent_id:
        raise ValueError("agent_id required")
    try:
        wait_sec = min(max(float(wait), 0.0), float(MAX_WAIT_SEC))
    except (TypeError, ValueError):
        raise ValueError("wait must be a number") from None

    key = (user, agent_id)
    deadline = _now() + wait_sec
    command = None
    with _COND:
        _LAST_POLL[key] = _now()
        while True:
            queue = _PENDING.get(key)
            if queue:
                command = queue.popleft()
                break
            remaining = deadline - _now()
            if remaining <= 0:
                break
            _COND.wait(timeout=remaining)
    return {"command": command}


def push_result(payload: dict, *, user: str) -> dict:
    command_id = str(payload.get("id") or "").strip()
    if not command_id:
        raise ValueError("id required")
    outcome = payload.get("result")
    if outcome is None or not isinstance(outcome, dict):
        raise ValueError("result object required")
    record = {
        "type": "universal.agent.result.v1",
        "id": command_id,
        "agent_id": str(payload.get("agent_id") or ""),
        "action": str(payload.get("action") or ""),
        "result": outcome,
        "user": user,
        "received_at": _now(),
    }
    encoded = json.dumps(record, ensure_ascii=False)
    if len(encoded.encode()) > MAX_RESULT_BYTES:
        raise ValueError("result exceeds MAX_RESULT_BYTES")
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with RESULTS_FILE.open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
    return {"accepted": True, "id": command_id}


def read_results(*, user: str, agent_id: str = "", limit: str = "20") -> dict:
    try:
        count = int(limit)
    except (TypeError, ValueError):
        raise ValueError("limit must be an integer") from None
    count = max(1, min(count, 200))
    try:
        lines = RESULTS_FILE.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    results = []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("user") != user:
            continue
        if agent_id and record.get("agent_id") != agent_id:
            continue
        results.append(record)
    return {"results": results[::-1][:count]}


def status(*, user: str) -> dict:
    agents: dict[str, dict] = {}
    with _LOCK:
        for (owner, agent_id), queue in _PENDING.items():
            if owner != user:
                continue
            entry = agents.setdefault(agent_id, {"agent_id": agent_id, "pending": 0})
            entry["pending"] = len(queue)
        for (owner, agent_id), at in _LAST_POLL.items():
            if owner != user:
                continue
            entry = agents.setdefault(agent_id, {"agent_id": agent_id, "pending": 0})
            entry["last_poll_at"] = at
            entry["seconds_since_poll"] = round(_now() - at, 1)
    return {"agents": sorted(agents.values(), key=lambda a: a["agent_id"])}
