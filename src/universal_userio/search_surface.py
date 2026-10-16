"""Search mode for the existing ``userio.channels.list`` tool.

One tool, two shapes: without ``search`` it lists chats exactly as before; with
``search`` it searches the same owner-scoped history and returns one entry per
matching message. The permission boundary is the same one the HTTP search uses:
the caller must hold ``read``, the optional ``conversation_ids`` allow-list is
applied inside the query, and every candidate is re-read from the live message
row under the current owner authorization before it is emitted.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence


class SearchArgumentsInvalid(ValueError):
    """Caller supplied search arguments outside the bounded contract."""


def normalize_search_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the bounded search contract shared by MCP and HTTP."""
    scope = str(arguments.get("scope") or "conversation").strip()
    if scope not in {"conversation", "message"}:
        raise SearchArgumentsInvalid("invalid scope")
    mode = str(arguments.get("mode") or ("hybrid" if scope == "message" else "text")).strip()
    if mode not in {"text", "semantic", "hybrid"}:
        raise SearchArgumentsInvalid("invalid mode")
    match_type = str(arguments.get("match_type") or "all").strip()
    if match_type not in {"all", "exact", "fuzzy"}:
        raise SearchArgumentsInvalid("invalid match_type")
    raw = arguments.get("conversation_ids") or []
    if isinstance(raw, str):
        raw = [item for item in raw.split(",") if item]
    if not isinstance(raw, (list, tuple)):
        raise SearchArgumentsInvalid("conversation_ids must be a list")
    ids: list[str] = []
    for item in raw:
        value = str(item).strip()
        if not value:
            continue
        if len(value) > 128 or not all(part.isalnum() or part in "_:-." for part in value):
            raise SearchArgumentsInvalid("invalid conversation id")
        ids.append(value)
    if len(ids) > 100:
        raise SearchArgumentsInvalid("at most 100 conversation ids are allowed")
    account_ref = str(arguments.get("account_ref") or "").strip()
    if len(account_ref) > 128:
        raise SearchArgumentsInvalid("invalid account_ref")
    try:
        offset = max(0, int(arguments.get("offset") or 0))
    except (TypeError, ValueError):
        raise SearchArgumentsInvalid("invalid offset") from None
    try:
        limit = int(arguments.get("limit") or (100 if scope == "conversation" else 50))
    except (TypeError, ValueError):
        raise SearchArgumentsInvalid("invalid limit") from None
    limit = max(1, min(limit, 100 if scope == "conversation" else 200))
    return {"scope": scope, "mode": mode, "match_type": match_type, "conversation_ids": ids,
            "account_ref": account_ref, "offset": offset, "limit": limit}


def search_response(store: Any, query: str, arguments: Mapping[str, Any], *, user_id: str | None,
                    source: str | None = None, ignored: Sequence[str] = ()) -> dict[str, Any]:
    """Run the shared core and return the agent-facing envelope."""
    from .search import search_conversations, search_coverage_payload, search_messages
    from .semantic_index import resolve_settings

    options = normalize_search_arguments(arguments)
    text = str(query or "").strip()
    if options["scope"] == "message":
        found = search_messages(
            store, text, source=source, account_ref=options["account_ref"] or None,
            limit=options["limit"], user_id=user_id, match_type=options["match_type"],
            offset=options["offset"], conversation_ids=options["conversation_ids"],
            mode=options["mode"],
        )
        results = [row for row in found["results"] if row["conversation_id"] not in set(ignored)]
        return {"ok": True, "scope": "message", "results": results,
                "coverage": found["coverage"], "semantic": found["semantic"],
                "mode_used": found["mode_used"], "offset": options["offset"],
                "limit": options["limit"], "next_offset": options["offset"] + len(results)}
    rows = search_conversations(
        store, text, source=source, account_ref=options["account_ref"] or None,
        limit=options["limit"], user_id=user_id, match_type=options["match_type"],
        offset=options["offset"],
    )
    allowed = set(options["conversation_ids"])
    blocked = set(ignored)
    results = [row for row in rows
               if row["id"] not in blocked and (not allowed or str(row["id"]) in allowed)]
    settings = resolve_settings(store, user_id=user_id)
    availability = {"available": bool(settings.enabled and settings.configured()),
                    "enabled": settings.enabled,
                    "model": settings.embedding_model if settings.enabled else "",
                    "collection": settings.qdrant_collection if settings.enabled else "",
                    "dimensions": settings.dimensions if settings.enabled else 0}
    coverage = search_coverage_payload(store, user_id=user_id,
                                       conversation_ids=options["conversation_ids"])
    return {"ok": True, "scope": "conversation", "results": results,
            "coverage": coverage, "semantic": availability,
            "mode_used": "text", "offset": options["offset"], "limit": options["limit"],
            "next_offset": options["offset"] + len(results)}