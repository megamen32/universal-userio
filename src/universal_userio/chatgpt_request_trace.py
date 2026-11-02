"""Bounded, metadata-only ChatGPT call diagnostics; no URLs, bodies or credentials in logs."""
from __future__ import annotations

from collections import OrderedDict, deque
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import logging
import re
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

_LOG = logging.getLogger("universal_userio.chatgpt_request_trace")
_KINDS = {"upstream_http", "delegated", "dom_cache_read", "page_resource_observed"}
_INITIATORS = {"userio_web", "userio_cdp", "herder_browser", "page_unknown"}
_EXECUTORS = {"curl_cffi", "cdp_sidecar", "herder_cdp", "browser_page"}
_OPERATIONS = {"auth_session", "conversation_list", "conversation_read", "message_send",
               "list_chats", "export_chat", "send_message", "other"}
_ENDPOINTS = {"/api/auth/session", "/backend-api/conversations",
              "/backend-api/conversation", "/backend-api/conversation/{id}",
              "/backend-api/conversations/{id}", "cdp_sidecar", "other"}


def classify_endpoint(url: str, *, method: str = "GET") -> tuple[str, str]:
    """Only allowlisted route *templates* survive, never opaque IDs or query strings."""
    try:
        path = urlsplit(url).path
    except (TypeError, ValueError):
        return "other", "other"
    if path == "/api/auth/session":
        return "auth_session", "/api/auth/session"
    if path == "/backend-api/conversations":
        return "conversation_list", "/backend-api/conversations"
    if path == "/backend-api/conversation":
        return ("message_send" if method.upper() == "POST" else "other",
                "/backend-api/conversation")
    if re.fullmatch(r"/backend-api/conversation/[^/]+", path):
        return "conversation_read", "/backend-api/conversation/{id}"
    if re.fullmatch(r"/backend-api/conversations/[^/]+", path):
        return "conversation_read", "/backend-api/conversations/{id}"
    return "other", "other"


def retry_after_seconds(value: object, *, at: float | None = None) -> int | None:
    """Parse only time, never retain the original possibly sensitive header."""
    if not isinstance(value, str) or len(value) > 100:
        return None
    v = value.strip()
    if re.fullmatch(r"\d{1,8}", v):
        return min(int(v), 86400)
    try:
        date = parsedate_to_datetime(v)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0, min(86400, int(date.timestamp() - (time.time() if at is None else at))))
    except (TypeError, ValueError, OverflowError):
        return None


class ChatGPTRequestTrace:
    RETENTION_SECONDS = 3600
    MAX_EVENTS = 512
    MAX_MINUTES = 60
    MAX_KEYS_PER_MINUTE = 128
    MAX_LOG_EVENTS_PER_MINUTE = 120

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: deque[dict] = deque(maxlen=self.MAX_EVENTS)
        self._minutes: OrderedDict[int, dict[tuple, dict]] = OrderedDict()

    def record(self, *, initiator: str, executor: str, kind: str, operation: str,
               endpoint: str, status: int | None = None, latency_ms: float = 0,
               retry_attempt: int = 0, backoff_ms: int = 0,
               retry_after_s: int | None = None, cache_hit: bool = False,
               request_id: str | None = None, timestamp: float | None = None) -> dict:
        now = time.time() if timestamp is None else timestamp
        # Never accept caller-provided free-form strings into logs.
        operation = operation if operation in _OPERATIONS else "other"
        endpoint = endpoint if endpoint in _ENDPOINTS else "other"
        initiator = initiator if initiator in _INITIATORS else "page_unknown"
        executor = executor if executor in _EXECUTORS else "browser_page"
        kind = kind if kind in _KINDS else "page_resource_observed"
        status = status if type(status) is int and 100 <= status <= 599 else None
        latency = max(0, min(3_600_000, round(float(latency_ms))))
        retry_attempt = max(0, min(99, int(retry_attempt)))
        backoff = max(0, min(86_400_000, int(backoff_ms)))
        retry_after = (max(0, min(86400, retry_after_s))
                       if type(retry_after_s) is int else None)
        # Request identity is generated here, not derived from account/chat IDs.
        rid = request_id if isinstance(request_id, str) and re.fullmatch(r"[a-f0-9]{16}", request_id) else uuid4().hex[:16]
        event = {
            "schema": "chatgpt_req_v1",
            "utc": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "rid": rid, "initiator": initiator, "executor": executor,
            "kind": kind, "operation": operation, "endpoint": endpoint,
            "http_status": status, "latency_ms": latency,
            "retry_attempt": retry_attempt, "backoff_ms": backoff,
            "retry_after_s": retry_after, "cache_hit": bool(cache_hit),
            "upstream_count": 1 if kind == "upstream_http" else 0,
        }
        minute = int(now // 60)
        key = (kind, operation, endpoint, status)
        completed_minute = None
        with self._lock:
            # Emit the previous minute only when existing traffic naturally arrives.
            if self._minutes and minute > next(reversed(self._minutes)):
                previous = next(reversed(self._minutes))
                completed_minute = {
                    "minute_utc": datetime.fromtimestamp(previous * 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "events": sum(v["events"] for v in self._minutes[previous].values()),
                    "upstream_http": sum(v["upstream_http"] for v in self._minutes[previous].values()),
                    "http_429": sum(v["http_429"] for v in self._minutes[previous].values()),
                    "cache_hits": sum(v["cache_hits"] for v in self._minutes[previous].values()),
                }
            while self._events and self._events[0]["_when"] < now - self.RETENTION_SECONDS:
                self._events.popleft()
            self._events.append({"_when": now, **event})
            for old in list(self._minutes):
                if old < minute - self.MAX_MINUTES + 1 or old > minute:
                    del self._minutes[old]
            bucket = self._minutes.setdefault(minute, {})
            if key not in bucket and len(bucket) >= self.MAX_KEYS_PER_MINUTE:
                key = ("overflow", "other", "other", None)
            stats = bucket.setdefault(key, {"events": 0, "upstream_http": 0, "http_429": 0,
                                            "latency_ms_sum": 0, "cache_hits": 0})
            stats["events"] += 1
            stats["upstream_http"] += event["upstream_count"]
            stats["http_429"] += int(status == 429 and kind == "upstream_http")
            stats["latency_ms_sum"] += latency
            stats["cache_hits"] += int(bool(cache_hit))
            emit_event = sum(x["events"] for x in bucket.values()) <= self.MAX_LOG_EVENTS_PER_MINUTE
        # Existing application logger/journald. Emit only explicitly assembled primitives.
        # Default Python logging sends WARNING to stderr even when the host has not
        # configured INFO handlers (the UserIO core uses stdlib HTTPServer).
        if completed_minute is not None:
            _LOG.warning("chatgpt_minute %s", json.dumps(completed_minute, separators=(",", ":")))
        if emit_event:
            _LOG.warning("chatgpt_request %s", json.dumps(event, ensure_ascii=True, separators=(",", ":")))
        return event

    def minute_snapshot(self, *, timestamp: float | None = None) -> list[dict]:
        now = time.time() if timestamp is None else timestamp
        lower = int(now // 60) - self.MAX_MINUTES + 1
        with self._lock:
            return [
                {"minute_utc": datetime.fromtimestamp(minute * 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "kind": key[0], "operation": key[1], "endpoint": key[2],
                 "http_status": key[3], **stats}
                for minute, bucket in self._minutes.items() if lower <= minute <= int(now // 60)
                for key, stats in bucket.items()
            ]

    def retained_event_count(self, *, timestamp: float | None = None) -> int:
        now = time.time() if timestamp is None else timestamp
        with self._lock:
            return sum(e["_when"] >= now - self.RETENTION_SECONDS for e in self._events)


CHATGPT_REQUEST_TRACE = ChatGPTRequestTrace()
