"""User-owned, named Bearer tokens using the existing token authenticator."""

import secrets
import json
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
                "SELECT id,name,created_at,revoked_at,expires_at,COALESCE(scope,'full_access') AS scope,account_refs FROM api_tokens "
                "WHERE user_id=? AND kind='personal' AND name<>'' ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [dict(row) | {'grants': json.loads(row['account_refs'] or '[]') if row['scope']=='scoped_messenger' else None,
            'account_refs':json.loads(row['account_refs'] or '[]') if row['scope'] in {'messenger_actions','telegram_admin'} else None} for row in rows]

    def create(self, user_id: str, name: str, *, scope: str = "full_access", account_refs=None, grants=None) -> dict:
        if not isinstance(scope, str) or scope not in {"full_access", "read_only", "messenger_actions", "meeting_recording", "scoped_messenger"}:
            raise ValueError("unsupported token scope")
        if scope == 'scoped_messenger':
            from .scoped_permissions import validate_grants
            if account_refs is not None:
                raise ValueError('Use grants for scoped_messenger')
            account_refs = validate_grants(self.store, user_id, grants)
        elif grants is not None:
            raise ValueError('grants requires scoped_messenger')
        if scope == 'messenger_actions':
            if (not isinstance(account_refs,list) or not 1<=len(account_refs)<=16
                    or any(not isinstance(ref,str) for ref in account_refs) or len(set(account_refs))!=len(account_refs)):
                raise ValueError('explicit account_refs required for messenger_actions')
            owned={a['id']:a for a in self.store.accounts(user_id=user_id)}
            if any(ref not in owned or owned[ref]['provider'] not in {'telegram','max','gmail','vk'}
                   or not owned[ref]['enabled'] or 'reply' not in owned[ref]['capabilities'] for ref in account_refs):
                raise ValueError('messenger sender accounts must be owned, enabled and reply-capable')
        elif account_refs is not None and scope != 'scoped_messenger':
            raise ValueError('account_refs is supported only for messenger_actions')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError("Название токена должно содержать от 1 до 80 символов")
        token_id, value, created = "token_" + uuid.uuid4().hex, "uio_" + secrets.token_urlsafe(32), time.time()
        with self.store._lock, self.store._connection:
            self.store._connection.execute(
                "INSERT INTO api_tokens (id,user_id,token_hash,created_at,kind,token_type,name,scope,account_refs) "
                "VALUES (?,?,?,?,'personal','access',?,?,?)",
                (token_id, user_id, self.store._token_hash(value), created, name.strip(), scope,
                 json.dumps(account_refs) if account_refs is not None else None),
            )
        return {"id": token_id, "name": name.strip(), "created_at": created,
                "expires_at": None, "scope": scope, "token": value, "token_returned_once": True,
                **({'account_refs':account_refs} if account_refs is not None else {})}

    def update_permissions(self, user_id: str, token_id: str, grants) -> dict:
        from .scoped_permissions import validate_grants
        grants = validate_grants(self.store,user_id,grants)
        with self.store._lock, self.store._connection:
            result = self.store._connection.execute("""UPDATE api_tokens SET scope='scoped_messenger',account_refs=?
                WHERE user_id=? AND id=? AND kind='personal' AND name<>'' AND revoked_at IS NULL""",
                (json.dumps(grants),user_id,token_id))
            if result.rowcount != 1:
                raise KeyError('token_not_found')
        return {'id':token_id,'scope':'scoped_messenger','grants':grants,'updated':True}

    def revoke(self, user_id: str, token_id: str) -> bool:
        with self.store._lock, self.store._connection:
            row = self.store._connection.execute(
                "UPDATE api_tokens SET revoked_at=? WHERE id=? AND user_id=? "
                "AND kind='personal' AND name<>'' AND revoked_at IS NULL",
                (time.time(), token_id, user_id),
            )
        return row.rowcount == 1
