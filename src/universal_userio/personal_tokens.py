"""User-owned, named Bearer tokens using the existing token authenticator."""

import secrets
import time
import uuid

from .store import SQLiteUserIOStore


class PersonalTokens:
    def __init__(self, store: SQLiteUserIOStore) -> None:
        self.store = store
        with store._lock, store._connection:
            columns = {row["name"] for row in store._connection.execute("PRAGMA table_info(api_tokens)")}
            if "name" not in columns:
                store._connection.execute("ALTER TABLE api_tokens ADD COLUMN name TEXT NOT NULL DEFAULT ''")

    def list(self, user_id: str) -> list[dict]:
        with self.store._lock:
            rows = self.store._connection.execute(
                "SELECT id,name,created_at,revoked_at,expires_at,COALESCE(scope,'full_access') AS scope FROM api_tokens "
                "WHERE user_id=? AND kind='personal' AND name<>'' ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create(self, user_id: str, name: str, *, scope: str = "full_access") -> dict:
        if not isinstance(scope, str) or scope not in {"full_access", "read_only"}:
            raise ValueError("scope must be full_access or read_only")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError("Название токена должно содержать от 1 до 80 символов")
        token_id, value, created = "token_" + uuid.uuid4().hex, "uio_" + secrets.token_urlsafe(32), time.time()
        with self.store._lock, self.store._connection:
            self.store._connection.execute(
                "INSERT INTO api_tokens (id,user_id,token_hash,created_at,kind,token_type,name,scope) "
                "VALUES (?,?,?,?,'personal','access',?,?)",
                (token_id, user_id, self.store._token_hash(value), created, name.strip(), scope),
            )
        return {"id": token_id, "name": name.strip(), "created_at": created,
                "expires_at": None, "scope": scope, "token": value, "token_returned_once": True}

    def revoke(self, user_id: str, token_id: str) -> bool:
        with self.store._lock, self.store._connection:
            row = self.store._connection.execute(
                "UPDATE api_tokens SET revoked_at=? WHERE id=? AND user_id=? "
                "AND kind='personal' AND name<>'' AND revoked_at IS NULL",
                (time.time(), token_id, user_id),
            )
        return row.rowcount == 1
