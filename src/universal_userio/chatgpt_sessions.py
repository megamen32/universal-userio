"""Per-account ChatGPT session store: browser extensions export their
``__Secure-next-auth.session-token`` cookie here; the headless web adapter
exchanges it for an access token and drives backend-api.

One JSON file per account slug under /var/lib/universal-userio/chatgpt-sessions/.
Deliberately no database; credentials never appear in operator-facing responses.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

SESSION_DIR = Path(os.environ.get(
    "USERIO_CHATGPT_SESSION_DIR", "/var/lib/universal-userio/chatgpt-sessions"))
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

_LOCK = threading.Lock()
_USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _slugify(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", str(raw or "").strip().lower()).strip("-.")
    if not slug:
        raise ValueError("could not derive an account slug; pass email or account_ref")
    return slug[:64]


def _user_dir(user: str) -> Path:
    user = str(user or "").strip()
    if not _USER_RE.match(user):
        raise ValueError("invalid UserIO username for session namespace")
    return SESSION_DIR / user.lower()


def _path(slug: str, *, user: str) -> Path:
    if not _SLUG_RE.match(slug):
        raise ValueError("account slug must match [a-z0-9][a-z0-9._-]{0,63}")
    return _user_dir(user) / f"{slug}.json"


def _cookie_chunks(payload: dict) -> list[dict]:
    """Normalize the payload into an ordered list of session cookie chunks.

    ChatGPT splits long JWTs into ``__Secure-next-auth.session-token`` plus
    ``.0/.1/...`` chunks; every chunk must be set as its own cookie.
    """
    chunks = payload.get("cookies")
    if not (isinstance(chunks, list) and chunks):
        token = str(payload.get("session_token") or "").strip()
        chunks = [{"name": "__Secure-next-auth.session-token", "value": token}] if token else []
    out = [
        {"name": str(c.get("name") or "").strip(), "value": str(c.get("value") or "")}
        for c in chunks if isinstance(c, dict)
    ]
    out = [c for c in out if c["name"] and c["value"]]
    if not out:
        raise ValueError("session_token or cookies required")
    if not any(c["name"].startswith("__Secure-next-auth.session-token") for c in out):
        raise ValueError("no session-token cookie in payload")
    return out


def save_session(payload: dict, *, user: str) -> dict:
    """Store one account's session cookies; returns public metadata only."""
    slug = _slugify(str(payload.get("account_ref") or payload.get("email") or payload.get("profile_hint") or payload.get("agent_id") or ""))
    record = {
        "slug": slug,
        "cookie_chunks": _cookie_chunks(payload),
        "user_agent": str(payload.get("user_agent") or ""),
        "email": str(payload.get("email") or ""),
        "name": str(payload.get("name") or ""),
        "agent": str(payload.get("agent") or ""),
        "agent_id": str(payload.get("agent_id") or ""),
        "stored_by": user,
        "updated_at": time.time(),
    }
    directory = _user_dir(user)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    tmp = _path(slug, user=user).with_suffix(".json.tmp")
    with _LOCK:
        tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, _path(slug, user=user))
        os.chmod(_path(slug, user=user), 0o600)
    return describe(record)


def load_session(slug: str, *, user: str) -> dict:
    """Return the full record including the session token."""
    try:
        return json.loads(_path(str(slug).strip(), user=user).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise KeyError(f"chatgpt session not found: {slug}") from None
    except json.JSONDecodeError as error:
        raise RuntimeError(f"chatgpt session file is corrupt: {slug}") from error


def list_sessions(*, user: str) -> list[dict]:
    """Public metadata for every stored account, oldest first."""
    directory = _user_dir(user)
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(record, dict) and record.get("cookie_chunks"):
            out.append(describe(record))
    return out


def delete_session(slug: str, *, user: str) -> dict:
    path = _path(str(slug).strip(), user=user)
    if not path.exists():
        raise KeyError(f"chatgpt session not found: {slug}")
    path.unlink()
    return {"deleted": path.stem}


def describe(record: dict) -> dict:
    return {
        "slug": record.get("slug", ""),
        "email": record.get("email", ""),
        "name": record.get("name", ""),
        "agent": record.get("agent", ""),
        "agent_id": record.get("agent_id", ""),
        "stored_by": record.get("stored_by", ""),
        "updated_at": record.get("updated_at", 0),
    }
