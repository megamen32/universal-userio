"""Personal read-only credentials: explicit read operations, default deny."""

READ_ONLY_TOOLS = frozenset({
    "userio.accounts.list", "userio.channels.list", "userio.channels.read",
    "userio.channels.download", "userio.inbox.list_new", "userio.conversation.get",
    "userio.workspace.poll", "userio.workspace.claim_log",
    "userio.workspace.exclusions.list", "userio.workspace.policy.get",
    "userio.workspace.policy.chats.list", "userio.workspace.policy.evaluate",
    "userio.workspace.triage.get",
})


def read_only_http_allowed(method: str, path: str) -> bool:
    if path == "/mcp":
        return method in {"GET", "POST"}
    if method != "GET":
        return False
    if path in {"/v1/accounts", "/v1/conversations", "/v1/search", "/v1/runtime",
                "/v1/workspace/events", "/v1/inbox", "/v1/drafts/pending"}:
        return True
    parts = path.split("/")
    if len(parts) >= 4 and parts[:3] == ["", "v1", "conversations"] and parts[3]:
        return (len(parts) == 4 or
                len(parts) == 5 and parts[4] in {"phone-history", "secretary"} or
                len(parts) == 6 and parts[4] == "media" and bool(parts[5]) or
                len(parts) == 7 and parts[4] == "media" and bool(parts[5]) and parts[6] == "raw")
    return False
