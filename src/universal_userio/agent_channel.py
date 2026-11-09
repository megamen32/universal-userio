"""Browser-agent command channel: operator enqueues commands, the extension
long-polls them, executes them inside the user's real logged-in browser, and
posts results back. Same no-database design as collect.py: JSONL audit files
plus an in-memory pending queue with a Condition for long-poll wakeups.

Deliberate boundary: the operator never receives site credentials -- commands
run inside the browser and only their JSON-serializable outputs come back.

Identity model (one extension profile = one browser UUID):

* A route is the pair (owner, agent_id). A poll carries an optional
  instance_id -- the browser UUID of the calling extension profile.
* When several different profiles poll the same route, the route is
  contested: its queue has no certain addressee, so it is HELD rather than
  handed to an arbitrary profile. Commands addressed to a browser UUID are
  always deliverable to that UUID's own queue.
* The fact "this route was seen under two profiles" is durable (private,
  owner-scoped JSON ledger) so it survives a restart and a TTL expiry. Only a
  correct migration (resolve_owner) ends the contest.
* Queues, receipts and UNKNOWN commands are never rewritten by this layer.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path

COMMANDS_FILE = Path(os.environ.get(
    "USERIO_AGENT_COMMANDS_FILE", "/var/lib/universal-userio/agent-commands.jsonl"))
RESULTS_FILE = Path(os.environ.get(
    "USERIO_AGENT_RESULTS_FILE", "/var/lib/universal-userio/agent-results.jsonl"))
IDENTITY_DIR = Path(os.environ.get(
    "USERIO_AGENT_IDENTITY_DIR", "/var/lib/universal-userio/agent-identity"))
MAX_RESULT_BYTES = 4_000_000
MAX_WAIT_SEC = 30
MAX_PENDING_PER_AGENT = 32
ACTIVE_POLLER_TTL = 90.0
INSTANCE_RE = re.compile(r"browser-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
LEGACY_PEER = "legacy"
IDENTITY_SCHEMA = "universal.agent.identity.v1"

_LOCK = threading.Lock()
# (user, agent_id) -> deque of queued commands not yet delivered.
_PENDING: dict[tuple[str, str], deque] = {}
_COND = threading.Condition(_LOCK)
_LAST_POLL: dict[tuple[str, str], float] = {}
# Different extension profiles must not compete for one legacy agent ID.
_ACTIVE_POLLERS: dict[tuple[str, str], dict[str, float]] = {}
# command id -> result object, kept so internal callers can block on one.
_RESULTS: dict[str, dict] = {}
_WAITING: set[str] = set()


def _now() -> float:
    return time.time()


# --- durable identity ledger ---------------------------------------------


def _identity_path(user: str, agent_id: str) -> Path:
    digest = hashlib.sha256(f"{user}\x00{agent_id}".encode()).hexdigest()
    return IDENTITY_DIR / f"{digest}.json"


def _empty_record() -> dict:
    return {"instances": set(), "legacy_seen": False, "owner_instance": ""}


def _read_identity_record(user: str, agent_id: str) -> tuple[dict, str]:
    """Read the durable ledger for one route.

    Returns (record, error). The error is empty for a healthy read,
    "missing" when the route was never seen, and "corrupt" or "unreadable"
    when the file exists but cannot be trusted. A corrupt or unreadable
    ledger is never silently treated as "no ambiguity": the route stays
    contested and the error is reported to its owner.
    """
    path = _identity_path(user, agent_id)
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty_record(), "missing"
    except OSError:
        return _empty_record(), "unreadable"
    try:
        raw = json.loads(raw_text)
    except ValueError:
        return _empty_record(), "corrupt"
    if not isinstance(raw, dict) or raw.get("schema") != IDENTITY_SCHEMA:
        return _empty_record(), "corrupt"
    if raw.get("user") != user or raw.get("agent_id") != agent_id:
        return _empty_record(), "corrupt"
    instances = raw.get("instances")
    if not isinstance(instances, list):
        return _empty_record(), "corrupt"
    parsed: set[str] = set()
    for value in instances:
        text = str(value)
        # A tampered entry is a damaged ledger, not an empty one: dropping it
        # silently could make a contested route look unambiguous again.
        if not INSTANCE_RE.fullmatch(text) or text in parsed:
            return _empty_record(), "corrupt"
        parsed.add(text)
    if "legacy_seen" not in raw or "owner_instance" not in raw:
        return _empty_record(), "corrupt"
    legacy_seen = raw["legacy_seen"]
    if not isinstance(legacy_seen, bool):
        return _empty_record(), "corrupt"
    owner = str(raw.get("owner_instance") or "")
    if raw["owner_instance"] and not INSTANCE_RE.fullmatch(owner):
        return _empty_record(), "corrupt"
    return {
        "instances": parsed,
        "legacy_seen": legacy_seen,
        "owner_instance": owner if INSTANCE_RE.fullmatch(owner) else "",
    }, ""


def _persist_identity(user: str, agent_id: str, record: dict) -> None:
    """Atomically and durably write the ledger for one route."""
    IDENTITY_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(IDENTITY_DIR, 0o700)
    payload = {
        "schema": IDENTITY_SCHEMA,
        "user": user,
        "agent_id": agent_id,
        "instances": sorted(record["instances"]),
        "legacy_seen": bool(record["legacy_seen"]),
        "owner_instance": record["owner_instance"],
        "updated_at": _now(),
    }
    path = _identity_path(user, agent_id)
    temp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        os.chmod(path, 0o600)
    finally:
        if temp.exists():
            temp.unlink()
    directory = os.open(IDENTITY_DIR, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _record_identity(user: str, agent_id: str, *, instance_id: str = "",
                     legacy_seen: bool = False) -> dict:
    """Store only genuinely new information about a route."""
    record, error = _read_identity_record(user, agent_id)
    if error and error != "missing":
        return record
    changed = False
    if instance_id and instance_id not in record["instances"]:
        record["instances"].add(instance_id)
        changed = True
    if legacy_seen and not record["legacy_seen"]:
        record["legacy_seen"] = True
        changed = True
    if changed:
        _persist_identity(user, agent_id, record)
    return record


def _durable_routes(user: str) -> dict[str, dict]:
    """Every route this owner was seen on, read back from disk."""
    routes: dict[str, dict] = {}
    try:
        paths = sorted(IDENTITY_DIR.glob("*.json"))
    except OSError:
        return routes
    for path in paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict) or raw.get("user") != user:
            continue
        agent_id = str(raw.get("agent_id") or "")
        if not agent_id:
            continue
        if _identity_path(user, agent_id) != path:
            # The file contents claim a different route than their digest:
            # treat it as a corrupt ledger for the claimed route.
            routes[agent_id] = {**_empty_record(), "error": "corrupt"}
            continue
        record, error = _read_identity_record(user, agent_id)
        routes[agent_id] = {**record, "error": error}
    return routes


# --- command queue --------------------------------------------------------


def enqueue(payload: dict, *, user: str) -> dict:
    agent_id = str(payload.get("agent_id") or "").strip()
    action = str(payload.get("action") or "").strip()
    if not agent_id or not action:
        raise ValueError("agent_id and action are required")
    args = payload.get("args")
    if args is not None and not isinstance(args, dict):
        raise ValueError("args must be an object")
    command = {
        "id": f"{agent_id}:{uuid.uuid4().hex}",
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


def _live_peers(key: tuple[str, str]) -> dict[str, float]:
    now = _now()
    peers = _ACTIVE_POLLERS.get(key, {})
    return {name: seen for name, seen in peers.items() if now - seen <= ACTIVE_POLLER_TTL}


def _route_is_ambiguous(agent_id: str, record: dict, error: str, known: set[str]) -> bool:
    # A legacy alias cannot identify its owner merely because only one modern
    # profile has polled so far: another profile may appear after a restart.
    # Callers must probe the exact room/session and target the instance UUID.
    return (bool(error) or len(known) > 1
            or any(instance != agent_id for instance in known)
            or (record["legacy_seen"] and bool(known)))


def _route_view(user: str, agent_id: str) -> dict:
    """Merged live and durable view of one route, as its owner sees it."""
    key = (user, agent_id)
    record, error = _read_identity_record(user, agent_id)
    peers = _live_peers(key)
    live_instances = {name for name in peers if name != LEGACY_PEER}
    known = set(record["instances"]) | live_instances
    ambiguous = _route_is_ambiguous(agent_id, record, error, known)
    pinned = bool(record["owner_instance"])
    return {
        "agent_id": agent_id,
        "instances": sorted(live_instances),
        "known_instances": sorted(known),
        "identity_conflict": ambiguous and not pinned,
        "legacy_unaddressable": LEGACY_PEER in peers,
        "legacy_held": ambiguous and not pinned,
        "legacy_seen": record["legacy_seen"],
        "owner_instance": record["owner_instance"],
        "identity_error": error,
    }


def poll(agent_id: str, wait: str | int, *, user: str, instance_id: str = "") -> dict:
    agent_id = str(agent_id or "").strip()
    if not agent_id:
        raise ValueError("agent_id required")
    instance_id = str(instance_id or "").strip()
    if instance_id and not INSTANCE_RE.fullmatch(instance_id):
        raise ValueError("invalid instance_id")
    try:
        wait_sec = min(max(float(wait), 0.0), float(MAX_WAIT_SEC))
    except (TypeError, ValueError):
        raise ValueError("wait must be a number") from None

    key = (user, agent_id)
    own_key = (user, instance_id) if instance_id else None
    deadline = _now() + wait_sec
    peer = instance_id or LEGACY_PEER
    command = None
    conflict = False
    contested_now = False
    with _COND:
        _LAST_POLL[key] = _now()
        peers = _ACTIVE_POLLERS.setdefault(key, {})
        peers[peer] = _now()
        _COND.notify_all()
        _record_identity(user, agent_id, instance_id=instance_id,
                         legacy_seen=not instance_id)
        while True:
            now = _now()
            for name, seen in list(peers.items()):
                if now - seen > ACTIVE_POLLER_TTL:
                    del peers[name]
            # Re-derive the routing decision on every wakeup: a profile that
            # appears mid-wait must immediately make the shared queue
            # contested instead of losing a race.
            ledger, ledger_error = _read_identity_record(user, agent_id)
            owner = ledger["owner_instance"]
            known = set(ledger["instances"]) | {
                name for name in peers if name != LEGACY_PEER}
            ambiguous = _route_is_ambiguous(agent_id, ledger, ledger_error, known)
            contested_now = ambiguous or ledger_error or len(peers) > 1
            # 1. Commands addressed to this profile's own browser UUID are
            #    always deliverable: that is the profile's private address.
            if own_key is not None:
                own_queue = _PENDING.get(own_key)
                if own_queue:
                    command = own_queue.popleft()
                    break
            # 2. A migrated route has exactly one addressee.
            if owner:
                if peer != owner:
                    conflict = True
                    break
                queue = _PENDING.get(key)
                if queue:
                    command = queue.popleft()
                    break
            else:
                # 3. An unreadable or corrupt ledger never resolves silently.
                if ledger_error:
                    conflict = True
                    break
                # 4. Hold an unverified alias even before a second profile
                #    polls. Addressed UUID queues remain available above.
                if ambiguous:
                    conflict = True
                    break
                queue = _PENDING.get(key)
                if queue:
                    command = queue.popleft()
                    break
            remaining = deadline - _now()
            if remaining <= 0:
                break
            _COND.wait(timeout=remaining)
    answer = {"command": command, "identity_conflict": conflict}
    if instance_id:
        answer["address"] = instance_id
    if conflict:
        view = _route_view(user, agent_id)
        answer["instances"] = view["instances"]
        answer["known_instances"] = view["known_instances"]
        if view["identity_error"]:
            answer["identity_error"] = view["identity_error"]
    elif command is not None and contested_now:
        # Delivered an addressed command while the shared route is contested.
        # identity_conflict stays false so the extension processes the command
        # instead of treating the response as an error; this flag is advisory.
        answer["route_contested"] = True
    return answer


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
        if command_id in _WAITING:
            _RESULTS[command_id] = record
        with RESULTS_FILE.open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
        _COND.notify_all()
    return {"accepted": True, "id": command_id}


def call(agent_id: str, action: str, args: dict | None = None, *, user: str,
         timeout_sec: float = 60.0) -> dict:
    """Enqueue a command and block until the extension posts its result.

    Server-side transport for flows that must run inside the real browser
    (for example ChatGPT delivery when Cloudflare rejects datacenter POSTs).
    """
    queued = enqueue({"agent_id": agent_id, "action": action, "args": args or {}}, user=user)
    command_id = queued["id"]
    deadline = _now() + max(1.0, min(timeout_sec, 120.0))
    with _COND:
        _WAITING.add(command_id)
        try:
            while command_id not in _RESULTS:
                remaining = deadline - _now()
                if remaining <= 0:
                    raise RuntimeError(f"agent {agent_id!r} did not answer {action!r} in time")
                _COND.wait(timeout=remaining)
            result = _RESULTS.pop(command_id)["result"]
        finally:
            _WAITING.discard(command_id)
            _RESULTS.pop(command_id, None)
    if not result.get("ok"):
        raise RuntimeError(f"{action} via {agent_id}: {result.get('error') or 'failed'}")
    return result


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


def resolve_owner(user: str, agent_id: str, instance_id: str) -> dict:
    """Pin one profile as the owner of a route: the end of a correct migration.

    The queue and every UNKNOWN command stay untouched; only the routing
    decision changes, and it survives a restart because it is on disk.
    """
    agent_id = str(agent_id or "").strip()
    instance_id = str(instance_id or "").strip()
    if not agent_id:
        raise ValueError("agent_id required")
    if not INSTANCE_RE.fullmatch(instance_id):
        raise ValueError("invalid instance_id")
    with _LOCK:
        record, error = _read_identity_record(user, agent_id)
        if error and error != "missing":
            raise ValueError(f"identity ledger is {error}")
        record["instances"].add(instance_id)
        record["owner_instance"] = instance_id
        _persist_identity(user, agent_id, record)
    return {"resolved": True, "agent_id": agent_id, "owner_instance": instance_id}


def identity_report(*, user: str) -> dict:
    """Durable identity facts for one owner."""
    routes = []
    for agent_id, record in _durable_routes(user).items():
        view = _route_view(user, agent_id)
        routes.append({
            "agent_id": agent_id,
            "instances": view["known_instances"],
            "owner_instance": view["owner_instance"],
            "identity_conflict": bool(view["identity_conflict"] and not view["owner_instance"]),
            "legacy_seen": record["legacy_seen"],
            "identity_error": view["identity_error"],
        })
    return {"routes": sorted(routes, key=lambda item: item["agent_id"])}


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
        for agent_id in _durable_routes(user):
            agents.setdefault(agent_id, {"agent_id": agent_id, "pending": 0})
        for agent_id in list(agents):
            view = _route_view(user, agent_id)
            entry = agents[agent_id]
            entry["active_instances"] = len(view["instances"])
            entry["instances"] = view["instances"]
            entry["known_instances"] = view["known_instances"]
            entry["identity_conflict"] = bool(view["identity_conflict"] and not view["owner_instance"])
            entry["legacy_unaddressable"] = bool(view["legacy_unaddressable"])
            entry["legacy_seen"] = bool(view["legacy_seen"])
            entry["owner_instance"] = view["owner_instance"]
            if view["identity_error"]:
                entry["identity_error"] = view["identity_error"]
    return {"agents": sorted(agents.values(), key=lambda a: a["agent_id"])}
