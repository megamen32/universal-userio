"""Operator-published site collection tasks and browser-agent results.

Tasks live in one JSON file the operator edits; results are appended to a
JSONL file by authenticated clients (the browser extension). Deliberately no
database and no scheduler: the extension polls, the operator edits the file.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

TASKS_FILE = Path(os.environ.get("USERIO_COLLECT_TASKS_FILE", "/var/lib/universal-userio/collect-tasks.json"))
RESULTS_FILE = Path(os.environ.get("USERIO_COLLECT_RESULTS_FILE", "/var/lib/universal-userio/collect-results.jsonl"))
MAX_RESULT_BYTES = 1_000_000

_LOCK = threading.Lock()


def load_tasks() -> list[dict]:
    try:
        raw = TASKS_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    tasks = json.loads(raw)
    if not isinstance(tasks, list):
        raise ValueError("collect tasks file must contain a JSON list")
    return tasks


def active_tasks() -> dict:
    published = []
    for task in load_tasks():
        if not isinstance(task, dict) or not task.get("active", True):
            continue
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            raise ValueError("collect task requires a non-empty id")
        published.append({
            "id": task_id,
            "type": "universal.collect.task.v1",
            "title": str(task.get("title") or task_id),
            "site": str(task.get("site") or ""),
            "recipe": dict(task.get("recipe") or {}),
            "every_sec": int(task.get("every_sec") or 300),
        })
    return {"tasks": published}


def append_result(payload: dict, *, user: str) -> None:
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("task_id required")
    if str(payload.get("status") or "") not in {"ok", "error"}:
        raise ValueError("status must be ok or error")
    record = {
        "type": "universal.collect.result.v1",
        "task_id": task_id,
        "status": str(payload["status"]),
        "http_status": int(payload.get("http_status") or 0),
        "data": payload.get("data"),
        "error": payload.get("error"),
        "fetched_at": str(payload.get("fetched_at") or ""),
        "agent": str(payload.get("agent") or ""),
        "user": user,
        "received_at": time.time(),
    }
    encoded = json.dumps(record, ensure_ascii=False)
    if len(encoded.encode()) > MAX_RESULT_BYTES:
        raise ValueError("result exceeds MAX_RESULT_BYTES")
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with RESULTS_FILE.open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")


def read_results(task_id: str | None = None, *, limit: str = "50") -> dict:
    try:
        count = int(limit)
    except (TypeError, ValueError):
        raise ValueError("limit must be an integer") from None
    count = max(1, min(count, 500))
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
        if task_id and record.get("task_id") != task_id:
            continue
        results.append(record)
    return {"results": results[::-1][:count]}
