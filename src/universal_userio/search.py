"""Global message search for Universal UserIO.

Search is deliberately user-scoped and provider-agnostic. It scans message text,
conversation/contact names and sender identifiers, then returns one best hit per
conversation. This keeps the UI simple while covering old messages across all
connected accounts instead of only the latest chat preview.
"""
from __future__ import annotations

from typing import Any


def _tokens(query: str) -> list[str]:
    return [part for part in query.casefold().split() if len(part) >= 2]


def search_conversations(
    store: Any,
    query: str,
    *,
    source: str | None = None,
    limit: int = 100,
    user_id: str | None = None,
    scan_limit: int = 50000,
) -> list[dict[str, object]]:
    tokens = _tokens(query)
    if not tokens:
        return []
    uid = store._user(user_id)
    source_sql = ""
    values: list[object] = [uid]
    if source:
        source = source.strip().lower()
        if source in {"mail", "email", "gmail"}:
            source_sql = " AND (c.source IN ('mail','email','gmail') OR c.source LIKE 'gmail:%')"
        else:
            source_sql = " AND c.source=?"
            values.append(source)
    values.append(max(100, min(int(scan_limit), 100000)))
    with store._lock:
        rows = store._connection.execute(
            f"""
            SELECT m.conversation_id,m.source,m.message_id,m.sender AS message_sender,
                   m.body,m.received_at,m.seen_at,
                   c.sender,c.identity_id,c.account_ref,c.updated_at,
                   (SELECT name FROM contact_names
                    WHERE user_id=c.user_id AND source=c.source AND sender=c.sender) AS display_name,
                   (SELECT COUNT(*) FROM messages um
                    WHERE um.user_id=c.user_id AND um.conversation_id=c.id AND um.seen_at IS NULL) AS unread_count
            FROM messages m
            JOIN conversations c ON c.user_id=m.user_id AND c.id=m.conversation_id
            WHERE m.user_id=? {source_sql}
            ORDER BY m.received_at DESC
            LIMIT ?
            """,
            values,
        ).fetchall()

    found: dict[str, dict[str, object]] = {}
    for row in rows:
        cid = str(row["conversation_id"])
        if cid in found:
            continue
        haystack = " ".join(
            str(row[key] or "")
            for key in ("body", "sender", "message_sender", "display_name", "identity_id")
        ).casefold()
        if not all(token in haystack for token in tokens):
            continue
        body = str(row["body"] or "").strip()
        found[cid] = {
            "id": cid,
            "source": str(row["source"] or ""),
            "sender": str(row["sender"] or ""),
            "identity_id": row["identity_id"],
            "display_name": row["display_name"],
            "account_ref": row["account_ref"],
            "preview": body[:300],
            "unread_count": int(row["unread_count"] or 0),
            "last_at": float(row["received_at"] or 0),
            "match_message_id": str(row["message_id"] or ""),
            "match_body": body[:500],
        }
        if len(found) >= max(1, min(int(limit), 500)):
            break
    return list(found.values())
