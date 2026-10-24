"""Search surface shared by the web UI, HTTP and the MCP tools.

One core, three shapes. Without ``search`` the list tool lists chats exactly as
before; with ``search`` it searches the same owner-scoped history and returns one
entry per matching message. The dedicated ``userio.channels.search`` tool adds
the two identifier lookups an agent needs and cannot get from a text index:
an exact conversation id and an exact message id.

The permission boundary is the same one the HTTP search uses: the caller must
hold ``read``, the optional ``conversation_ids`` allow-list is applied inside
the query, and every candidate is re-read from the live message row under the
current owner authorization before it is emitted.
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
    conversation_id = str(arguments.get("conversation_id") or "").strip()
    if len(conversation_id) > 128:
        raise SearchArgumentsInvalid("invalid conversation_id")
    message_id = str(arguments.get("message_id") or "").strip()
    if len(message_id) > 256:
        raise SearchArgumentsInvalid("invalid message_id")
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
            "conversation_id": conversation_id, "message_id": message_id,
            "account_ref": account_ref, "offset": offset, "limit": limit}


def _semantic_availability(store: Any, *, user_id: str | None) -> dict[str, Any]:
    from .semantic_index import resolve_settings

    settings = resolve_settings(store, user_id=user_id)
    return {"available": bool(settings.enabled and settings.configured()),
            "enabled": settings.enabled,
            "model": settings.embedding_model if settings.enabled else "",
            "collection": settings.qdrant_collection if settings.enabled else "",
            "dimensions": settings.dimensions if settings.enabled else 0}


def _envelope(scope: str, results: list[dict[str, Any]], options: Mapping[str, Any],
              semantic: Mapping[str, Any], coverage: Mapping[str, Any], *,
              mode_used: str) -> dict[str, Any]:
    return {"ok": True, "scope": scope, "results": results,
            "coverage": dict(coverage), "semantic": dict(semantic),
            "mode_used": mode_used, "offset": options["offset"],
            "limit": options["limit"], "next_offset": options["offset"] + len(results)}


def search_tool_response(store: Any, arguments: Mapping[str, Any], *, user_id: str | None,
                         source: str | None = None, ignored: Sequence[str] = (),
                         query: str | None = None) -> dict[str, Any]:
    options = normalize_search_arguments(arguments)
    if options['scope'] == 'conversation':
        # This retained-only path also reads settings and coverage outside
        # search_conversations. Serialize the whole SQLite projection so a
        # parallel ingress/search cannot reuse its connection statements.
        # Message semantic lookup may use network IO and stays outside here.
        with store._lock:
            return _search_tool_response(store, arguments, user_id=user_id,
                                         source=source, ignored=ignored, query=query)
    return _search_tool_response(store, arguments, user_id=user_id,
                                 source=source, ignored=ignored, query=query)


def _search_tool_response(store: Any, arguments: Mapping[str, Any], *, user_id: str | None,
                          source: str | None = None, ignored: Sequence[str] = (),
                          query: str | None = None) -> dict[str, Any]:
    """Run the shared core and return the agent-facing envelope.

    Three ways in, one shape out: free text, an exact conversation id, or an
    exact message id. An identifier is resolved against the live store, so a
    chat that the text index never reached is still found by its id.
    """
    from .search import (lookup_conversation, lookup_message, search_conversations,
                         search_coverage_payload, search_messages)

    options = normalize_search_arguments(arguments)
    text = str(query if query is not None else (arguments.get("query") or arguments.get("search") or "")).strip()
    allowed = set(options["conversation_ids"])
    blocked = set(ignored)
    semantic = _semantic_availability(store, user_id=user_id)

    if options["conversation_id"]:
        row = lookup_conversation(
            store, options["conversation_id"], user_id=user_id, source=source,
            account_ref=options["account_ref"] or None)
        keep = row is not None and row["id"] not in blocked and (not allowed or row["id"] in allowed)
        coverage = search_coverage_payload(store, user_id=user_id,
                                           conversation_ids=options["conversation_ids"])
        return _envelope("conversation", [row] if keep else [], options, semantic, coverage,
                         mode_used="exact_id")

    if options["message_id"]:
        row = lookup_message(store, options["message_id"], source=source, user_id=user_id,
                             account_ref=options["account_ref"] or None)
        if row is not None:
            conversation_id = str(row.get("conversation_id") or "")
            if conversation_id in blocked or (allowed and conversation_id not in allowed):
                row = None
        coverage = search_coverage_payload(store, user_id=user_id,
                                           conversation_ids=options["conversation_ids"])
        return _envelope("message", [row] if row else [], options, semantic, coverage,
                         mode_used="exact_id")

    if not text:
        raise SearchArgumentsInvalid("search requires query, conversation_id or message_id")

    if options["scope"] == "message":
        found = search_messages(
            store, text, source=source, account_ref=options["account_ref"] or None,
            limit=options["limit"], user_id=user_id, match_type=options["match_type"],
            offset=options["offset"], conversation_ids=options["conversation_ids"],
            mode=options["mode"],
        )
        results = [row for row in found["results"] if row["conversation_id"] not in blocked]
        return _envelope("message", results, options, found["semantic"], found["coverage"],
                         mode_used=found["mode_used"])

    rows = search_conversations(
        store, text, source=source, account_ref=options["account_ref"] or None,
        limit=options["limit"], user_id=user_id, match_type=options["match_type"],
        offset=options["offset"],
    )
    results = [row for row in rows
               if row["id"] not in blocked and (not allowed or str(row["id"]) in allowed)]
    coverage = search_coverage_payload(store, user_id=user_id,
                                       conversation_ids=options["conversation_ids"])
    return _envelope("conversation", results, options, semantic, coverage, mode_used="text")


def search_response(store: Any, query: str, arguments: Mapping[str, Any], *, user_id: str | None,
                    source: str | None = None, ignored: Sequence[str] = ()) -> dict[str, Any]:
    """Search mode for the existing ``userio.channels.list`` tool."""
    return search_tool_response(store, arguments, user_id=user_id, source=source,
                                 ignored=ignored, query=query)
