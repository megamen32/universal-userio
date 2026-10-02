"""Encrypted session vault: browser extensions store and fetch session blobs.

A "session" is whatever the client decided to back up (cookies, localStorage,
etc.), optionally encrypted client-side before upload. The server is a dumb
named-blob store: it never sees plaintext when the client used a passphrase,
and it never needs a database — one JSON file per session under
/var/lib/universal-userio/vault/.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

VAULT_DIR = Path(os.environ.get("USERIO_VAULT_DIR", "/var/lib/universal-userio/vault"))
MAX_SESSION_BYTES = 32_000_000
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")

_LOCK = threading.Lock()
_USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _user_dir(user: str) -> Path:
    user = str(user or "").strip()
    if not _USER_RE.match(user):
        raise ValueError("invalid UserIO username for vault namespace")
    return VAULT_DIR / user.lower()


def _session_path(name: str, *, user: str) -> Path:
    name = str(name or "").strip()
    if not _NAME_RE.match(name):
        raise ValueError("session name must match [a-zA-Z0-9][a-zA-Z0-9._-]{0,63}")
    return _user_dir(user) / f"{name}.json"


def save_session(name: str, payload: dict, *, user: str) -> dict:
    """Store one session blob under ``name`` and return its metadata."""
    if payload.get("blob") is None:
        raise ValueError("blob required")
    path = _session_path(name, user=user)
    directory = _user_dir(user)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    record = {
        "name": path.stem,
        "blob": payload.get("blob"),
        "enc": str(payload.get("enc") or "none"),
        "agent": str(payload.get("agent") or ""),
        "machine": str(payload.get("machine") or ""),
        "profile": str(payload.get("profile") or ""),
        "domains": list(payload.get("domains") or [])[:200],
        "cookie_count": int(payload.get("cookie_count") or 0),
        "stored_by": user,
        "stored_at": time.time(),
    }
    encoded = json.dumps(record, ensure_ascii=False)
    if len(encoded.encode()) > MAX_SESSION_BYTES:
        raise ValueError("session exceeds MAX_SESSION_BYTES")
    tmp = path.with_suffix(".json.tmp")
    with _LOCK:
        tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    return describe(record)


def load_session(name: str, *, user: str) -> dict:
    """Return the full stored session record including the blob."""
    path = _session_path(name, user=user)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise KeyError(f"session not found: {path.stem}") from None
    return record


def list_sessions(*, user: str) -> list[dict]:
    """List stored sessions, newest first, without blobs."""
    directory = _user_dir(user)
    if not directory.is_dir():
        return []
    sessions = []
    for path in directory.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(record, dict) or "stored_at" not in record:
            continue
        sessions.append(describe(record))
    sessions.sort(key=lambda s: s["stored_at"], reverse=True)
    return sessions


def delete_session(name: str, *, user: str) -> dict:
    path = _session_path(name, user=user)
    if not path.exists():
        raise KeyError(f"session not found: {path.stem}")
    path.unlink()
    return {"deleted": path.stem}


def describe(record: dict) -> dict:
    """Strip the blob out of a record for listing responses."""
    return {k: v for k, v in record.items() if k != "blob"}
