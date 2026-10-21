"""Opt-in derived semantic index over UserIO's own retained message history.

Qdrant holds vectors plus minimal scope metadata (owner, chat, message identity,
body hash) and never a second copy of the message text. Every candidate is
re-read from the live ``messages`` row and re-checked against the current owner
authorization before it is emitted, so the index can never widen access, return
deleted text, or serve a stale body. Without configuration every caller keeps
working on text alone.

Nothing here embeds or sends a message anywhere until the owner has explicitly
opted in, and the embeddings endpoint and collection are the owner's own.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from typing import Any, Sequence

CHUNK_CHARS = 1200
CHUNKS_PER_REQUEST = 8
POINT_HASH = 2048
MAX_CHUNK_ROWS = 400
# A CPU embedding backend answers a batch of eight ~1200-character chunks in
# roughly 50 seconds, which is close to the 60-second timeout this client used
# to hardcode. The timeout is a deployment knob so a slower embedding backend
# cannot abort the request and leave the indexer with a broken pipe.
EMBEDDING_TIMEOUT_SECONDS = 180


class SemanticUnavailable(RuntimeError):
    """The vector service cannot answer right now; the caller falls back to text."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"semantic search unavailable: {reason}")


@dataclass(frozen=True, slots=True)
class SemanticSettings:
    enabled: bool = False
    qdrant_url: str = ""
    qdrant_collection: str = ""
    qdrant_token: str = ""
    qdrant_ca_path: str = ""
    embedding_url: str = ""
    embedding_model: str = ""
    embedding_token: str = ""
    dimensions: int = POINT_HASH
    embedding_timeout: int = EMBEDDING_TIMEOUT_SECONDS

    def configured(self) -> bool:
        return bool(self.qdrant_url and self.qdrant_collection
                    and self.embedding_url and self.embedding_model)

    def public(self) -> dict[str, Any]:
        """Owner-facing view. Secrets are reported as presence, never as values."""
        return {
            "enabled": self.enabled, "qdrant_url": self.qdrant_url,
            "qdrant_collection": self.qdrant_collection,
            "embedding_url": self.embedding_url, "embedding_model": self.embedding_model,
            "dimensions": self.dimensions if self.enabled else 0,
            "embedding_timeout": self.embedding_timeout,
            "has_qdrant_key": bool(self.qdrant_token),
            "has_embedding_key": bool(self.embedding_token),
        }


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def resolve_settings(store: Any, *, user_id: str | None = None,
                     environment: dict[str, str] | None = None) -> SemanticSettings:
    """Per-user opt-in settings over the server-side default configuration.

    Empty per-user fields inherit the deployment defaults, so an operator can
    provision the endpoint once and owners only enable and scope it.
    """
    environment = dict(os.environ if environment is None else environment)
    # Deployment credentials belong to the configured owner. Another user must
    # opt in with their own service settings, including on the direct search path.
    if store._user(user_id) != store.owner().user_id:
        environment = {}
    saved = store.user_search_settings(user_id=user_id) or {}

    def pick(key: str, env_key: str) -> str:
        return str(saved.get(key) or "").strip() or environment.get(env_key, "").strip()

    try:
        dimensions = int(saved.get("dimensions") or environment.get("USERIO_SEARCH_EMBEDDING_DIMENSIONS")
                        or POINT_HASH)
    except (TypeError, ValueError):
        dimensions = POINT_HASH
    enabled = _truthy(saved.get("enabled")) if "enabled" in saved else _truthy(
        environment.get("USERIO_SEARCH_SEMANTIC_ENABLED"))
    # The timeout is not a credential, so it stays readable from the deployment
    # environment even for a user that inherits no other deployment value.
    try:
        embedding_timeout = int(
            environment.get("USERIO_SEARCH_EMBEDDING_TIMEOUT")
            or os.environ.get("USERIO_SEARCH_EMBEDDING_TIMEOUT")
            or EMBEDDING_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        embedding_timeout = EMBEDDING_TIMEOUT_SECONDS
    if embedding_timeout <= 0:
        embedding_timeout = EMBEDDING_TIMEOUT_SECONDS
    settings = SemanticSettings(
        enabled=enabled,
        qdrant_url=pick("qdrant_url", "USERIO_SEARCH_QDRANT_URL"),
        qdrant_collection=pick("qdrant_collection", "USERIO_SEARCH_QDRANT_COLLECTION"),
        qdrant_token=pick("qdrant_token", "USERIO_SEARCH_QDRANT_TOKEN"),
        qdrant_ca_path=environment.get("USERIO_SEARCH_QDRANT_CA", "").strip(),
        embedding_url=pick("embedding_url", "USERIO_SEARCH_EMBEDDING_URL"),
        embedding_model=pick("embedding_model", "USERIO_SEARCH_EMBEDDING_MODEL"),
        embedding_token=pick("embedding_token", "USERIO_SEARCH_EMBEDDING_TOKEN"),
        dimensions=dimensions if dimensions > 0 else POINT_HASH,
        embedding_timeout=embedding_timeout,
    )
    return settings if settings.configured() else replace(settings, enabled=False)


def public_settings(store: Any, *, user_id: str | None = None) -> dict[str, Any]:
    """Show the effective connection (including inherited defaults), never keys."""
    saved = store.user_search_settings_public(user_id=user_id)
    effective = resolve_settings(store, user_id=user_id)
    if effective.configured():
        return {**saved, **effective.public()}
    return saved


# Qwen3-Embedding is an instruction-tuned retriever: the official model card
# requires the query to carry a task instruction while documents stay raw.
# Every other model keeps the query exactly as the caller wrote it.
QUERY_INSTRUCTION = "Given a query, retrieve relevant messages from a conversation history."
QUERY_INSTRUCTION_MODELS = ("qwen3-embedding",)


def instruction_query(model: str, query: str) -> str:
    """The query string this model expects, without touching the documents."""
    text = str(query or "")
    if not text.strip() or not any(marker in str(model or "").lower() for marker in QUERY_INSTRUCTION_MODELS):
        return text
    if text.lower().startswith("instruct:"):
        return text
    return f"Instruct: {QUERY_INSTRUCTION}\nQuery: {text}"


def chunk_text(body: str, size: int = CHUNK_CHARS) -> list[str]:
    """Split on whitespace where possible; a long unbroken block is hard-cut."""
    text = " ".join(str(body or "").split())
    if not text:
        return []
    chunks: list[str] = []
    while len(text) > size:
        cut = text.rfind(" ", 0, size)
        if cut < size // 2:
            cut = size
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        chunks.append(text)
    return chunks


def body_hash(body: str) -> str:
    return hashlib.sha256(" ".join(str(body or "").split()).encode("utf-8")).hexdigest()


def point_id(user_id: str, message_rowid: int, chunk: int) -> int:
    """Stable point id: the same message always maps to the same vector slot."""
    digest = hashlib.blake2b(
        f"{user_id}\0{int(message_rowid)}\0{int(chunk)}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") >> 1


def _ssl_context(ca_path: str) -> ssl.SSLContext | None:
    return ssl.create_default_context(cafile=ca_path) if ca_path and os.path.exists(ca_path) else None


def qdrant_request(settings: SemanticSettings, method: str, path: str,
                   body: Any = None, *, timeout: int = 30) -> Any:
    """One Qdrant call. Returns ``(status, payload)`` so a 404 stays visible."""
    url = settings.qdrant_url.rstrip("/") + path
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    headers = {"Content-Type": "application/json"}
    if settings.qdrant_token:
        headers["api-key"] = settings.qdrant_token
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    context = _ssl_context(settings.qdrant_ca_path) if url.startswith("https://") else None
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:  # noqa: S310
            raw = response.read(8_000_001)
    except urllib.error.HTTPError as error:
        detail = error.read()
        if error.code == 404:
            return 404, json.loads(detail or b"{}")
        raise SemanticUnavailable(f"qdrant http {error.code}") from None
    except (OSError, urllib.error.URLError) as error:
        raise SemanticUnavailable("qdrant unreachable") from error
    if len(raw) > 8_000_000:
        raise SemanticUnavailable("qdrant response exceeds limit")
    return 200, json.loads(raw or b"{}")


def embeddings_endpoint(base_url: str) -> str:
    """Normalise an OpenAI-compatible base URL to its embeddings path.

    Owners configure what their endpoint documents: the bare origin, the
    standard ``/v1`` base, or the full embeddings path. All three must work, and
    none may produce a doubled ``/v1/v1``.
    """
    base = str(base_url or "").strip().rstrip("/")
    if base.endswith("/embeddings"):
        return base
    if base.endswith("/v1"):
        return base + "/embeddings"
    return base + "/v1/embeddings"


def embed(settings: SemanticSettings, texts: Sequence[str], *, timeout: int | None = None) -> list[list[float]]:
    if not texts:
        return []
    request_timeout = settings.embedding_timeout if timeout is None else timeout
    url = embeddings_endpoint(settings.embedding_url)
    payload = {"model": settings.embedding_model, "input": list(texts)}
    request = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + settings.embedding_token} if settings.embedding_token else {})})
    try:
        with urllib.request.urlopen(request, timeout=request_timeout) as response:  # noqa: S310
            data = json.loads(response.read(32_000_001))
    except urllib.error.HTTPError as error:
        error.read()
        raise SemanticUnavailable(f"embeddings http {error.code}") from None
    except (OSError, urllib.error.URLError, ValueError) as error:
        raise SemanticUnavailable("embeddings unreachable") from error
    rows = sorted(data.get("data") or [], key=lambda row: int(row.get("index", 0)))
    if len(rows) != len(texts):
        raise SemanticUnavailable("embeddings returned an incomplete batch")
    vectors: list[list[float]] = []
    for row in rows:
        vector = [float(value) for value in (row.get("embedding") or [])
                  if isinstance(value, (int, float))]
        if len(vector) != settings.dimensions:
            raise SemanticUnavailable("embeddings dimension mismatch")
        norm = math.sqrt(sum(value * value for value in vector))
        vectors.append([value / norm for value in vector] if norm else [0.0] * settings.dimensions)
    return vectors


# --- durable derived state -------------------------------------------------


def initialize(store: Any) -> None:
    """Idempotent derived-state schema. Safe to call on every start."""
    store._connection.executescript('''
        CREATE TABLE IF NOT EXISTS search_vector_state (
            message_rowid INTEGER PRIMARY KEY, user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL, message_id TEXT NOT NULL,
            body_sha256 TEXT NOT NULL, chunks INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '',
            updated_at REAL NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS search_vector_state_state
            ON search_vector_state(state,message_rowid);
        CREATE TABLE IF NOT EXISTS search_vector_queue (
            message_rowid INTEGER PRIMARY KEY, operation TEXT NOT NULL DEFAULT 'upsert',
            created_at REAL NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS search_vector_queue_created
            ON search_vector_queue(created_at,message_rowid);
        CREATE TABLE IF NOT EXISTS search_vector_meta (
            name TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL);
    ''')


def attach_triggers(store: Any) -> None:
    """Keep the derived index in step with ingress, edits and deletions.

    The queue is only a hint: the worker re-reads the live row, so a stale or
    duplicated hint can never publish text the source no longer holds.
    """
    db = store._connection
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='search_vector_queue_insert'").fetchone():
        return
    db.execute('''CREATE TRIGGER search_vector_queue_insert AFTER INSERT ON messages BEGIN
        INSERT INTO search_vector_queue(message_rowid,operation,created_at)
        VALUES(new.rowid,'upsert',CAST(strftime('%s','now') AS REAL))
        ON CONFLICT(message_rowid) DO NOTHING; END''')
    db.execute('''CREATE TRIGGER search_vector_queue_update AFTER UPDATE OF body,deleted_at,conversation_id
        ON messages BEGIN
        INSERT INTO search_vector_queue(message_rowid,operation,created_at)
        VALUES(new.rowid,CASE WHEN new.deleted_at IS NOT NULL THEN 'delete' ELSE 'upsert' END,
          CAST(strftime('%s','now') AS REAL))
        ON CONFLICT(message_rowid) DO UPDATE
        SET operation=excluded.operation,created_at=excluded.created_at; END''')
    db.execute('''CREATE TRIGGER search_vector_queue_delete AFTER DELETE ON messages BEGIN
        INSERT INTO search_vector_queue(message_rowid,operation,created_at)
        VALUES(old.rowid,'delete',CAST(strftime('%s','now') AS REAL))
        ON CONFLICT(message_rowid) DO NOTHING; END''')


def meta(store: Any, name: str, default: str = "") -> str:
    row = store._connection.execute("SELECT value FROM search_vector_meta WHERE name=?", (name,)).fetchone()
    return str(row["value"]) if row else default


def set_meta(store: Any, name: str, value: str) -> None:
    store._connection.execute(
        "INSERT INTO search_vector_meta(name,value,updated_at) VALUES(?,?,?)"
        " ON CONFLICT(name) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
        (name, str(value), time.time()))


def status(store: Any, *, user_id: str | None = None,
           conversation_ids: Sequence[str] = ()) -> dict[str, Any]:
    """Index readiness. With conversation_ids the numbers describe that scope."""
    uid = store._user(user_id)
    db = store._connection
    scope = [str(value).strip() for value in conversation_ids if str(value).strip()][:100]
    clause = " AND conversation_id IN (%s)" % ",".join("?" * len(scope)) if scope else ""
    scope_args: list[Any] = [*scope]
    live = db.execute(
        "SELECT COUNT(*) FROM messages WHERE user_id=? AND deleted_at IS NULL"
        " AND length(trim(coalesce(body,'')))>0" + clause, (uid, *scope_args)).fetchone()[0]
    indexed = db.execute(
        "SELECT COUNT(*) FROM search_vector_state WHERE user_id=? AND state='indexed'" + clause,
        (uid, *scope_args)).fetchone()[0]
    chunks = db.execute(
        "SELECT COALESCE(SUM(chunks),0) FROM search_vector_state WHERE user_id=? AND state='indexed'"
        + clause, (uid, *scope_args)).fetchone()[0]
    pending = db.execute(
        "SELECT COUNT(*) FROM search_vector_queue q JOIN messages m ON m.rowid=q.message_rowid"
        " WHERE m.user_id=? AND m.deleted_at IS NULL" + clause.replace("conversation_id", "m.conversation_id"),
        (uid, *scope_args)).fetchone()[0]
    errors = db.execute(
        "SELECT COUNT(*) FROM search_vector_state WHERE user_id=? AND state='error'" + clause,
        (uid, *scope_args)).fetchone()[0]
    partial = db.execute(
        "SELECT COUNT(*) FROM search_vector_state WHERE user_id=? AND state='partial'" + clause,
        (uid, *scope_args)).fetchone()[0]
    return {"checkpoint": int(meta(store, f"checkpoint:{uid}", "0") or 0),
            "max_rowid": int(db.execute("SELECT COALESCE(MAX(rowid),0) FROM messages WHERE user_id=?",
                                        (uid,)).fetchone()[0]),
            "live_messages": int(live), "indexed_messages": int(indexed),
            "indexed_chunks": int(chunks), "pending": int(pending), "errors": int(errors),
            "partial": int(partial), "scope_conversations": len(scope),
            "coverage_ratio": round(int(indexed) / int(live), 4) if live else 0.0,
            "complete": bool(live) and int(indexed) >= int(live) and int(pending) == 0
            and int(partial) == 0 and int(errors) == 0}


def _write_state(store: Any, uid: str, row: Any, chunks: int, digest: str, state: str,
                 error: str = "") -> None:
    store._connection.execute(
        "INSERT INTO search_vector_state(message_rowid,user_id,conversation_id,message_id,"
        "body_sha256,chunks,state,error,updated_at) VALUES(?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(message_rowid) DO UPDATE SET conversation_id=excluded.conversation_id,"
        "message_id=excluded.message_id,body_sha256=excluded.body_sha256,chunks=excluded.chunks,"
        "state=excluded.state,error=excluded.error,updated_at=excluded.updated_at",
        (int(row["rowid"]), uid, str(row["conversation_id"]), str(row["message_id"]),
         digest, int(chunks), state, error, time.time()))


def index_rows(store: Any, settings: SemanticSettings, rows: Sequence[Any], *, uid: str,
               deadline: float | None = None, advance: bool = False) -> dict[str, int]:
    """Embed and upsert a bounded list of message rows.

    Every embedding request carries at most ``CHUNKS_PER_REQUEST`` chunks, so a
    single very large message cannot produce an unbounded request: its chunks
    simply continue in the next bounded slice. Chunk writes go through stable
    point ids, so a repeated run after an interruption overwrites the same slots
    instead of duplicating them. The checkpoint advances only over rows whose
    chunks are completely written, so an outage or a cancelled run resumes at
    the first unfinished row instead of skipping it forever.
    """
    db = store._connection
    plan: list[tuple[Any, int, str, str, int]] = []
    already_written: dict[int, int] = {}
    for row in rows:
        chunks = chunk_text(row["body"])
        digest = body_hash(row["body"])
        total = len(chunks)
        if not total:
            continue
        rowid = int(row["rowid"])
        # A message that did not fit into an earlier slice keeps its place:
        # the chunks already written are not embedded a second time, so a
        # message larger than one slice still finishes instead of restarting
        # from its first chunk on every slice. An edited body starts over,
        # because its stored digest no longer matches.
        already = 0
        prior = db.execute(
            "SELECT chunks, body_sha256, state FROM search_vector_state WHERE message_rowid=?",
            (rowid,)).fetchone()
        if (prior is not None and str(prior["state"]) == "partial"
                and str(prior["body_sha256"] or "") == digest):
            already = max(0, min(int(prior["chunks"] or 0), total))
        already_written[rowid] = already
        plan.extend((row, index, chunk, digest, total)
                    for index, chunk in enumerate(chunks) if index >= already)
    if not plan and not already_written:
        return {"indexed": 0, "chunks": 0, "failed": 0, "partial": 0, "complete_through": 0, "stopped": 0}
    written: dict[int, int] = dict(already_written)
    totals: dict[int, int] = {}
    failing: dict[int, str] = {}
    indexed = chunk_count = partial = 0
    highest_complete = 0
    stopped = False
    for start in range(0, len(plan), CHUNKS_PER_REQUEST):
        if deadline is not None and time.monotonic() >= deadline:
            stopped = True
            break
        batch = plan[start:start + CHUNKS_PER_REQUEST]
        try:
            vectors = embed(settings, [item[2] for item in batch])
            qdrant_request(
                settings, "PUT", f"/collections/{settings.qdrant_collection}/points?wait=false",
                {"points": [{"id": point_id(uid, int(item[0]["rowid"]), item[1]), "vector": list(vector),
                             "payload": {"user_id": uid,
                                         "conversation_id": str(item[0]["conversation_id"]),
                                         "message_rowid": int(item[0]["rowid"]),
                                         "message_id": str(item[0]["message_id"]),
                                         "body_sha256": item[3], "chunk": item[1]}}
                            for item, vector in zip(batch, vectors)]}, timeout=60)
        except SemanticUnavailable as error:
            for item in batch:
                failing[int(item[0]["rowid"])] = error.reason
            stopped = True
            break
        for item in batch:
            rowid = int(item[0]["rowid"])
            written[rowid] = written.get(rowid, 0) + 1
            totals[rowid] = item[4]
            chunk_count += 1
    for row in rows:
        rowid = int(row["rowid"])
        digest = body_hash(row["body"])
        if rowid in failing:
            _write_state(store, uid, row, written.get(rowid, 0), digest, "error", failing[rowid])
            continue
        count = written.get(rowid, 0)
        if count and count < totals.get(rowid, 0):
            _write_state(store, uid, row, count, digest, "partial",
                         "chunk budget reached before this message was complete")
            partial += 1
            continue
        if count:
            _write_state(store, uid, row, count, digest, "indexed")
            indexed += 1
            highest_complete = max(highest_complete, rowid)
    if advance and highest_complete:
        set_meta(store, f"checkpoint:{uid}", str(highest_complete))
    # Consume only hints this write is newer than, so an edit that arrived while
    # we were embedding stays queued instead of being silently dropped.
    for row in rows:
        rowid = int(row["rowid"])
        state = db.execute("SELECT state,updated_at FROM search_vector_state WHERE message_rowid=?",
                           (rowid,)).fetchone()
        if state is None or str(state["state"]) != "indexed":
            continue
        db.execute("DELETE FROM search_vector_queue WHERE message_rowid=? AND operation='upsert'"
                   " AND created_at<=?", (rowid, float(state["updated_at"])))
    db.commit()
    return {"indexed": indexed, "chunks": chunk_count, "failed": len(failing), "partial": partial,
            "complete_through": highest_complete, "stopped": 1 if stopped else 0}



def backfill_step(store: Any, settings: SemanticSettings, *, user_id: str | None = None,
                  limit: int = 200, deadline: float | None = None,
                  priority_conversation_ids: Sequence[str] = ()) -> dict[str, int]:
    """One bounded, resumable slice of the initial history index.

    The owner's declared priority conversations are served first so their
    answers become useful long before the whole archive is covered; the rest of
    the retained history follows in rowid order from the same checkpoint.
    """
    uid = store._user(user_id)
    checkpoint = int(meta(store, f"checkpoint:{uid}", "0") or 0)
    priority = [str(value).strip() for value in priority_conversation_ids if str(value).strip()][:100]
    limit = max(1, min(int(limit), MAX_CHUNK_ROWS))
    done = 0
    result = {"indexed": 0, "chunks": 0, "failed": 0, "partial": 0, "stopped": 0}
    if priority:
        placeholders = ",".join("?" * len(priority))
        rows = store._connection.execute(
            f"SELECT rowid,conversation_id,message_id,body FROM messages"
            f" WHERE user_id=? AND deleted_at IS NULL AND length(trim(coalesce(body,'')))>0"
            f" AND conversation_id IN ({placeholders})"
            f" AND rowid NOT IN (SELECT message_rowid FROM search_vector_state WHERE state='indexed')"
            f" ORDER BY rowid LIMIT ?", (uid, *priority, limit)).fetchall()
        if rows:
            result = index_rows(store, settings, rows, uid=uid, deadline=deadline)
            done += result["indexed"]
    remaining = max(0, limit - done)
    if remaining:
        rows = store._connection.execute(
            "SELECT rowid,conversation_id,message_id,body FROM messages"
            " WHERE rowid>? AND user_id=? AND deleted_at IS NULL"
            " AND length(trim(coalesce(body,'')))>0 ORDER BY rowid LIMIT ?",
            (checkpoint, uid, remaining)).fetchall()
        tail = index_rows(store, settings, rows, uid=uid, deadline=deadline, advance=True)
        result = {**tail, "indexed": result["indexed"] + tail["indexed"],
                  "chunks": result["chunks"] + tail["chunks"],
                  "failed": result["failed"] + tail["failed"],
                  "partial": result["partial"] + tail["partial"]}
    else:
        result = {"indexed": 0, "chunks": 0, "failed": 0, "partial": 0, "stopped": 0}
    result["priority_indexed"] = done
    return result


def drain_queue(store: Any, settings: SemanticSettings, *, user_id: str | None = None,
                limit: int = 200, deadline: float | None = None) -> dict[str, int]:
    """Apply ingress, edits and deletions recorded since the last pass.

    Only hints that were really applied leave the queue. A failed, partial or
    untouched row keeps its hint, so an outage or a cancelled step resumes with
    the work still owed instead of losing it.
    """
    uid = store._user(user_id)
    db = store._connection
    pending = db.execute(
        "SELECT q.message_rowid AS rowid, q.operation AS operation, m.rowid AS live_rowid,"
        " m.conversation_id AS conversation_id, m.message_id AS message_id, m.body AS body"
        " FROM search_vector_queue q LEFT JOIN messages m ON m.rowid=q.message_rowid"
        " WHERE COALESCE(m.user_id,?) = ? ORDER BY q.created_at, q.message_rowid LIMIT ?",
        (uid, uid, max(1, min(int(limit), MAX_CHUNK_ROWS)))).fetchall()
    upserts = [row for row in pending
               if row["operation"] == "upsert" and row["live_rowid"] and row["body"]]
    result = index_rows(store, settings, upserts, uid=uid, deadline=deadline)
    # A row counts as applied only when this run wrote every one of its chunks.
    applied = {int(row["rowid"]) for row in upserts
               if result.get("complete_through", 0) >= int(row["rowid"])}
    deleted = 0
    for row in pending:
        rowid = int(row["rowid"])
        if row["operation"] == "upsert" and row["live_rowid"] and row["body"]:
            if rowid not in applied:
                db.execute("UPDATE search_vector_queue SET created_at=0 WHERE message_rowid=?", (rowid,))
            continue
        state = db.execute("SELECT chunks FROM search_vector_state WHERE message_rowid=?",
                           (rowid,)).fetchone()
        count = int(state["chunks"]) if state else 0
        if count:
            qdrant_request(settings, "POST",
                           f"/collections/{settings.qdrant_collection}/points/delete?wait=false",
                           {"points": [point_id(uid, rowid, index) for index in range(count)]},
                           timeout=60)
        db.execute("DELETE FROM search_vector_state WHERE message_rowid=?", (rowid,))
        db.execute("DELETE FROM search_vector_queue WHERE message_rowid=?", (rowid,))
        deleted += 1
    db.commit()
    return {**result, "deleted": deleted, "deferred": len(upserts) - len(applied)}


def search(store: Any, settings: SemanticSettings, query: str, *, user_id: str | None = None,
           conversation_ids: Sequence[str] = (), limit: int = 50) -> list[dict[str, Any]]:
    """Return candidate message identities with their best score.

    Only identity comes from the index; the body and its authorization are read
    again from the live message row by the caller before anything is emitted.
    """
    uid = store._user(user_id)
    vector = embed(settings, [instruction_query(settings.embedding_model, query)])[0]
    must: list[dict[str, Any]] = [{"key": "user_id", "match": {"value": uid}}]
    if conversation_ids:
        must.append({"key": "conversation_id", "match": {"any": list(conversation_ids)}})
    _, result = qdrant_request(settings, "POST",
                               f"/collections/{settings.qdrant_collection}/points/search",
                               {"vector": vector, "limit": max(1, min(int(limit), 200)),
                                "with_payload": True, "filter": {"must": must}}, timeout=45)
    hits: dict[int, dict[str, Any]] = {}
    for item in result.get("result") or []:
        payload = item.get("payload") or {}
        try:
            rowid = int(payload.get("message_rowid"))
        except (TypeError, ValueError):
            continue
        score = float(item.get("score") or 0.0)
        if rowid not in hits or score > hits[rowid]["score"]:
            hits[rowid] = {"message_rowid": rowid, "score": score,
                           "message_id": str(payload.get("message_id") or ""),
                           "conversation_id": str(payload.get("conversation_id") or ""),
                           "body_sha256": str(payload.get("body_sha256") or "")}
    return sorted(hits.values(), key=lambda hit: -hit["score"])


def ensure_collection(settings: SemanticSettings) -> dict[str, Any]:
    """Make the owner's own configured collection usable, within a finite budget.

    A collection-scoped token may create nothing: when it cannot, the caller
    receives an actionable message instead of a silent index that never fills.
    An existing collection whose contract differs is reported, never modified or
    removed.
    """
    collection = f"/collections/{settings.qdrant_collection}"
    try:
        status, existing = qdrant_request(settings, "GET", collection, timeout=15)
    except SemanticUnavailable as error:
        raise SemanticUnavailable(f"{error.reason}; check the collection URL and key") from None
    if status == 404:
        try:
            created, _ = qdrant_request(settings, "PUT", collection, {
                "vectors": {"size": settings.dimensions, "distance": "Cosine", "on_disk": True},
                "hnsw_config": {"on_disk": True}, "on_disk_payload": True}, timeout=30)
        except SemanticUnavailable as error:
            raise SemanticUnavailable(
                f"collection {settings.qdrant_collection} does not exist and this key cannot "
                f"create it ({error.reason}); create that exact collection or grant create on it"
            ) from None
        if created not in (200, 201):
            raise SemanticUnavailable(
                f"collection {settings.qdrant_collection} was refused ({created}); "
                "create that exact collection or grant create on it")
        status, existing = qdrant_request(settings, "GET", collection, timeout=15)
        if status != 200:
            raise SemanticUnavailable("collection was created but is not readable")
    if status != 200:
        raise SemanticUnavailable(f"collection {settings.qdrant_collection} is not readable")
    info = existing.get("result") or {}
    vectors = (info.get("config") or {}).get("params", {}).get("vectors") or {}
    if int(vectors.get("size") or 0) != settings.dimensions:
        raise SemanticUnavailable(
            f"collection {settings.qdrant_collection} has {vectors.get('size')} dimensions but the "
            f"configured embedding model produces {settings.dimensions}; point the settings at a "
            "matching collection instead of changing this one")
    for field in ("user_id", "conversation_id"):
        qdrant_request(settings, "PUT", f"{collection}/index?wait=true",
                       {"field_name": field, "field_schema": "keyword"}, timeout=30)
    return {"collection": settings.qdrant_collection, "dimensions": settings.dimensions,
            "points": int(info.get("points_count") or 0)}


def config_fingerprint(settings: SemanticSettings) -> str:
    """Identity of the index configuration, without any secret.

    Changing the endpoint, collection or model invalidates the derived index;
    enabling it, adding a key or editing the priority list does not.
    """
    material = "\\0".join([settings.qdrant_url, settings.qdrant_collection,
                           settings.embedding_url, settings.embedding_model,
                           str(settings.dimensions)])
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def reset_own_derived_state(store: Any, uid: str) -> dict[str, int]:
    """Drop only this owner's derived index bookkeeping.

    The retained messages, the source history and any collection itself are left
    untouched: only the rows that claim what this owner has indexed are removed,
    so a configuration switch starts honestly instead of reporting a stale
    "complete" index that lives in a different collection.
    """
    db = store._connection
    removed = db.execute("DELETE FROM search_vector_state WHERE user_id=?", (uid,)).rowcount
    db.execute("DELETE FROM search_vector_queue WHERE message_rowid IN"
               " (SELECT rowid FROM messages WHERE user_id=?)", (uid,))
    db.execute("DELETE FROM search_vector_meta WHERE name LIKE ?", (f"%:{uid}",))
    db.commit()
    return {"removed": max(0, int(removed))}


def ensure_fingerprint(store: Any, uid: str, settings: SemanticSettings) -> dict[str, Any]:
    """Keep the owner's derived state in step with the current index configuration."""
    current = config_fingerprint(settings)
    stored = meta(store, f"fingerprint:{uid}", "")
    if stored == current:
        return {"changed": False}
    reset = reset_own_derived_state(store, uid) if stored else {"removed": 0}
    set_meta(store, f"fingerprint:{uid}", current)
    return {"changed": True, **reset}


def probe(settings: SemanticSettings) -> dict[str, Any]:
    """Cheap liveness for the owner status surface. Never raises."""
    if not settings.enabled or not settings.configured():
        return {"available": False, "reason": "not_configured"}
    try:
        status, result = qdrant_request(settings, "GET",
                                        f"/collections/{settings.qdrant_collection}", timeout=10)
    except SemanticUnavailable as error:
        return {"available": False, "reason": error.reason}
    if status == 404:
        return {"available": False, "reason": "collection_missing"}
    info = result.get("result") or {}
    vectors = (info.get("config") or {}).get("params", {}).get("vectors") or {}
    if int(vectors.get("size") or 0) != settings.dimensions:
        return {"available": False, "reason": "dimension_mismatch"}
    try:
        embed(settings, ["проверка индекса"])
    except SemanticUnavailable as error:
        return {"available": False, "reason": error.reason}
    return {"available": True, "reason": "", "dimensions": settings.dimensions,
            "points": int(info.get("points_count") or 0)}