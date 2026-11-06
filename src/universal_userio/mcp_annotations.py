"""Central deterministic MCP ``tools/list`` annotation policy.

Descriptor metadata only. This module is intentionally dependency-free so
both serializers (``mcp_catalog.ToolSpec`` and ``mcp_surface.ToolSpec``) can
share one policy without import cycles.

Deliberately separate from ``token_permissions.READ_ONLY_TOOLS``: that
frozenset is a credential authorization allowlist and mixes handler behaviors
(e.g. ``userio.contacts.resolve`` may trigger provider ``ImportContacts`` on
its phone branch, while ``userio.messenger.question.audit`` is a SELECT-only
handler that is deliberately absent from the credential allowlist). MCP
annotations describe handler behavior for clients and do not change
server-side authorization; clients may still use the hints to choose how
prominently to confirm an operation.

Classification source: direct handler review at the deployed revision
(question_coordination.py, profile_sharing.py, messenger handlers, dispatch
paths). Names not listed here keep conservative write metadata; for unreviewed
writes the emitted values are exactly the MCP specification defaults (an
unannotated tool is treated as a potential write), not a claim of proven
destructiveness.
"""

from __future__ import annotations

from typing import Any

# Handler-reviewed pure read operations: repeated calls neither import
# provider state nor run ASR/STT branches, and are not observed to send
# provider side-effect requests. Pure here means "not verified pure across
# all branches" does not apply: excluded read-like handlers (contacts.resolve,
# messenger.resolve, messenger.history, messenger.has_contacted,
# messenger.media, channels.download) carry such branches and therefore stay
# conservative. Reviewed reads may still lazily CREATE TABLE or cache status
# as implementation detail; that does not create or modify business records.
# Everything else keeps write metadata.
PROVEN_READ_TOOLS = frozenset({
    "search",
    "fetch",
    "userio.accounts.list",
    "userio.calls.matrix.get",
    "userio.calls.telegram.get",
    "userio.channels.list",
    "userio.channels.read",
    "userio.channels.search",
    "userio.conversation.get",
    "userio.diagnostic.get",
    "userio.inbox.list_new",
    "userio.meetings.telemost.get",
    "userio.messenger.capabilities",
    "userio.messenger.events",
    "userio.messenger.get",
    "userio.profile_share.audit",
    "userio.profile_share.list",
    "userio.messenger.question.audit",
    "userio.workspace.claim_log",
    "userio.workspace.exclusions.list",
    "userio.workspace.policy.chats.list",
    "userio.workspace.policy.evaluate",
    "userio.workspace.policy.get",
    "userio.workspace.poll",
    "userio.workspace.triage.get",
})


def tool_annotations(name: str) -> dict[str, Any]:
    """Return a fresh MCP annotations object for one registered tool name.

    Every registered descriptor receives explicit annotations so that a
    missing annotation can never make a proven read appear as a write to MCP
    clients. Read operations get bounded hints; all other names keep the
    specification's conservative defaults.
    """

    read_only = name in PROVEN_READ_TOOLS
    return {
        "readOnlyHint": read_only,
        "destructiveHint": not read_only,
        "idempotentHint": read_only,
        "openWorldHint": not read_only,
    }
