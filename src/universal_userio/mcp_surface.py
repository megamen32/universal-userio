"""Authenticated MCP facade for user-scoped UserIO operations."""

from __future__ import annotations

import json
from typing import Any

from .contracts import UserPrincipal
from .mcp_catalog import TOOL_CAPABILITIES, TOOL_SPECS
from .mcp_dispatch import UserIOToolDispatcher
from .service import UserIOService
from .store import SQLiteUserIOStore


class UserIOMcpSurface:
    """Compose MCP discovery/resources with the transport-free tool dispatcher."""

    def __init__(
        self,
        store: SQLiteUserIOStore,
        service: UserIOService,
        principal: UserPrincipal | None = None,
    ) -> None:
        self._store = store
        self._service = service
        self._principal = store.owner() if principal is None else principal
        self._tools = UserIOToolDispatcher(store, service)

    def tool_manifest(
        self, *, principal: UserPrincipal | None = None
    ) -> dict[str, Any]:
        principal = self._principal if principal is None else principal
        specs = tuple(
            spec
            for spec in TOOL_SPECS
            if (required := TOOL_CAPABILITIES.get(spec.name)) is None
            or self._store.capability_enabled(
                required, user_id=principal.user_id
            )
        )
        return {"tools": [spec.as_dict() for spec in specs]}

    def resource_manifest(
        self, *, principal: UserPrincipal | None = None
    ) -> dict[str, Any]:
        principal = self._principal if principal is None else principal
        if not self._store.capability_enabled(
            "read", user_id=principal.user_id
        ):
            return {"resources": []}
        return {
            "resources": [
                {
                    "uri": "userio://accounts",
                    "name": "Connected accounts",
                    "mimeType": "application/json",
                },
                {
                    "uri": "userio://conversations",
                    "name": "Recent conversations",
                    "mimeType": "application/json",
                },
                {
                    "uri": "userio://inbox/unread",
                    "name": "Unread inbox",
                    "mimeType": "application/json",
                },
            ]
        }

    def resource_template_manifest(
        self, *, principal: UserPrincipal | None = None
    ) -> dict[str, Any]:
        principal = self._principal if principal is None else principal
        if not self._store.capability_enabled(
            "read", user_id=principal.user_id
        ):
            return {"resourceTemplates": []}
        return {
            "resourceTemplates": [
                {
                    "uriTemplate": "userio://conversations/{conversationId}",
                    "name": "Conversation by id",
                    "mimeType": "application/json",
                }
            ]
        }

    def read_resource(
        self, uri: str, *, principal: UserPrincipal | None = None
    ) -> dict[str, Any]:
        principal = self._principal if principal is None else principal
        user_id = principal.user_id
        if not self._store.capability_enabled("read", user_id=user_id):
            raise PermissionError("read capability disabled")
        if uri == "userio://accounts":
            payload = {"accounts": self._store.accounts(user_id=user_id)}
        elif uri == "userio://conversations":
            payload = {
                "conversations": self._store.conversations(
                    limit=100, user_id=user_id
                )
            }
        elif uri == "userio://inbox/unread":
            payload = {
                "messages": self._store.new_messages(limit=100, user_id=user_id)
            }
        elif uri.startswith("userio://conversations/"):
            conversation_id = uri.removeprefix(
                "userio://conversations/"
            ).strip()
            if not conversation_id or "/" in conversation_id:
                raise ValueError("invalid conversation resource uri")
            conversation = self._store.conversation(
                conversation_id, user_id=user_id
            )
            if conversation is None:
                raise KeyError("conversation not found")
            payload = {"conversation": conversation}
        else:
            raise KeyError("resource not found")
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(
                        payload, ensure_ascii=False, separators=(",", ":")
                    ),
                }
            ]
        }

    def dispatch(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        principal: UserPrincipal | None = None,
    ) -> dict[str, Any]:
        principal = self._principal if principal is None else principal
        if name == "tools/list":
            return self.tool_manifest(principal=principal)
        if name == "tools/call":
            tool_arguments = arguments.get("arguments", {})
            if not isinstance(tool_arguments, dict):
                return {"ok": False, "error": "arguments must be an object"}
            return self._tools.dispatch(
                str(arguments.get("name")),
                tool_arguments,
                principal=principal,
            )
        return self._tools.dispatch(name, arguments, principal=principal)
